"""手动模式（`runtime_json.mode="manual"`）：不跑槽位、不起子进程。

一个槽位都不执行：不复制工程源码、不生成 flow.py、不探测签名，plan 只提供拓扑与分支，
文案（title / detail / fields）从发布版本的 doc 读。推进完全由平台完成——每到一个节点
建一条 token 与一条 run（`runs.source="manual"`），停下等人提交待办，再按选择走 default
或 yes/no。终态收敛、回退、终止与自动模式共用同一套记录与语义。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app import models
from app.db import new_id, now_iso
from app.services.flow_format import expand
from app.services.scheduler import _dumps, _loads

logger = logging.getLogger(__name__)

UNNAMED = "未命名排查"
FIELD_KEYS = ("observation", "acquisition", "assessment")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


class ManualFlow:
    """平台侧的推进器；持久化与会话由 GeneratedRunner（owner）提供。"""

    def __init__(self, owner) -> None:
        self.owner = owner

    # ---------------------------------------------------------------- 对外入口

    def start(self, db, instance) -> None:
        plan = self._release_plan(db, instance)
        if not plan.get("steps"):
            self.owner._fail(db, instance, "plan/step-missing", "发布版本缺少步骤表")
            db.commit()
            return
        # 手动模式的拓扑在启动时固定下来：回退、切换都用同一份，不再读 doc
        instance.plan_json = _dumps(plan)
        if not self.owner._tokens(db, instance.id):
            self.owner._ensure_entry_token(db, instance, plan)
        db.commit()
        self.advance(db, instance)

    def submit(self, db, instance, review, decision) -> None:
        token = db.get(models.Token, review.token_id)
        plan = self._plan(db, instance)
        step = plan["steps"].get(token.node_id if token else None) or {}
        if token is None or token.status != "waiting":
            raise ValueError("该次到访已不在等待人工输入")
        nodes = self._nodes(db, instance)
        phase = _loads(review.request_json).get("type")
        result = decision.get("result")
        targets: list[str]
        branch = "default"

        if phase == "category":
            options = list(step.get("next", {}).get("default") or [])
            choice = decision.get("target")
            if choice not in options:
                raise ValueError("请选择一个后续排查节点")
            output = {"choice": choice}
            targets = [choice]
            evidence = self._evidence(nodes.get(token.node_id), options=options, nodes=nodes)
        elif phase == "decision":
            if result not in ("yes", "no"):
                raise ValueError("请选择是或否")
            reason = decision.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("请填写判断依据")
            output = {"result": result, "reason": reason.strip()}
            branch = result
            targets = list(step.get("next", {}).get(result) or [])
            evidence = {**self._evidence(nodes.get(token.node_id)), "result": result,
                        "reason": reason.strip()}
        else:
            if result not in ("done", "continue"):
                raise ValueError("请确认继续")
            output = {"result": "done"}
            targets = list(step.get("next", {}).get("default") or [])
            evidence = self._evidence(nodes.get(token.node_id))

        review.status, review.decision_json = "done", _dumps(decision)
        review.decided_at, review.decided_by = now_iso(), "local"
        token.status, token.updated_at = "done", now_iso()
        instance.status = "running"
        self.owner._write_run(
            db, instance, token,
            status="ok" if targets else "failed",
            error=None if targets else "route/no-successor: 当前分支没有后继",
            output=output,
            evidence={**evidence, "branch": branch},
            source="manual",
        )
        if not targets:
            token.status = "failed"
            logger.info("实例 %s 节点 %s 没有 %s 分支后继", instance.id, token.node_id, branch)
            db.commit()
            self.advance(db, instance)
            return
        self._children(db, instance, plan, token, targets, branch)
        db.commit()
        self.advance(db, instance)

    def rollback(self, db, instance, target, branch, reason, *, target_token_id=None) -> None:
        tokens = self.owner._tokens(db, instance.id)
        candidates = [item for item in tokens if item.id == target_token_id] if target_token_id else [
            item for item in tokens if item.node_id == target and item.visited_at
        ]
        selected = candidates[-1] if candidates else None
        if selected is None or selected.status != "done":
            raise ValueError("未找到该次已完成的人工到访")
        plan = self._plan(db, instance)
        step = plan["steps"].get(selected.node_id) or {}
        if step.get("terminal") or step.get("kind") == "start":
            raise ValueError("开始和终止节点不能作为回退目标")
        kind = step.get("kind")
        if kind == "category":
            # 分类节点的「新选后继」就是它当时选的那条，回退后照旧走
            chosen = (_loads(self._last_output(db, selected)) or {}).get("choice")
            targets = [chosen] if chosen in (step.get("next", {}).get("default") or []) else []
            branch = "default"
        else:
            branch = branch if kind == "investigation" else "default"
            targets = list(step.get("next", {}).get(branch) or [])
        if not targets:
            raise ValueError("所选分支没有后继")

        retained, cursor = set(), selected
        by_id = {item.id: item for item in tokens}
        while cursor is not None and cursor.id not in retained:
            retained.add(cursor.id)
            cursor = by_id.get(cursor.parent_token_id)
        for token in tokens:
            if token.id not in retained and token.status != "failed":
                token.status, token.updated_at = "abandoned", now_iso()
        for review in db.scalars(select(models.Review).where(
                models.Review.instance_id == instance.id, models.Review.status == "pending")):
            review.status, review.decided_at = "cancelled", now_iso()
        generation = max(item.generation for item in tokens) + 1
        self.owner._write_run(
            db, instance, selected, status="rollback",
            args={"reason": reason or "", "branch": branch},
            evidence={"target": selected.node_id, "targetTokenId": selected.id,
                      "successors": targets, "generation": generation},
            node_id=selected.node_id,
            generation=generation,
            source="manual",
        )
        self._children(db, instance, plan, selected, targets, branch, generation=generation)
        instance.status, instance.ended_at = "running", None
        db.commit()
        logger.info("实例 %s 手动回退到访 %s（新代 %s）：%s",
                    instance.id, selected.id, generation, reason or "未填原因")
        self.advance(db, instance)

    # ---------------------------------------------------------------- 推进

    def advance(self, db, instance) -> None:
        plan = self._plan(db, instance)
        nodes = self._nodes(db, instance)
        while instance.status not in ("terminated", "success", "escalated", "failed"):
            tokens = self.owner._tokens(db, instance.id)
            if any(token.status == "waiting" for token in tokens):
                instance.status = "waiting"
                db.commit()
                return
            queued = [token for token in tokens if token.status == "queued"]
            if not queued:
                self._settle(db, instance, tokens)
                return
            self._enter(db, instance, queued[0], plan, nodes)
            db.commit()

    def _enter(self, db, instance, token, plan, nodes) -> None:
        step = plan["steps"].get(token.node_id)
        stamp = now_iso()
        token.visited_at = token.visited_at or stamp
        token.updated_at = stamp
        if step is None:
            token.status = "failed"
            self.owner._write_run(db, instance, token, status="failed",
                                  error="plan/step-missing: 步骤表缺少该节点", source="manual")
            return
        node = nodes.get(token.node_id) or {}
        if step.get("kind") == "start":
            # 开始节点的槽位在手动模式一个都不跑：直接放行到它的出线
            token.status = "done"
            self.owner._write_run(db, instance, token, status="ok", output={"initialized": True},
                                  evidence=self._evidence(node), source="manual")
            self._children(db, instance, plan, token,
                           list(step.get("next", {}).get("default") or []), "default")
            return
        if step.get("terminal"):
            # 到达即收敛，不生成待办
            token.status = "done"
            self.owner._write_run(db, instance, token, status="ok", evidence=self._evidence(node),
                                  source="manual")
            return
        token.status = "waiting"
        instance.status = "waiting"
        db.add(models.Review(
            id=new_id(), instance_id=instance.id, token_id=token.id, node_id=token.node_id,
            kind="action" if step.get("kind") not in ("category", "investigation") else "decision",
            status="pending", request_json=_dumps(self._request(token, step, node, nodes)),
        ))
        logger.info("实例 %s 手动等待节点 %s（%s）", instance.id, token.node_id, step.get("kind"))

    def _request(self, token, step, node, nodes) -> dict:
        kind = step.get("kind")
        request = {
            "type": "action", "nodeKind": kind,
            "title": _text(node.get("title")) or token.node_id,
            "detail": _text(node.get("detail")),
        }
        if kind == "category":
            request["type"] = "category"
            request["options"] = [
                {"node": node_id,
                 "title": _text((nodes.get(node_id) or {}).get("title")) or UNNAMED}
                for node_id in (step.get("next", {}).get("default") or [])
            ]
        elif kind == "investigation":
            request["type"] = "decision"
            fields = node.get("fields") if isinstance(node.get("fields"), dict) else {}
            request["fields"] = {key: _text(fields.get(key)) for key in FIELD_KEYS}
        return request

    def _settle(self, db, instance, tokens) -> None:
        stamp = now_iso()
        plan = self._plan(db, instance)
        steps = plan.get("steps") or {}
        kinds = {}
        for token in tokens:
            if token.status == "done":
                kinds[token.node_id] = (steps.get(token.node_id) or {}).get("kind")
        if "problemSolved" in kinds.values():
            instance.status = "success"
        elif "escalation" in kinds.values():
            instance.status = "escalated"
        else:
            instance.status = "failed"
        instance.ended_at = instance.ended_at or stamp
        db.commit()
        logger.info("实例 %s 手动模式收敛：%s", instance.id, instance.status)

    # ---------------------------------------------------------------- 工序

    def _children(self, db, instance, plan, parent, targets, branch, generation=None) -> None:
        # 手动模式一次只走一条：按串行语义插在父令牌之后，深度优先
        self.owner._create_tokens(db, instance, {**plan, "mode": "serial"}, parent, targets,
                                  branch, generation or parent.generation)

    def _evidence(self, node, *, options=None, nodes=None) -> dict:
        node = node or {}
        evidence = {"title": _text(node.get("title")), "detail": _text(node.get("detail"))}
        fields = node.get("fields") if isinstance(node.get("fields"), dict) else {}
        if fields:
            evidence["fields"] = {key: _text(fields.get(key)) for key in FIELD_KEYS}
        if options is not None:
            evidence["options"] = [
                {"node": node_id, "title": _text((nodes.get(node_id) or {}).get("title")) or UNNAMED}
                for node_id in options
            ]
        return evidence

    def _last_output(self, db, token) -> str | None:
        run = db.scalars(
            select(models.Run)
            .where(models.Run.token_id == token.id, models.Run.status == "ok")
            .order_by(models.Run.started_at.desc())
            .limit(1)
        ).first()
        return run.output_json if run is not None else None

    def _plan(self, db, instance) -> dict:
        return self.owner._plan(db, instance)

    def _release_plan(self, db, instance) -> dict:
        release = db.get(models.Release, instance.release_id)
        if release is None:
            return {}
        plan = _loads(release.plan_json)
        return plan if isinstance(plan, dict) else {}

    def _nodes(self, db, instance) -> dict[str, dict]:
        """节点文案与排查字段来自发布版本的 doc；读到就用，读不到退回只用 plan。"""
        try:
            release = db.get(models.Release, instance.release_id)
            revision = db.get(models.Revision, release.revision_id) if release else None
            if revision is None:
                return {}
            doc = expand(json.loads(revision.json))
        except (ValueError, TypeError):
            logger.warning("实例 %s 的发布文档不可读，手动模式只显示节点 id", instance.id)
            return {}
        return {node["id"]: node for node in doc.get("nodes", []) if isinstance(node, dict)}

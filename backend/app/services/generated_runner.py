"""生成脚本的平台适配：进程限额、事件记录、人工输入及按到访回退。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select

from app import db as database, models
from app.db import data_dir, new_id, now_iso
from app.services import codegen
from app.services.scheduler import Scheduler, _serialized, _loads, _dumps
from app.worker.node_sdk import validate_fields
from app.worker.processes import stop_tree, process_options

logger = logging.getLogger(__name__)


@dataclass
class Submission:
    review: str
    ready: threading.Event = field(default_factory=threading.Event)
    error: str = ""


@dataclass
class Branch:
    instance: str
    generation: int
    token: str
    process: subprocess.Popen
    owned: set = field(default_factory=set)
    states: dict = field(default_factory=dict)
    visits: set = field(default_factory=set)
    waiting: bool = False
    expected_exit: bool = False
    touched: float = field(default_factory=time.monotonic)
    error: str = ""
    origin: str = ""
    submission: Submission | None = None


class GeneratedRunner(Scheduler):
    def __init__(self):
        super().__init__(database.SessionLocal)
        self.branches = {}

    def _directory(self, instance):
        return data_dir() / "executions" / instance

    def _plan(self, db, instance):
        return _loads(instance.plan_json) or super()._plan(db, instance)

    def _close_session(self, instance_id):
        pass

    @_serialized
    def start(self, instance_id):
        with self._open() as db:
            instance = db.get(models.Instance, instance_id)
            if instance is None or instance.status in ("success", "escalated", "terminated"):
                return
            if self._tokens(db, instance_id):
                if not self.branches.get(instance_id):
                    self._fail(db, instance, "execution/interrupted", "诊断进程已退出，请重新启动诊断")
                    instance.status = "failed"
                    db.commit()
                return
            release = db.get(models.Release, instance.release_id)
            options = _loads(instance.runtime_json)
            plan, directory = codegen.prepare(db, instance, release, options)
            instance.plan_json = _dumps(plan)
            self._ensure_entry_token(db, instance, plan)
            token = self._tokens(db, instance_id)[0]
            self._save_launch(instance_id, token, {})
            db.commit()
            self._dispatch(db, instance)

    def _save_launch(self, instance_id, token, state):
        directory = self._directory(instance_id) / "branches"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{token.id}.json").write_text(_dumps({
            **state, "node": token.node_id, "token": token.id, "generation": token.generation,
        }), encoding="utf-8")

    def _dispatch(self, db, instance):
        if instance.status in ("paused", "success", "failed", "escalated", "terminated"):
            return
        handles = self.branches.setdefault(instance.id, [])
        options = _loads(instance.runtime_json)
        limit = 1 if options["mode"] == "serial" else options["maxParallel"]
        owned = set().union(*(handle.owned for handle in handles)) if handles else set()
        for token in self._tokens(db, instance.id):
            if len(handles) >= limit:
                break
            if token.status != "queued" or token.id in owned:
                continue
            directory = self._directory(instance.id)
            launch_path = directory / "branches" / f"{token.id}.json"
            launch = json.loads(launch_path.read_text(encoding="utf-8"))
            siblings = []
            if options["mode"] == "serial":
                for sibling in self._tokens(db, instance.id):
                    if sibling.status == "queued" and sibling.id != token.id and sibling.id not in owned:
                        siblings.append(json.loads((directory / "branches" / f"{sibling.id}.json").read_text(encoding="utf-8")))
                launch["pending"] = siblings
                launch_path.write_text(_dumps(launch), encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(directory / "flow.py"), "--worker", str(launch_path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", cwd=directory,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                    **process_options(),
                )
            except OSError as exc:
                token.status = "failed"
                self._write_run(db, instance, token, status="failed", error=f"execution/spawn: {exc}")
                db.commit()
                continue
            handle = Branch(instance.id, token.generation, token.id, proc, owned={token.id})
            handle.origin = token.id
            handle.owned.update(item["token"] for item in siblings)
            handles.append(handle)
            token.status = "running"
            instance.status = "running"
            db.commit()
            threading.Thread(target=self._stderr, args=(handle,), daemon=True).start()
            threading.Thread(target=self._pump, args=(handle,), daemon=True).start()
            threading.Thread(target=self._watch, args=(handle,), daemon=True).start()
            logger.info("实例 %s 分支启动 pid=%s 活跃=%s 上限=%s", instance.id, proc.pid, len(handles), limit)
        self._update_status(db, instance)
        db.commit()

    def _reply(self, handle, value):
        handle.process.stdin.write(_dumps(value) + "\n")
        handle.process.stdin.flush()

    def _stderr(self, handle):
        try:
            for line in handle.process.stderr:
                handle.error = (handle.error + line)[-8000:]
                logger.info("诊断 %s pid=%s %s", handle.instance, handle.process.pid, line.rstrip())
        except (OSError, UnicodeError):
            logger.exception("诊断错误流读取失败")

    def _watch(self, handle):
        while handle.process.poll() is None:
            time.sleep(1)
            if not handle.waiting and time.monotonic() - handle.touched > self._timeout:
                handle.error += "\nexecution/timeout: 自动步骤超过运行时限"
                stop_tree(handle.process)
                return

    def _pump(self, handle):
        try:
            for line in handle.process.stdout:
                event = json.loads(line)
                with self._instance_lock(handle.instance):
                    response = self._event(handle, event)
                    if response is not None:
                        self._reply(handle, response)
        except Exception:
            logger.exception("实例 %s 事件处理失败", handle.instance)
            if handle.process.poll() is None:
                stop_tree(handle.process)
        finally:
            handle.process.wait()
            with self._instance_lock(handle.instance):
                self._acknowledge(handle, "诊断进程已退出")
                handles = self.branches.get(handle.instance, [])
                if handle in handles:
                    handles.remove(handle)
                with self._open() as db:
                    instance = db.get(models.Instance, handle.instance)
                    if instance is None or handle.expected_exit:
                        return
                    token = db.get(models.Token, handle.token)
                    if token and token.status in ("running", "waiting"):
                        token.status = "failed"
                        self._write_run(db, instance, token, status="failed",
                                        error="execution/crash: " + (handle.error or "诊断进程意外退出"))
                        for review in db.scalars(select(models.Review).where(
                                models.Review.token_id == token.id, models.Review.status == "pending")):
                            review.status = "cancelled"
                    for owned in handle.owned:
                        pending = db.get(models.Token, owned)
                        if pending and pending.status == "queued":
                            path = self._directory(instance.id) / "branches" / f"{pending.id}.json"
                            launch = json.loads(path.read_text(encoding="utf-8"))
                            # 进程退出释放名额；尚未进入的串行思路继续使用最后取得的数据。
                            if handle.states:
                                launch["states"] = handle.states
                                path.write_text(_dumps(launch), encoding="utf-8")
                    db.commit()
                    self._settle_generated(db, instance)

    def _event(self, handle, event):
        handle.touched = time.monotonic()
        with self._open() as db:
            instance = db.get(models.Instance, handle.instance)
            if instance is None:
                return {"error": "实例不存在"}
            current = db.scalar(select(func.max(models.Token.generation)).where(models.Token.instance_id == instance.id))
            if handle.expected_exit or current != handle.generation or instance.status == "terminated":
                return {"error": "执行代已失效"}
            token = db.get(models.Token, event.get("token"))
            if token is None or token.id not in handle.owned or token.instance_id != instance.id:
                raise ValueError("进程没有该节点的执行权")
            handle.token = token.id
            kind = event["type"]
            if kind == "files":
                self._register_files(db, event.get("files", []))
                db.commit()
                return {}
            if kind == "enter":
                if token.status not in ("queued", "running"):
                    raise ValueError("该次到访已执行")
                token.status, token.visited_at, token.updated_at = "running", now_iso(), now_iso()
                handle.visits.add(token.id)
                instance.status = "running"
                db.commit()
                return {"token": token.id}
            if kind == "ask":
                handle.states = event.get("states", handle.states)
                self._save_latest_data(handle)
                request = event["request"]
                phase = request.get("type")
                if request.get("retry") and handle.submission:
                    review = db.get(models.Review, handle.submission.review)
                    review.status, review.decision_json = "pending", None
                    review.decided_at, review.decided_by = None, None
                    review.request_json = _dumps(request)
                else:
                    review = models.Review(id=new_id(), instance_id=instance.id, token_id=token.id,
                                           node_id=token.node_id, kind="action" if phase == "action" else "decision",
                                           status="pending", request_json=_dumps(request))
                    db.add(review)
                token.status = "waiting"
                handle.waiting = True
                instance.status = "paused" if phase == "after_success" else (
                    "running" if any(not item.waiting for item in self.branches.get(instance.id, [])) else "waiting"
                )
                db.commit()
                self._acknowledge(handle, request.get("error", "") if request.get("retry") else "")
                return None
            if kind == "complete":
                handle.states = event["states"]
                self._register_files(db, event.get("files", []))
                self._write_run(db, instance, token, status="ok", output=event.get("output"),
                                evidence={**event.get("evidence", {}), "branch": event.get("branch")},
                                args=event.get("arguments"), started=event.get("startedAt"))
                token.status = "failed" if event.get("failed") else "done"
                if event.get("failed"):
                    logger.info("实例 %s 节点 %s route/no-successor: 当前分支没有后继", instance.id, token.node_id)
                token.updated_at = now_iso()
                state = {"states": handle.states, "history": event["history"]}
                self._save_state(handle, token, state)
                children = self._create_tokens(db, instance, self._plan(db, instance), token,
                                               event["targets"], event["branch"], handle.generation)
                entries = []
                for index, child in enumerate(children):
                    self._save_launch(instance.id, child, state)
                    entries.append({"node": child.node_id, "token": child.id})
                    if index == 0 or _loads(instance.runtime_json)["mode"] == "serial":
                        handle.owned.add(child.id)
                db.commit()
                self._acknowledge(handle)
                self._dispatch(db, instance)
                return {"entries": entries}
            if kind == "error":
                handle.states = event.get("states", handle.states)
                self._save_latest_data(handle)
                token.status = "failed"
                self._write_run(db, instance, token, status="failed", error=event["message"],
                                args=event.get("arguments"), started=event.get("startedAt"))
            db.commit()
            if kind == "error":
                self._acknowledge(handle, event["message"])
            return {}

    def _acknowledge(self, handle, error=""):
        receipt = handle.submission
        if receipt:
            handle.submission = None
            receipt.error = error
            receipt.ready.set()

    def _save_state(self, handle, token, state):
        directory = self._directory(handle.instance) / "states"
        directory.mkdir(exist_ok=True)
        (directory / f"{token.id}.json").write_text(_dumps({**state, "branch": handle.origin}), encoding="utf-8")
        self._save_latest_data(handle)

    def _save_latest_data(self, handle):
        directory = self._directory(handle.instance) / "states"
        directory.mkdir(exist_ok=True)
        (directory / f"{handle.origin}.data.json").write_text(_dumps(handle.states), encoding="utf-8")

    def _settle_generated(self, db, instance):
        if instance.status in ("success", "escalated", "terminated"):
            return
        self._update_status(db, instance)
        db.commit()
        self._dispatch(db, instance)

    def _update_status(self, db, instance):
        # 结束/继续待办由存活的生成进程提出，不能由旧调度器创建无进程接收的待办。
        handles = self.branches.get(instance.id, [])
        tokens = self._tokens(db, instance.id)
        if handles:
            if any(not item.waiting for item in handles):
                instance.status = "running"
            elif instance.status != "paused":
                instance.status = "waiting"
        elif any(token.status == "queued" for token in tokens):
            instance.status = "running"
        else:
            plan = self._plan(db, instance)
            instance.status = "success" if self._success_token(tokens, plan) else (
                "escalated" if self._reached(tokens, plan, "escalation") else "failed"
            )
            instance.ended_at = instance.ended_at or now_iso()

    def submit_review(self, instance_id, review_id, decision):
        receipt = self._submit_review(instance_id, review_id, decision)
        # 不能持有实例锁等待：业务校验结果由事件线程写回同一实例。
        if receipt:
            if not receipt.ready.wait(self._timeout + 5):
                raise ValueError("业务处理尚未确认，请刷新执行状态后再操作")
            if receipt.error:
                raise ValueError(receipt.error)

    @_serialized
    def _submit_review(self, instance_id, review_id, decision):
        with self._open() as db:
            instance = db.get(models.Instance, instance_id)
            review = db.get(models.Review, review_id)
            if review is None or review.instance_id != instance_id or review.status != "pending":
                raise ValueError("待办已处理或不存在")
            handle = next((item for item in self.branches.get(instance_id, []) if item.token == review.token_id), None)
            if handle is None:
                raise ValueError("等待进程已结束")
            if not handle.waiting or handle.submission:
                raise ValueError("该分支正在处理上一次提交")
            request = _loads(review.request_json)
            phase = request.get("type")
            response = dict(decision)
            if phase in ("parameters", "acquisition"):
                fields = request.get("fields", [])
                if phase == "parameters":
                    from app.services.parameters import form_values
                    response["data"] = form_values(db, fields, decision.get("data"))
                else:
                    checked = validate_fields(fields, decision.get("data"))
                    response["data"] = self._resolve_manual_data(db, {"manualFields": fields}, checked)
            elif phase in ("decision", "judgment") and decision.get("result") not in ("yes", "no"):
                raise ValueError("请选择是或否")
            elif phase == "action" and decision.get("result") != "done":
                raise ValueError("请使用回退入口或确认完成")
            elif phase == "after_success" and decision.get("result") not in ("continue", "done", "yes", "no"):
                raise ValueError("请选择结束或继续")
            if phase == "judgment" and (not isinstance(decision.get("reason"), str) or not decision["reason"].strip()):
                raise ValueError("请填写判断依据")
            review.status, review.decision_json = "done", _dumps(decision)
            review.decided_at, review.decided_by = now_iso(), "local"
            token = db.get(models.Token, review.token_id)
            token.status = "done" if phase == "after_success" else "running"
            instance.status = "running"
            if phase == "after_success" and decision["result"] not in ("continue", "yes"):
                self._resolve_after_success(db, instance, "done", now_iso())
                self._stop(instance_id)
            db.commit()
            handle.waiting = False
            handle.touched = time.monotonic()
            receipt = Submission(review_id) if phase in ("parameters", "acquisition", "judgment") else None
            handle.submission = receipt
            if not handle.expected_exit:
                if phase == "after_success" and response["result"] == "yes":
                    response["result"] = "continue"
                self._reply(handle, response)
            return receipt

    def _stop(self, instance_id):
        for handle in self.branches.pop(instance_id, []):
            handle.expected_exit = True
            self._acknowledge(handle, "本次执行已取消")
            if handle.process.poll() is None:
                stop_tree(handle.process)

    @_serialized
    def terminate(self, instance_id):
        with self._open() as db:
            instance = db.get(models.Instance, instance_id)
            if instance.status == "terminated":
                return
            if instance.status in ("success", "escalated", "failed"):
                raise ValueError("已结束的实例不能终止")
            self._stop(instance_id)
            instance.status, instance.ended_at = "terminated", now_iso()
            self._abandon(db, instance, {token.id for token in self._tokens(db, instance_id)
                                         if token.status not in ("queued", "running", "waiting")})
            db.commit()

    def _abandon(self, db, instance, retained=frozenset()):
        for token in self._tokens(db, instance.id):
            if token.id not in retained and token.status != "failed":
                token.status = "abandoned"
        for review in db.scalars(select(models.Review).where(
                models.Review.instance_id == instance.id, models.Review.status == "pending")):
            review.status, review.decided_at = "cancelled", now_iso()

    @_serialized
    def rollback(self, instance_id, target, branch, reason, *, target_token_id=None, mode="branch"):
        with self._open() as db:
            instance = db.get(models.Instance, instance_id)
            if instance.status not in ("waiting", "paused", "failed"):
                raise ValueError("请在人工待办或排查结束后回退")
            tokens = self._tokens(db, instance_id)
            candidates = [item for item in tokens if item.id == target_token_id] if target_token_id else [
                item for item in tokens if item.node_id == target and item.visited_at]
            selected = candidates[-1] if candidates else None
            if selected is None:
                raise ValueError("未找到该次到访")
            saved = self._directory(instance_id) / "states" / f"{selected.id}.json"
            if not saved.is_file():
                raise ValueError("该次到访尚未完成，无法复用结果")
            plan = self._plan(db, instance)
            step = plan["steps"][selected.node_id]
            if step.get("terminal") or step["kind"] == "start":
                raise ValueError("开始和终止节点不能作为回退目标")
            if mode != "branch":
                raise ValueError("生成代码模式回退复用已完成结果，请选择后继分支")
            branch = branch if step["kind"] == "investigation" else "default"
            targets = step.get("next", {}).get(branch, [])
            if not targets:
                raise ValueError("所选分支没有后继")
            state = json.loads(saved.read_text(encoding="utf-8"))
            data_file = saved.with_name(f"{state.get('branch', selected.id)}.data.json")
            if data_file.is_file():
                state["states"] = json.loads(data_file.read_text(encoding="utf-8"))
            retained, cursor = set(), selected
            by_id = {item.id: item for item in tokens}
            while cursor and cursor.id not in retained:
                retained.add(cursor.id)
                cursor = by_id.get(cursor.parent_token_id)
            self._stop(instance_id)
            self._abandon(db, instance, retained)
            generation = max(item.generation for item in tokens) + 1
            children = self._create_tokens(db, instance, plan, selected, targets, branch, generation)
            for child in children:
                self._save_launch(instance_id, child, state)
            instance.status, instance.ended_at = "running", None
            db.commit()
            logger.info("实例 %s 回退到访 %s，复用结果选择 %s，新代 %s：%s",
                        instance_id, selected.id, branch, generation, reason or "未填原因")
            self._dispatch(db, instance)

    def shutdown(self):
        for instance in list(self.branches):
            with self._instance_lock(instance):
                self._stop(instance)


runner = GeneratedRunner()

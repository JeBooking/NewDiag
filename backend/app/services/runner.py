"""进程树执行（spec 第 6 节）。

一个实例是一棵进程树：每个进程跑一条路径，遇到分叉就上报 fork 然后退出，由后端
为每个分支另起进程。人工节点停在 input() 上，后端把用户的决定写进那个进程的 stdin。

进程不碰数据库：它只说 stdout 上的 JSON 行，落库全在这里。这样生成出来的工程
可以直接在终端里手工跑，不需要连库。
"""

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
from types import SimpleNamespace

from sqlalchemy import func, select

from app import db as db_module
from app import models
from app.db import data_dir, new_id, now_iso
from app.services import codegen

logger = logging.getLogger(__name__)

TERMINAL_INSTANCE = ("success", "failed", "escalated")
ACTIVE_TOKEN = ("queued", "running")

ERR_CRASH = "execution/crash"
ERR_TIMEOUT = "execution/timeout"
ERR_MISSING_STEP = "plan/step-missing"


@dataclass
class Handle:
    """一个正在跑的进程。"""

    instance_id: str
    node_id: str
    generation: int
    token_id: str
    proc: subprocess.Popen
    directory: Path
    outcome: str = ""
    # 回退会主动 kill 进程，这种退出不是执行失败，不能记一条 crash
    expected_exit: bool = False
    stderr: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None


class Runner:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        # 传进来的是模块级 SessionLocal 时，跟着 db.configure() 换库走
        self._follow_module = session_factory is db_module.SessionLocal
        self._handles: dict[str, list[Handle]] = {}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def _open(self):
        factory = db_module.SessionLocal if self._follow_module else self._session_factory
        return factory()

    # ------------------------------------------------------------ 对外入口

    def start(self, instance_id: str) -> None:
        """启动实例：铺开生成工程，从入口起第一个进程。"""
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is None or instance.status in TERMINAL_INSTANCE:
                return
            release = db.get(models.Release, instance.release_id)
            if release is None:
                self._fail_instance(db, instance, ERR_MISSING_STEP, "发布版本不存在")
                db.commit()
                return
            plan = _loads(release.plan_json) or {}
            entry = plan.get("entry")
            if not entry or not plan.get("steps"):
                self._fail_instance(db, instance, ERR_MISSING_STEP, "发布版本缺少步骤表")
                db.commit()
                return

            directory = self._materialize(db, instance, release, plan)
            existing = db.scalar(
                select(func.count(models.Token.id)).where(
                    models.Token.instance_id == instance.id
                )
            )
            if existing:
                if self._alive(instance.id):
                    return
                # 令牌还在、进程没了：后端重启过，Python 栈已经丢了，续不了
                logger.warning("实例 %s 已有执行记录但没有进程，标记中断", instance_id)
                self._fail_instance(
                    db, instance, "execution/interrupted", "执行进程已不在，实例无法续跑"
                )
                db.commit()
                return
            token = self._new_token(db, instance, str(entry), 1, None, "queued")
            db.commit()
            self._spawn(db, instance, token.id, str(entry), 1, directory)
        finally:
            db.close()

    def submit_review(self, instance_id: str, review_id: str, decision: dict) -> None:
        """把人的决定写回那个正在等待的进程。"""
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is None:
                raise ValueError("实例不存在")
            review = db.get(models.Review, review_id)
            if review is None or review.instance_id != instance_id:
                raise ValueError("待办不存在")

            payload = decision or _loads(review.decision_json) or {}
            request = _loads(review.request_json) or {}
            stamp = now_iso()
            review.decision_json = json.dumps(payload, ensure_ascii=False)
            review.decided_at = review.decided_at or stamp
            review.decided_by = review.decided_by or "local"
            review.status = "done"

            if request.get("type") == "after_success":
                self._resolve_after_success(db, instance, str(payload.get("result") or ""), stamp)
                db.commit()
                self._settle(db, instance)
                db.commit()
                return

            token = db.get(models.Token, review.token_id) if review.token_id else None
            if token is None or token.status != "waiting":
                # 重复提交：副作用已经发生过
                db.commit()
                return

            handle = self._handle_for_token(instance_id, token.id)
            if handle is None:
                raise ValueError("这个待办对应的进程已经结束，无法继续；请重新启动实例")

            self._write_stdin(handle, payload)
            token.status = "running"
            token.updated_at = stamp
            instance.status = "running"
            db.commit()
            logger.info(
                "实例 %s 待办 %s 已回写给进程（节点 %s，结果 %s）",
                instance_id,
                review_id,
                review.node_id,
                payload.get("result"),
            )
        finally:
            db.close()

    def rollback(self, instance_id: str, target: str, branch: str, reason: str | None) -> None:
        """回退：作废目标之后的路径，从目标节点的指定分支另起进程。"""
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is None:
                raise ValueError("实例不存在")
            if instance.status in TERMINAL_INSTANCE:
                raise ValueError("实例已结束，不能回退")
            release = db.get(models.Release, instance.release_id)
            plan = (_loads(release.plan_json) if release else None) or {}
            steps = plan.get("steps") or {}
            step = steps.get(target)
            if step is None:
                raise ValueError("目标节点不在该发布版本里")

            branch_key = branch if step.get("kind") == "investigation" else "default"
            if step.get("kind") == "investigation" and branch_key not in ("yes", "no"):
                raise ValueError("回退分支必须是是/否")
            targets = (step.get("next") or {}).get(branch_key) or []

            tokens = self._tokens(db, instance.id)
            latest = max((token.generation for token in tokens), default=1)
            target_token = self._executed_token(db, instance, steps, tokens, target, latest)
            if target_token is None:
                raise ValueError("目标节点在当前代没有执行记录，不能回退")

            descendants = _descendants(tokens, target_token.id)
            # 目标就是正在等人工的节点：没有后继可作废，改成重做它自己
            redo_self = not descendants and target_token.status == "waiting"
            if not descendants and not redo_self:
                raise ValueError("目标节点之后没有可回退的路径")
            if not targets and not redo_self:
                raise ValueError(f"目标节点没有 {branch_key} 分支的后继")

            blocked = self._other_active(db, instance, tokens, target_token)
            if blocked:
                raise ValueError("还有其他活跃路径，请先处理完再回退")

            stamp = now_iso()
            self._abandon_after(db, instance, tokens, target_token, stamp, redo_self=redo_self)
            generation = max(latest, target_token.generation) + 1
            directory = self._materialize(db, instance, release, plan)

            relaunch = [target] if redo_self else targets
            created = [
                self._new_token(db, instance, node, generation, target_token.id, "queued")
                for node in relaunch
            ]
            instance.status = "running"
            instance.ended_at = None
            db.commit()
            logger.info(
                "实例 %s 回退到 %s（%s）：%s，第 %d 代",
                instance_id,
                target,
                branch_key,
                reason or "未填原因",
                generation,
            )
            self._launch(db, instance, plan, created, directory)
            db.commit()
            self._settle(db, instance)
            db.commit()
        finally:
            db.close()

    def stop(self, instance_id: str) -> None:
        """关掉实例的所有进程。"""
        with self._lock:
            handles = self._handles.pop(instance_id, [])
        for handle in handles:
            _kill(handle)

    def close(self) -> None:
        for instance_id in list(self._handles):
            self.stop(instance_id)
        # 等 pump 线程读完 stdout 并落完库再放行，避免测试切换库时残留线程读到新库
        with self._lock:
            threads = list(self._threads)
            self._threads.clear()
        for thread in threads:
            thread.join(timeout=5)

    # ------------------------------------------------------------ 进程

    def _materialize(self, db, instance, release, plan: dict) -> Path:
        """把发布时冻结的源码铺到实例目录，并把启动上下文写进去。"""
        directory = data_dir() / "runs" / instance.id
        code = _loads(release.code_json) if getattr(release, "code_json", None) else None
        if not isinstance(code, dict) or not code:
            code = self._generate_plan_code(db, release, plan)
        codegen.write(directory, code)

        snapshot = db.get(models.Snapshot, instance.snapshot_id) if instance.snapshot_id else None
        context = _loads(snapshot.payload_json) if snapshot else {}
        (directory / "context.json").write_text(
            json.dumps(context or {}, ensure_ascii=False), encoding="utf-8"
        )
        self._write_runs(db, instance, directory)
        return directory

    def _write_runs(self, db, instance, directory: Path) -> None:
        """把已有输出落成 runs.json：分叉出来的进程靠它读上游结果。"""
        runs: dict[str, object] = {}
        for run in db.scalars(
            select(models.Run)
            .where(models.Run.instance_id == instance.id, models.Run.status == "ok")
            .order_by(models.Run.started_at)
        ):
            runs[run.node_id] = _loads(run.output_json)
        (directory / "runs.json").write_text(
            json.dumps(runs, ensure_ascii=False, default=repr), encoding="utf-8"
        )

    def _generate_plan_code(self, db, release, plan: dict) -> dict[str, str]:
        """老版本没有冻结源码时按 plan 现生成一份，保证老实例还能跑。"""
        projects: dict[str, dict] = {}
        for step in (plan.get("steps") or {}).values():
            call = step.get("call") if isinstance(step, dict) else None
            if not call:
                continue
            name = str(call.get("project") or "")
            if not name or name in projects:
                continue
            record = db.get(models.Project, name)
            if record is None:
                record = db.scalars(
                    select(models.Project).where(models.Project.name == name)
                ).first()
            if record is not None:
                projects[name] = {
                    "path": record.path or "",
                    "module": record.module or "",
                    "factory": record.factory or "",
                }
        flow = db.get(models.Flow, release.flow_id)
        return codegen.generate(
            plan,
            title=flow.name if flow else "",
            release_no=release.version_no,
            projects=projects,
        )

    def _spawn(self, db, instance, token_id: str, node_id: str, generation: int, directory: Path):
        python = self._python(db, instance)
        self._write_runs(db, instance, directory)
        env = {
            **os.environ,
            "DIAG_INSTANCE": instance.id,
            "DIAG_START": node_id,
            "DIAG_GENERATION": str(generation),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        try:
            proc = subprocess.Popen(
                [python, str(directory / codegen.FLOW_FILE)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(directory),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            self._fail_token(db, instance, token_id, ERR_CRASH, f"无法启动进程：{exc}")
            return None

        handle = Handle(
            instance_id=instance.id,
            node_id=node_id,
            generation=generation,
            token_id=token_id,
            proc=proc,
            directory=directory,
        )
        with self._lock:
            self._handles.setdefault(instance.id, []).append(handle)
        stdout_thread = threading.Thread(target=self._pump_stdout, args=(handle,), daemon=True)
        stderr_thread = threading.Thread(target=self._pump_stderr, args=(handle,), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        with self._lock:
            self._threads.extend([stdout_thread, stderr_thread])
        logger.info(
            "实例 %s 起进程 pid=%s 从节点 %s（第 %d 代）",
            instance.id,
            proc.pid,
            node_id,
            generation,
        )
        return handle

    def _python(self, db, instance) -> str:
        """优先用绑定工程的解释器；一个进程只能有一个，多工程时取第一个非空的。"""
        release = db.get(models.Release, instance.release_id)
        plan = (_loads(release.plan_json) if release else None) or {}
        for step in (plan.get("steps") or {}).values():
            call = step.get("call") if isinstance(step, dict) else None
            if not call:
                continue
            record = db.scalars(
                select(models.Project).where(models.Project.name == str(call.get("project") or ""))
            ).first()
            if record is not None and record.python:
                return record.python
        return sys.executable

    def _launch(self, db, instance, plan: dict, tokens: list, directory: Path) -> None:
        """并行：把所有令牌立刻发出去；串行：只跑队首，其余的等前面的收尾。"""
        if not tokens:
            return
        if plan.get("mode") == "serial":
            ordered = sorted(tokens, key=lambda item: (item.sort_order, item.created_at or ""))
            first = ordered[0]
            self._spawn(db, instance, first.id, first.node_id, first.generation, directory)
            return
        for token in tokens:
            self._spawn(db, instance, token.id, token.node_id, token.generation, directory)

    # ------------------------------------------------------------ 事件

    def _pump_stdout(self, handle: Handle) -> None:
        stream = handle.proc.stdout
        if stream is None:
            self._on_exit(handle)
            return
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    logger.warning("实例 %s 无法解析的协议行：%s", handle.instance_id, line[:200])
                    continue
                if isinstance(event, dict):
                    self._on_event(handle, event)
        finally:
            self._on_exit(handle)

    def _pump_stderr(self, handle: Handle) -> None:
        stream = handle.proc.stderr
        if stream is None:
            return
        for line in stream:
            handle.stderr.append(line)
            logger.debug("实例 %s 进程输出：%s", handle.instance_id, line.rstrip()[:500])
            if len(handle.stderr) > 200:
                del handle.stderr[:100]

    def _on_event(self, handle: Handle, event: dict) -> None:
        kind = str(event.get("t") or "")
        handle.outcome = kind
        db = self._open()
        try:
            instance = db.get(models.Instance, handle.instance_id)
            if instance is None:
                return
            release = db.get(models.Release, instance.release_id)
            plan = (_loads(release.plan_json) if release else None) or {}

            if kind == "enter":
                self._on_enter(db, instance, handle, str(event.get("node") or ""))
            elif kind == "leave":
                self._on_leave(db, instance, handle, event)
            elif kind == "call":
                self._on_call(db, instance, handle, event)
            elif kind == "ask":
                self._on_ask(db, instance, handle, event)
            elif kind == "fork":
                self._on_fork(db, instance, handle, plan, event)
            elif kind == "finish":
                self._on_finish(db, instance, handle, event)
            elif kind == "error":
                self._on_error(db, instance, handle, event)
            elif kind == "rollback":
                self._on_rollback(db, instance, handle, plan, event)
            db.commit()
            # 只有 ask 需要立刻把实例翻成 waiting（进程还卡在 input()，不会退出）；
            # 其余事件等到进程退出后由 _on_exit 统一收敛，避免在事件间隙误判终态。
            if kind == "ask":
                self._settle(db, instance)
                db.commit()
        except Exception:  # noqa: BLE001 - 事件处理失败不能带崩读线程
            logger.exception("实例 %s 处理事件 %s 失败", handle.instance_id, kind)
            db.rollback()
        finally:
            db.close()

    def _on_enter(self, db, instance, handle: Handle, node_id: str) -> None:
        token = db.get(models.Token, handle.token_id)
        if token is not None and token.node_id == node_id and token.status == "queued":
            token.status = "running"
            token.updated_at = now_iso()
            db.flush()
            return
        token = self._new_token(db, instance, node_id, handle.generation, handle.token_id, "running")
        handle.token_id = token.id

    def _on_leave(self, db, instance, handle: Handle, event: dict) -> None:
        """节点函数正常返回：把这个节点的令牌收尾。分支键留给需要它的地方看。"""
        token = db.get(models.Token, handle.token_id)
        if token is not None:
            branch = str(event.get("branch") or "")
            if token.status != "failed" and branch:
                token.branch = None if branch == "default" else branch
            token.status = "done"
            token.updated_at = now_iso()
            db.flush()

    def _on_call(self, db, instance, handle: Handle, event: dict) -> None:
        token = db.get(models.Token, handle.token_id)
        ok = bool(event.get("ok"))
        db.add(
            models.Run(
                id=new_id(),
                instance_id=instance.id,
                token_id=token.id if token else None,
                node_id=str(event.get("node") or (token.node_id if token else "")),
                generation=handle.generation,
                attempt=1,
                status="ok" if ok else "failed",
                input_json=_dumps(event.get("args")),
                output_json=_dumps(event.get("value")),
                evidence_json=_dumps(
                    {"branch": _branch_hint(event.get("value"))} if ok and token else None
                ),
                error=None if ok else str(event.get("error") or "execution/exception"),
                started_at=now_iso(),
                ended_at=now_iso(),
            )
        )
        if token is not None:
            token.status = "done" if ok else "failed"
            token.updated_at = now_iso()
        db.flush()

    def _on_ask(self, db, instance, handle: Handle, event: dict) -> None:
        token = db.get(models.Token, handle.token_id)
        if token is None:
            return
        node_id = str(event.get("node") or token.node_id)
        meta = self._node_meta(db, instance, node_id)
        kind = str(event.get("kind") or "decision")
        request = {
            "title": meta.get("title") or node_id,
            "detail": meta.get("detail") or meta.get("text") or "",
            "attachments": meta.get("attachments") or [],
            "instructions": str(event.get("prompt") or ""),
        }
        db.add(
            models.Review(
                id=new_id(),
                instance_id=instance.id,
                token_id=token.id,
                node_id=node_id,
                kind=kind,
                status="pending",
                request_json=json.dumps(request, ensure_ascii=False),
                created_at=now_iso(),
            )
        )
        token.status = "waiting"
        token.updated_at = now_iso()
        db.flush()

    def _on_fork(self, db, instance, handle: Handle, plan: dict, event: dict) -> None:
        token = db.get(models.Token, handle.token_id)
        if token is not None:
            token.status = "done"
            token.updated_at = now_iso()
        targets = [str(item) for item in (event.get("targets") or []) if item]
        parent = token.id if token is not None else handle.token_id
        created = [
            self._new_token(db, instance, node_id, handle.generation, parent, "queued")
            for node_id in targets
        ]
        db.commit()
        self._launch(db, instance, plan, created, handle.directory)

    def _on_finish(self, db, instance, handle: Handle, event: dict) -> None:
        token = db.get(models.Token, handle.token_id)
        if token is not None:
            token.status = "done"
            token.updated_at = now_iso()
        db.flush()

    def _on_error(self, db, instance, handle: Handle, event: dict) -> None:
        token = db.get(models.Token, handle.token_id)
        node_id = str(event.get("node") or (token.node_id if token else ""))
        code = str(event.get("code") or "execution/exception")
        db.add(
            models.Run(
                id=new_id(),
                instance_id=instance.id,
                token_id=token.id if token else None,
                node_id=node_id,
                generation=handle.generation,
                attempt=1,
                status="failed",
                input_json=_dumps(event.get("args")),
                error=f"{code}: {event.get('message') or ''}",
                started_at=now_iso(),
                ended_at=now_iso(),
            )
        )
        if token is not None:
            token.status = "failed"
            token.updated_at = now_iso()
        db.flush()

    def _on_rollback(self, db, instance, handle: Handle, plan: dict, event: dict) -> None:
        """进程内回退：人工节点上选了回退，进程上报后由这里另起进程重跑。"""
        target = str(event.get("target") or "")
        branch = str(event.get("branch") or "default")
        steps = plan.get("steps") or {}
        step = steps.get(target) or {}
        branch_key = branch if step.get("kind") == "investigation" else "default"
        targets = (step.get("next") or {}).get(branch_key) or []

        tokens = self._tokens(db, instance.id)
        latest = max((token.generation for token in tokens), default=handle.generation)
        target_token = self._executed_token(db, instance, steps, tokens, target, latest)
        if target_token is None:
            self._on_error(
                db,
                instance,
                handle,
                {
                    "node": target,
                    "code": "rollback/no-target",
                    "message": f"回退目标 {target} 没有执行记录",
                },
            )
            return

        descendants = _descendants(tokens, target_token.id)
        redo_self = not descendants and target_token.status == "waiting"
        if not descendants and not redo_self:
            self._on_error(
                db,
                instance,
                handle,
                {
                    "node": target,
                    "code": "rollback/no-path",
                    "message": f"回退目标 {target} 之后没有可回退的路径",
                },
            )
            return
        if not targets and not redo_self:
            self._on_error(
                db,
                instance,
                handle,
                {
                    "node": target,
                    "code": "route/no-outgoing",
                    "message": f"回退目标 {target} 没有 {branch_key} 分支的后继",
                },
            )
            return

        stamp = now_iso()
        self._abandon_after(db, instance, tokens, target_token, stamp, redo_self=redo_self)
        generation = max(latest, target_token.generation) + 1
        relaunch = [target] if redo_self else targets
        created = [
            self._new_token(db, instance, node_id, generation, target_token.id, "queued")
            for node_id in relaunch
        ]
        db.commit()
        self._launch(db, instance, plan, created, handle.directory)

    def _on_exit(self, handle: Handle) -> None:
        with self._lock:
            handles = self._handles.get(handle.instance_id, [])
            if handle in handles:
                handles.remove(handle)
            if not handles:
                self._handles.pop(handle.instance_id, None)

        code = handle.proc.poll()
        db = self._open()
        try:
            instance = db.get(models.Instance, handle.instance_id)
            if instance is None:
                return
            # 正常收尾的事件（fork/finish/rollback/error）已经改过令牌；回退主动 kill 的也不算错
            if not handle.expected_exit and handle.outcome not in ("fork", "finish", "rollback", "error"):
                detail = "".join(handle.stderr[-10:]).strip()
                self._fail_token(
                    db,
                    instance,
                    handle.token_id,
                    ERR_CRASH,
                    f"进程退出（code={code}）{('：' + detail[-300:]) if detail else ''}",
                )
            db.commit()
            self._settle(db, instance)
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------ 状态收敛

    def _settle(self, db, instance) -> None:
        if instance.status in TERMINAL_INSTANCE:
            return
        release = db.get(models.Release, instance.release_id)
        plan = (_loads(release.plan_json) if release else None) or {}
        tokens = self._tokens(db, instance.id)
        alive = self._alive(instance.id)

        def apply(status: str) -> None:
            if instance.status != status:
                logger.info("实例 %s 状态 %s -> %s", instance.id, instance.status, status)
            instance.status = status

        # 有人等人工就显示 waiting，哪怕别的分支还在跑：等人工是这个实例当前最该被看见的状态
        statuses = [token.status for token in tokens]
        if "running" in statuses:
            apply("running")
            return
        if "waiting" in statuses:
            apply("waiting")
            return
        if alive:
            apply("running")
            return

        queued = [token for token in tokens if token.status == "queued"]
        if queued:
            success = self._reached(tokens, plan, "problemSolved")
            if plan.get("mode") == "serial" and success is not None:
                review = self._ensure_after_success_review(db, instance, success)
                if review is not None:
                    apply("paused")
                    return
            apply("running")
            db.commit()
            ordered = sorted(queued, key=lambda item: (item.sort_order, item.created_at or ""))
            handle = self._spawn(
                db,
                instance,
                ordered[0].id,
                ordered[0].node_id,
                ordered[0].generation,
                self._instance_dir(instance),
            )
            if handle is None:
                apply("failed")
            return

        if self._reached(tokens, plan, "problemSolved") is not None:
            apply("success")
        elif self._reached(tokens, plan, "escalation") is not None:
            apply("escalated")
        else:
            apply("failed")
        instance.ended_at = instance.ended_at or now_iso()
        logger.info(
            "实例 %s 结束：%s，令牌 %s",
            instance.id,
            instance.status,
            [(token.node_id, token.status) for token in tokens],
        )

    def _ensure_after_success_review(self, db, instance, token):
        for review in db.scalars(
            select(models.Review).where(models.Review.instance_id == instance.id)
        ):
            request = _loads(review.request_json) or {}
            if request.get("type") != "after_success" or review.token_id != token.id:
                continue
            # 已经问过：待办还在就继续暂停，已处理就不再拦
            return review if review.status == "pending" else None
        review = models.Review(
            id=new_id(),
            instance_id=instance.id,
            token_id=token.id,
            node_id=token.node_id,
            kind="decision",
            status="pending",
            request_json=json.dumps(
                {
                    "type": "after_success",
                    "title": "已找到问题，是否继续排查剩余思路",
                    "detail": "串行模式下队列里还有待排查的分支，可结束本次诊断或继续。",
                },
                ensure_ascii=False,
            ),
            created_at=now_iso(),
        )
        db.add(review)
        db.flush()
        return review

    def _resolve_after_success(self, db, instance, result: str, stamp: str) -> None:
        if result in ("continue", "yes"):
            instance.status = "running"
            return
        instance.status = "success"
        instance.ended_at = stamp
        for token in self._tokens(db, instance.id):
            if token.status in ACTIVE_TOKEN:
                token.status = "abandoned"
                token.updated_at = stamp
        for review in db.scalars(
            select(models.Review).where(
                models.Review.instance_id == instance.id, models.Review.status == "pending"
            )
        ):
            review.status = "cancelled"
            review.decided_at = stamp
        self.stop(instance.id)

    # ------------------------------------------------------------ 回退细节

    def _executed_token(self, db, instance, steps: dict, tokens: list, node_id: str, generation: int):
        step = steps.get(node_id) or {}
        needs_run = bool(step.get("call"))
        candidates = [
            token
            for token in tokens
            if token.node_id == node_id
            and token.generation <= generation
            and token.status in ("done", "waiting", "running")
        ]
        candidates.sort(key=lambda item: (item.generation, item.created_at or ""), reverse=True)
        for token in candidates:
            if not needs_run:
                return token
            run = db.scalars(
                select(models.Run)
                .where(
                    models.Run.instance_id == instance.id,
                    models.Run.token_id == token.id,
                    models.Run.status == "ok",
                )
                .limit(1)
            ).first()
            if run is not None:
                return token
        return None

    def _other_active(self, db, instance, tokens: list, target_token) -> list:
        """目标之外还有别的路径在跑或等人时不许回退（v1 保守策略）。"""
        descendants = _descendants(tokens, target_token.id) | {target_token.id}
        blocked = []
        for token in tokens:
            if token.status in ACTIVE_TOKEN or token.status == "waiting":
                if token.id not in descendants:
                    blocked.append(token)
        return blocked

    def _abandon_after(
        self, db, instance, tokens: list, target_token, stamp: str, redo_self: bool = False
    ) -> None:
        """把目标之后的路径作废：子令牌、它们的 run、进程，以及挂着的待办。

        回退语义分两种：重跑后继（目标本身保留 done，作废其后代）与重做目标
        （目标正等人，把目标自己也作废再重排一个）。redo_self 只对后者为真。
        """
        if target_token is None:
            return
        descendants = _descendants(tokens, target_token.id)
        affected = set(descendants)
        if redo_self:
            affected.add(target_token.id)
        for token in tokens:
            if token.id in affected and token.status not in ("failed", "abandoned"):
                token.status = "abandoned"
                token.updated_at = stamp

        with self._lock:
            handles = list(self._handles.get(instance.id, []))
        for handle in handles:
            if handle.token_id in affected:
                logger.info("实例 %s 回退：终止进程 pid=%s", instance.id, handle.proc.pid)
                handle.expected_exit = True
                _kill(handle)
                with self._lock:
                    current = self._handles.get(instance.id, [])
                    if handle in current:
                        current.remove(handle)

        for run in db.scalars(
            select(models.Run).where(
                models.Run.instance_id == instance.id, models.Run.token_id.in_(affected)
            )
        ):
            run.status = "abandoned"
        for review in db.scalars(
            select(models.Review).where(
                models.Review.instance_id == instance.id, models.Review.status == "pending"
            )
        ):
            review.status = "cancelled"
            review.decided_at = stamp
        db.flush()

    # ------------------------------------------------------------ 杂项

    def _new_token(
        self, db, instance, node_id: str, generation: int, parent_id: str | None, status: str
    ):
        stamp = now_iso()
        token = models.Token(
            id=new_id(),
            instance_id=instance.id,
            node_id=node_id,
            status=status,
            sort_order=self._next_order(db, instance.id),
            generation=generation,
            parent_token_id=parent_id,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(token)
        db.flush()
        return token

    def _next_order(self, db, instance_id: str) -> int:
        current = db.scalar(
            select(func.max(models.Token.sort_order)).where(models.Token.instance_id == instance_id)
        )
        return int(current or 0) + 1

    def _tokens(self, db, instance_id: str) -> list:
        return list(
            db.scalars(
                select(models.Token)
                .where(models.Token.instance_id == instance_id)
                .order_by(models.Token.sort_order, models.Token.created_at)
            )
        )

    def _alive(self, instance_id: str) -> list[Handle]:
        with self._lock:
            handles = self._handles.get(instance_id, [])
            return [handle for handle in handles if handle.alive]

    def _handle_for_token(self, instance_id: str, token_id: str) -> Handle | None:
        with self._lock:
            for handle in self._handles.get(instance_id, []):
                if handle.token_id == token_id and handle.alive:
                    return handle
        return None

    def _instance_dir(self, instance) -> Path:
        return data_dir() / "runs" / instance.id

    def _fail_token(self, db, instance, token_id: str, code: str, message: str) -> None:
        token = db.get(models.Token, token_id) if token_id else None
        db.add(
            models.Run(
                id=new_id(),
                instance_id=instance.id,
                token_id=token_id or None,
                node_id=token.node_id if token else "__instance__",
                generation=token.generation if token else 1,
                attempt=1,
                status="failed",
                error=f"{code}: {message}",
                started_at=now_iso(),
                ended_at=now_iso(),
            )
        )
        if token is not None:
            token.status = "failed"
            token.updated_at = now_iso()
        db.flush()

    def _fail_instance(self, db, instance, code: str, message: str) -> None:
        self._fail_token(db, instance, None, code, message)
        instance.status = "failed"
        instance.ended_at = instance.ended_at or now_iso()

    def _reached(self, tokens: list, plan: dict, kind: str):
        steps = plan.get("steps") or {}
        for token in tokens:
            if token.status != "done":
                continue
            if (steps.get(token.node_id) or {}).get("kind") == kind:
                return token
        return None

    def _node_meta(self, db, instance, node_id: str) -> dict:
        release = db.get(models.Release, instance.release_id)
        if release is None:
            return {}
        revision = db.get(models.Revision, release.revision_id)
        if revision is None:
            return {}
        doc = _loads(revision.json) or {}
        for node in doc.get("nodes") or []:
            if isinstance(node, dict) and node.get("id") == node_id:
                return node
        return {}

    def _write_stdin(self, handle: Handle, payload: dict) -> None:
        stream = handle.proc.stdin
        if stream is None:
            raise ValueError("进程的输入流已关闭")
        try:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stream.flush()
        except (OSError, ValueError) as exc:
            raise ValueError(f"写入进程失败：{exc}") from exc


def _kill(handle: Handle) -> None:
    try:
        if handle.proc.poll() is None:
            handle.proc.kill()
    except OSError:
        pass


def _loads(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _dumps(value):
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=repr)


def _branch_hint(value) -> str | None:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return None


def _descendants(tokens: list, root: str) -> set[str]:
    children: dict[str, list[str]] = {}
    for token in tokens:
        if token.parent_token_id:
            children.setdefault(token.parent_token_id, []).append(token.id)
    found: set[str] = set()
    queue = list(children.get(root, []))
    while queue:
        current = queue.pop()
        if current in found:
            continue
        found.add(current)
        queue.extend(children.get(current, []))
    return found


runner = Runner(db_module.SessionLocal)

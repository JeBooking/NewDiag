"""双策略调度循环（spec 第 6 节）。

每个实例一个 daemon 线程；线程内每次只开一个短事务，不跨线程共享 Session。
人工节点把实例推到 waiting/paused 后线程自然退出，由 submit_review/rollback 重新拉起。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from functools import wraps
from types import SimpleNamespace

from sqlalchemy import func, select

from app import db as db_module
from app import models
from app.db import SessionLocal, new_id, now_iso
from app.logging_setup import brief
from app.services import signatures
from app.services.sessions import SessionCrashed, SessionError, SessionManager, SessionTimeout

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0

TERMINAL_INSTANCE = ("success", "failed", "escalated", "terminated")
ACTIVE_TOKEN = ("queued", "running")

# 失败分类：route/* 属于正常排除，execution/* 属于真出错
ERR_ROUTE = "route/no-outgoing"
ERR_MISSING_STEP = "plan/step-missing"
ERR_ARGUMENT = "execution/argument"
ERR_TIMEOUT = "execution/timeout"
ERR_CRASH = "execution/crash"
ERR_EXCEPTION = "execution/exception"

YES_VALUES = {"yes", "y", "true", "是"}
NO_VALUES = {"no", "n", "false", "否"}


def _serialized(method):
    @wraps(method)
    def guarded(self, instance_id, *args, **kwargs):
        with self._instance_lock(instance_id):
            return method(self, instance_id, *args, **kwargs)
    return guarded


class Scheduler:
    def __init__(
        self,
        session_factory,
        sessions: SessionManager | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._session_factory = session_factory
        # 传进来的是模块级 SessionLocal 时，跟着 db.configure() 换库走
        self._follow_module = session_factory is db_module.SessionLocal
        self._sessions = sessions if sessions is not None else SessionManager()
        self._timeout = timeout
        self._threads: dict[str, threading.Thread] = {}
        self._wake: set[str] = set()
        self._lock = threading.Lock()
        self._operation_locks: dict[str, threading.RLock] = {}
        self._termination_locks: dict[str, threading.Lock] = {}
        self._cancelled: set[str] = set()

    def _instance_lock(self, instance_id: str):
        with self._lock:
            return self._operation_locks.setdefault(instance_id, threading.RLock())

    def _is_cancelled(self, instance_id: str) -> bool:
        with self._lock:
            return instance_id in self._cancelled

    def _open(self):
        factory = db_module.SessionLocal if self._follow_module else self._session_factory
        return factory()

    # ------------------------------------------------------------ 对外入口

    def start(self, instance_id: str) -> None:
        """起线程跑主循环并立刻返回；同一实例不会重复起线程。"""
        with self._lock:
            existing = self._threads.get(instance_id)
            if existing is not None and existing.is_alive():
                self._wake.add(instance_id)  # 让在跑的循环再检查一次队列
                return
            thread = threading.Thread(
                target=self._run_loop,
                args=(instance_id,),
                name=f"diag-sched-{instance_id[:8]}",
                daemon=True,
            )
            self._threads[instance_id] = thread
            thread.start()
            logger.info("实例 %s 调度线程启动", instance_id)

    def terminate(self, instance_id: str) -> None:
        # 同实例的重复终止请求不能交错设置与撤销临时取消标记。
        with self._lock:
            request_lock = self._termination_locks.setdefault(instance_id, threading.Lock())
        with request_lock:
            self._terminate(instance_id)

    def _terminate(self, instance_id: str) -> None:
        # 调用持有实例操作锁，必须先中断专属进程才能等调度收尾。
        with self._open() as db:
            instance = db.get(models.Instance, instance_id)
            if instance is None:
                raise ValueError("实例不存在")
            if instance.status == "terminated":
                return
            if instance.status in TERMINAL_INSTANCE:
                raise ValueError("实例已结束，不能终止")
        with self._lock:
            self._cancelled.add(instance_id)
        self._sessions.cancel(instance_id)
        with self._instance_lock(instance_id), self._open() as db:
            instance = db.get(models.Instance, instance_id)
            if instance.status == "terminated":
                return
            if instance.status in TERMINAL_INSTANCE:
                # 自然结束先落库时保留原结论；失败实例仍可回退到新一代。
                self._sessions.clear_cancellation(instance_id)
                with self._lock:
                    self._cancelled.discard(instance_id)
                raise ValueError("实例已结束，不能终止")
            stamp = now_iso()
            instance.status = "terminated"
            instance.ended_at = stamp
            for token in self._tokens(db, instance_id):
                if token.status in ("queued", "running", "waiting"):
                    token.status = "abandoned"
                    token.updated_at = stamp
            for review in db.scalars(select(models.Review).where(
                models.Review.instance_id == instance_id, models.Review.status == "pending"
            )):
                review.status = "cancelled"
                review.decided_at = stamp
            db.commit()
            logger.info("实例 %s 已由用户终止", instance_id)

    @_serialized
    def submit_review(self, instance_id: str, review_id: str, decision: dict) -> None:
        """人工提交判断/完成/回退，与实例终止串行处理。"""
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is None:
                raise ValueError("实例不存在")
            if instance.status in TERMINAL_INSTANCE or self._is_cancelled(instance_id):
                raise ValueError("实例已结束，不能处理待办")
            review = db.get(models.Review, review_id)
            if review is None or review.instance_id != instance_id:
                raise ValueError("待办不存在")
            if review.status != "pending":
                raise ValueError("该待办已处理")

            payload = decision or _loads(review.decision_json) or {}
            request = _loads(review.request_json) or {}
            stamp = now_iso()
            review.decision_json = json.dumps(payload, ensure_ascii=False)
            review.decided_at = review.decided_at or stamp
            review.decided_by = review.decided_by or "local"
            review.status = "done"

            result = str(payload.get("result") or "")

            if request.get("type") == "after_success":
                self._resolve_after_success(db, instance, result, stamp)
                db.commit()
                if instance.status == "running":
                    self.start(instance_id)
                return

            plan = self._plan(db, instance)
            token = db.get(models.Token, review.token_id) if review.token_id else None
            if token is None:
                raise ValueError("待办缺少令牌")
            if instance.status in TERMINAL_INSTANCE:
                db.commit()
                return
            if token.status != "waiting":
                db.commit()  # 重复提交：副作用已发生过
                return

            step = plan["steps"].get(token.node_id) or {}
            if result == "rollback":
                branch = str(payload.get("branch") or _opposite(token.branch))
                target = str(payload.get("target") or token.node_id)
                self._rollback(db, instance, plan, target, branch, payload.get("reason"))
                db.commit()
                self.start(instance_id)
                return

            if result == "continue":
                if instance.status != "paused":
                    raise ValueError("当前实例不在暂停态，无法继续")
                instance.status = "running"
                db.commit()
                self.start(instance_id)
                return

            if result not in ("yes", "no", "done"):
                raise ValueError(f"无法识别的处理结果：{result}")
            # 只有排查有是/否语义，动作节点一律按「完成」推进
            is_decision = step.get("kind") == "investigation"
            branch = result if is_decision else "default"
            if not (step.get("next") or {}).get(branch):
                token.status = "failed"
                token.updated_at = stamp
            else:
                token.status = "done"
                token.updated_at = stamp
                self._spawn_successors(db, instance, plan, token, step, branch)
            self._refresh(db, instance, plan)
            db.commit()
            if instance.status not in TERMINAL_INSTANCE:
                self.start(instance_id)
            else:
                self._close_session(instance_id)
        finally:
            db.close()

    @_serialized
    def rollback(self, instance_id: str, target: str, branch: str, reason: str | None) -> None:
        if self._is_cancelled(instance_id):
            raise ValueError("实例已终止，不能回退")
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is None:
                raise ValueError("实例不存在")
            plan = self._plan(db, instance)
            self._rollback(db, instance, plan, target, branch, reason)
            db.commit()
            self.start(instance_id)
        finally:
            db.close()

    # ------------------------------------------------------------ 主循环

    def _run_loop(self, instance_id: str) -> None:
        try:
            while True:
                if not self._preflight(instance_id):
                    return
                while self._step(instance_id):
                    continue
                self._settle(instance_id)
                with self._lock:
                    if instance_id in self._wake:
                        # 循环收尾期间有人提交了待办，重新检查再退出
                        self._wake.discard(instance_id)
                        continue
                    if self._threads.get(instance_id) is threading.current_thread():
                        self._threads.pop(instance_id, None)
                    return
        except Exception:  # noqa: BLE001 - 线程里不能把异常吞掉不留痕
            logger.exception("实例 %s 调度异常", instance_id)
            self._fail_instance(instance_id)
        finally:
            with self._lock:
                current = self._threads.get(instance_id)
                if current is threading.current_thread():
                    self._threads.pop(instance_id, None)
                self._wake.discard(instance_id)

    def _preflight(self, instance_id: str) -> bool:
        """启动前比对签名；不一致直接失败，避免用错参数跑。"""
        with self._instance_lock(instance_id), self._open() as db:
            instance = db.get(models.Instance, instance_id)
            if instance is None or instance.status in TERMINAL_INSTANCE or self._is_cancelled(instance_id):
                return False
            plan = self._plan(db, instance)
            if not plan.get("steps"):
                self._fail(db, instance, ERR_MISSING_STEP, "发布版本缺少步骤表")
                db.commit()
                return False
            finished_runs = db.scalar(
                select(func.count(models.Run.id)).where(
                    models.Run.instance_id == instance_id,
                    models.Run.status != "abandoned",
                )
            )
            self._ensure_entry_token(db, instance, plan)
            db.commit()
            if finished_runs or not any(step.get("call") for step in plan["steps"].values()):
                return True  # 只在实例首次启动时比对签名，人工恢复不重新拦
            stored = self._stored_signatures(db, instance)
            expected = plan if plan.get("signature") else {**plan, "signature": stored}
        # 签名探测可能等待外部工程启动，不占用终止操作需要的实例锁。
        try:
            current = signatures.collect_signatures(
                plan, cancelled=lambda: self._is_cancelled(instance_id)
            )
            diffs = signatures.compare_signatures(expected, current)
        except Exception:  # noqa: BLE001 - 探针失败不阻断启动
            logger.warning("实例 %s 签名比对失败，跳过", instance_id, exc_info=True)
            return not self._is_cancelled(instance_id)
        with self._instance_lock(instance_id), self._open() as db:
            instance = db.get(models.Instance, instance_id)
            if instance is None or instance.status in TERMINAL_INSTANCE or self._is_cancelled(instance_id):
                return False
            if diffs:
                detail = "；".join(f"{item['key']} {item['status']}" for item in diffs)
                self._fail(db, instance, "signature/changed", f"函数签名与发布时不一致：{detail}")
                db.commit()
                self._close_session(instance_id)
                return False
            return True

    @_serialized
    def _step(self, instance_id: str) -> bool:
        """推进一轮；返回是否还有可推进的令牌。"""
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is None or instance.status in TERMINAL_INSTANCE or self._is_cancelled(instance_id):
                return False
            if instance.status == "paused":
                return False
            plan = self._plan(db, instance)
            tokens = self._tokens(db, instance_id)
            queued = [t for t in tokens if t.status == "queued"]
            if not queued:
                return False

            if plan.get("mode") == "serial":
                # 串行一次只跑一条路径；队首在等人工时不推进后面的
                if any(t.status in ("waiting", "running") for t in tokens):
                    return False
                targets = [min(queued, key=lambda t: (t.sort_order, t.created_at or ""))]
            else:
                targets = queued

            for token in targets:
                if self._is_cancelled(instance_id):
                    return False
                self._advance(db, instance, plan, token)
                db.commit()
            if self._is_cancelled(instance_id):
                return False
            self._refresh(db, instance, plan)
            db.commit()
            logger.debug(
                "实例 %s 本轮推进 %s",
                instance_id,
                [(t.node_id, t.generation) for t in targets],
            )
            return True
        finally:
            db.close()

    @_serialized
    def _settle(self, instance_id: str) -> None:
        if self._is_cancelled(instance_id):
            return
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is None:
                return
            plan = self._plan(db, instance)
            self._refresh(db, instance, plan)
            db.commit()
            if instance.status in TERMINAL_INSTANCE:
                self._close_session(instance_id)
        finally:
            db.close()

    @_serialized
    def _fail_instance(self, instance_id: str) -> None:
        if self._is_cancelled(instance_id):
            return
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is not None and instance.status not in TERMINAL_INSTANCE:
                instance.status = "failed"
                instance.ended_at = now_iso()
                db.commit()
        finally:
            db.close()
        self._close_session(instance_id)

    # ------------------------------------------------------------ 单节点执行

    def _advance(self, db, instance, plan: dict, token) -> None:
        step = plan.get("steps", {}).get(token.node_id)
        if step is None:
            self._write_run(
                db,
                instance,
                token,
                status="failed",
                error=f"{ERR_MISSING_STEP}: 步骤表里没有节点 {token.node_id}",
                started=now_iso(),
            )
            self._fail_token(db, instance, token)
            return

        token.status = "running"
        token.updated_at = now_iso()
        db.flush()
        logger.debug(
            "实例 %s 推进节点 %s（%s，gen=%s）",
            instance.id,
            token.node_id,
            step.get("kind"),
            token.generation,
        )

        if step.get("terminal"):
            token.status = "done"
            token.updated_at = now_iso()
            db.flush()
            logger.info("实例 %s 到达终止节点 %s（%s）", instance.id, token.node_id, step.get("kind"))
            return

        if step.get("human"):
            self._ensure_review(db, instance, token, step)
            token.status = "waiting"
            token.updated_at = now_iso()
            db.flush()
            logger.info(
                "实例 %s 等待人工：节点 %s（%s）", instance.id, token.node_id, step.get("kind")
            )
            return

        call = step.get("call")
        if call:
            self._run_call(db, instance, plan, token, step, call)
            return

        # 结论节点：展示设计时文本后继续
        if not (step.get("next") or {}).get("default"):
            self._fail_token(db, instance, token)
            return
        self._spawn_successors(db, instance, plan, token, step, "default")
        token.status = "done"
        token.updated_at = now_iso()
        db.flush()

    def _run_call(self, db, instance, plan: dict, token, step: dict, call: dict) -> None:
        args, error = self._eval_args(
            db, instance, token, call.get("args") or {}, str(call.get("function") or "")
        )
        started = now_iso()
        t0 = time.perf_counter()
        if error is not None:
            logger.warning("实例 %s 节点 %s 参数求值失败：%s", instance.id, token.node_id, error)
            self._write_run(
                db,
                instance,
                token,
                status="failed",
                error=f"{ERR_ARGUMENT}: {error}",
                started=started,
            )
            self._fail_token(db, instance, token)
            return

        value, failure = self._invoke(db, instance, call, args, token)
        if self._is_cancelled(instance.id):
            self._write_run(
                db, instance, token, status="abandoned", args=args,
                error="execution/terminated: 用户终止诊断", started=started,
            )
            return
        if failure is not None:
            code, message, attempts = failure
            logger.warning(
                "实例 %s 节点 %s 调用 %s.%s 失败（%s，第 %s 次）：%s",
                instance.id,
                token.node_id,
                call.get("project"),
                call.get("function"),
                code,
                attempts,
                message,
            )
            self._write_run(
                db,
                instance,
                token,
                status="failed",
                args=args,
                error=f"{code}: {message}",
                started=started,
                attempt=attempts,
            )
            self._fail_token(db, instance, token)
            return

        branch = _branch_of(step, value)
        targets = (step.get("next") or {}).get(branch) if branch else None
        if not targets:
            reason = (
                f"返回值 {value!r} 无法判定为是/否"
                if branch is None
                else f"分支 {branch} 没有后继节点"
            )
            logger.warning("实例 %s 节点 %s 无法路由：%s", instance.id, token.node_id, reason)
            self._write_run(
                db,
                instance,
                token,
                status="failed",
                args=args,
                output=value,
                error=f"{ERR_ROUTE}: {reason}",
                started=started,
            )
            self._fail_token(db, instance, token)
            return

        self._write_run(
            db,
            instance,
            token,
            status="ok",
            args=args,
            output=value,
            evidence={"branch": branch} if step.get("kind") == "investigation" else None,
            started=started,
        )
        self._spawn_successors(db, instance, plan, token, step, branch)
        token.status = "done"
        token.updated_at = now_iso()
        db.flush()
        logger.info(
            "实例 %s 节点 %s 调用 %s.%s -> %s，分支 %s，%.0fms",
            instance.id,
            token.node_id,
            call.get("project"),
            call.get("function"),
            brief(value, 120),
            branch,
            (time.perf_counter() - t0) * 1000,
        )

    def _invoke(self, db, instance, call: dict, args: dict, token):
        """调会话子进程；超时/崩溃重试一次。返回 (value, (code, message, attempts))。"""
        ref = str(call.get("project") or "")
        function = str(call.get("function") or "")
        project = self._project(db, ref)
        if project is None:
            return None, (ERR_EXCEPTION, f"工程 {ref} 未登记，无法调用 {function}", 1)
        attempts = 0
        while attempts < 2:
            if self._is_cancelled(instance.id):
                return None, ("execution/terminated", "用户终止诊断", attempts)
            attempts += 1
            logger.debug(
                "实例 %s 调用 %s.%s（第 %s 次）参数 %s",
                instance.id,
                ref,
                function,
                attempts,
                brief(args),
            )
            try:
                value = self._sessions.call(
                    instance.id, project, function, args, timeout=self._timeout
                )
                return value, None
            except SessionTimeout as exc:
                if attempts >= 2:
                    return None, (ERR_TIMEOUT, str(exc), attempts)
                logger.warning("实例 %s 调用 %s 超时，重启会话后重试", instance.id, function)
            except SessionCrashed as exc:
                if attempts >= 2:
                    return None, (ERR_CRASH, str(exc), attempts)
                logger.warning(
                    "实例 %s 调用 %s 会话崩溃，重启会话后重试：%s", instance.id, function, exc
                )
            except SessionError as exc:
                return None, (ERR_EXCEPTION, str(exc), attempts)
            except Exception as exc:  # noqa: BLE001 - 业务工程千奇百怪，统一记执行错误
                return None, (ERR_EXCEPTION, str(exc), attempts)
        return None, (ERR_TIMEOUT, "调用超时", attempts)

    def _eval_args(
        self, db, instance, token, specs: dict, function: str = ""
    ) -> tuple[dict, str | None]:
        snapshot = self._snapshot_payload(db, instance)
        args: dict = {}
        for name, expr in specs.items():
            if not isinstance(expr, dict):
                return {}, f"参数 {name} 的表达式不合法"
            if "lit" in expr:
                args[name] = expr["lit"]
            elif "snapshot" in expr:
                found, value = _snapshot_value(snapshot, function, str(expr["snapshot"]))
                if not found:
                    if expr.get("optional"):
                        continue  # 可选参数留空：省略，让函数用自己的默认值
                    return {}, f"快照里找不到字段 {expr['snapshot']}"
                args[name] = value
            elif "run" in expr:
                found, value = self._lookup_run(
                    db, instance, token, str(expr["run"]), str(expr.get("path") or "")
                )
                if not found:
                    if expr.get("optional"):
                        continue
                    return {}, f"找不到节点 {expr['run']} 的输出 {expr.get('path')}"
                args[name] = value
            else:
                return {}, f"参数 {name} 的表达式不合法"
        return args, None

    def _lookup_run(self, db, instance, token, node_id: str, path: str) -> tuple[bool, object]:
        run = db.scalars(
            select(models.Run)
            .where(
                models.Run.instance_id == instance.id,
                models.Run.node_id == node_id,
                models.Run.status == "ok",
                models.Run.generation <= token.generation,
            )
            .order_by(models.Run.generation.desc(), models.Run.started_at.desc())
            .limit(1)
        ).first()
        if run is None:
            return False, None
        root = _loads(run.output_json)
        parts = [p for p in path.split(".") if p]
        if parts and parts[0] == "output":
            parts = parts[1:]
        elif parts and parts[0] == "input":
            root = _loads(run.input_json)
            parts = parts[1:]
        return _lookup(root, ".".join(parts)) if parts else (True, root)

    def _snapshot_payload(self, db, instance) -> dict:
        if not instance.snapshot_id:
            return {}
        snapshot = db.get(models.Snapshot, instance.snapshot_id)
        if snapshot is None:
            return {}
        payload = _loads(snapshot.payload_json)
        return payload if isinstance(payload, dict) else {"value": payload}

    def _project(self, db, ref: str):
        if not ref:
            return None
        project = db.get(models.Project, ref)
        if project is None:
            project = db.scalars(select(models.Project).where(models.Project.name == ref)).first()
        return project

    # ------------------------------------------------------------ 令牌与后继

    def _tokens(self, db, instance_id: str) -> list:
        return list(
            db.scalars(
                select(models.Token)
                .where(models.Token.instance_id == instance_id)
                .order_by(models.Token.sort_order, models.Token.created_at)
            )
        )

    def _spawn_successors(self, db, instance, plan: dict, token, step: dict, branch: str) -> list:
        targets = (step.get("next") or {}).get(branch) or []
        if not targets:
            return []
        return self._create_tokens(
            db, instance, plan, token, targets, branch, token.generation
        )

    def _create_tokens(
        self, db, instance, plan: dict, parent, targets: list[str], branch: str, generation: int
    ) -> list:
        """串行把后继插到父令牌之后（深度优先），并行接到队尾。"""
        if plan.get("mode") == "serial":
            order = parent.sort_order + 1
            queued = db.scalars(
                select(models.Token).where(
                    models.Token.instance_id == instance.id,
                    models.Token.status == "queued",
                    models.Token.sort_order > parent.sort_order,
                )
            ).all()
            for token in queued:
                token.sort_order += len(targets)
        else:
            order = self._next_order(db, instance.id)

        stamp = now_iso()
        created = []
        for index, node_id in enumerate(targets):
            child = models.Token(
                id=new_id(),
                instance_id=instance.id,
                node_id=node_id,
                status="queued",
                branch=None if branch == "default" else branch,
                sort_order=order + index,
                generation=generation,
                parent_token_id=parent.id,
                created_at=stamp,
                updated_at=stamp,
            )
            db.add(child)
            created.append(child)
        db.flush()
        return created

    def _ensure_entry_token(self, db, instance, plan: dict) -> None:
        existing = db.scalar(
            select(func.count(models.Token.id)).where(models.Token.instance_id == instance.id)
        )
        if existing:
            return
        entry = plan.get("entry")
        if not entry:
            self._fail(db, instance, ERR_MISSING_STEP, "步骤表缺少入口节点")
            return
        stamp = now_iso()
        db.add(
            models.Token(
                id=new_id(),
                instance_id=instance.id,
                node_id=entry,
                status="queued",
                sort_order=1,
                generation=1,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        db.flush()

    def _next_order(self, db, instance_id: str) -> int:
        current = db.scalar(
            select(func.max(models.Token.sort_order)).where(models.Token.instance_id == instance_id)
        )
        return int(current or 0) + 1

    # ------------------------------------------------------------ 待办

    def _ensure_review(self, db, instance, token, step: dict):
        existing = db.scalars(
            select(models.Review).where(
                models.Review.token_id == token.id, models.Review.status == "pending"
            )
        ).first()
        if existing is not None:
            return existing
        meta = self._node_meta(db, instance, token.node_id)
        kind = "decision" if step.get("kind") == "investigation" else "action"
        request = {
            "title": meta.get("title") or token.node_id,
            "detail": meta.get("detail") or "",
            "attachments": meta.get("attachments") or [],
            "nodeKind": step.get("kind"),
        }
        if kind == "decision":
            request["instructions"] = "按节点的判定依据选择「是」或「否」"
        elif step.get("kind") == "jump":
            # 转交是「把问题交出去」，不是在本流程里做一个动作，提示要区分开
            request["instructions"] = "转交给对应链路后选「完成」；转交不出去请回退"
        else:
            request["instructions"] = "完成动作后选「完成」；没做成请回退"
        review = models.Review(
            id=new_id(),
            instance_id=instance.id,
            token_id=token.id,
            node_id=token.node_id,
            kind=kind,
            status="pending",
            request_json=json.dumps(request, ensure_ascii=False),
            created_at=now_iso(),
        )
        db.add(review)
        db.flush()
        return review

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
        self._close_session(instance.id)

    # ------------------------------------------------------------ 实例状态

    def _refresh(self, db, instance, plan: dict) -> None:
        if instance.status in TERMINAL_INSTANCE:
            return
        tokens = self._tokens(db, instance.id)
        statuses = [t.status for t in tokens]
        success_token = self._success_token(tokens, plan)

        def apply(status: str) -> None:
            if instance.status != status:
                logger.info("实例 %s 状态 %s -> %s", instance.id, instance.status, status)
            instance.status = status

        if "running" in statuses:
            apply("running")
            return
        if "waiting" in statuses:
            apply("waiting")
            return
        if "queued" in statuses:
            if plan.get("mode") == "serial" and success_token is not None:
                # 同一个成功令牌只问一次；选过「继续」就不再拦
                review = self._ensure_after_success_review(db, instance, success_token)
                if review is not None:
                    apply("paused")
                    return
            apply("running")
            return
        if success_token is not None:
            apply("success")
        elif self._reached(tokens, plan, "escalation") is not None:
            # 上升是交给人接手，不是失败；有成功路径时仍算 success
            apply("escalated")
        else:
            apply("failed")
        instance.ended_at = instance.ended_at or now_iso()
        logger.info(
            "实例 %s 结束：%s，令牌 %s",
            instance.id,
            instance.status,
            [(t.node_id, t.status) for t in tokens],
        )

    def _success_token(self, tokens: list, plan: dict):
        return self._reached(tokens, plan, "problemSolved")

    def _reached(self, tokens: list, plan: dict, kind: str):
        steps = plan.get("steps") or {}
        for token in tokens:
            if token.status != "done":
                continue
            step = steps.get(token.node_id) or {}
            if step.get("kind") == kind:
                return token
        return None

    def _fail(self, db, instance, code: str, message: str) -> None:
        token = db.scalars(
            select(models.Token)
            .where(models.Token.instance_id == instance.id)
            .order_by(models.Token.created_at)
            .limit(1)
        ).first()
        self._write_run(
            db,
            instance,
            token,
            status="failed",
            error=f"{code}: {message}",
            started=now_iso(),
        )
        instance.status = "failed"
        instance.ended_at = instance.ended_at or now_iso()

    def _fail_token(self, db, instance, token) -> None:
        token.status = "failed"
        token.updated_at = now_iso()
        db.flush()

    # ------------------------------------------------------------ 回退

    def _rollback(self, db, instance, plan: dict, target: str, branch: str, reason) -> None:
        if instance.status in TERMINAL_INSTANCE:
            raise ValueError("实例已结束，不能回退")
        step = (plan.get("steps") or {}).get(target)
        if step is None:
            raise ValueError("目标节点不在该发布版本里")
        branch_key = branch if step.get("kind") == "investigation" else "default"
        if step.get("kind") == "investigation" and branch_key not in ("yes", "no"):
            raise ValueError("回退分支必须是是/否")
        if not (step.get("next") or {}).get(branch_key):
            raise ValueError(f"目标节点没有 {branch_key} 分支的后继")

        tokens = self._tokens(db, instance.id)
        latest_generation = max((t.generation for t in tokens), default=1)
        target_token = self._executed_token(db, instance, plan, tokens, target, latest_generation)
        if target_token is None:
            raise ValueError("目标节点在当前代没有执行记录，不能回退")

        descendants = _descendants(tokens, target_token.id)
        # 目标就是正在等人工的节点：没有后继可回退，按「重做该节点」处理
        redo_self = not descendants and target_token.status == "waiting"
        if not descendants and not redo_self:
            raise ValueError("目标节点之后没有可回退的路径")
        blocked = [
            t
            for t in tokens
            if t.status in ("queued", "running", "waiting")
            and t.id not in descendants
            and t.id != target_token.id
        ]
        if blocked:
            raise ValueError("还有其他活跃路径，请先处理完再回退")

        stamp = now_iso()
        for token in tokens:
            if token.id in descendants and token.status not in ("failed", "abandoned"):
                token.status = "abandoned"
                token.updated_at = stamp
            elif redo_self and token.id == target_token.id:
                token.status = "abandoned"
                token.updated_at = stamp
            elif instance.status == "paused" and token.status == "queued":
                token.status = "abandoned"
                token.updated_at = stamp

        abandoned = set(descendants) | {t.id for t in tokens if t.status == "abandoned"}
        for run in db.scalars(
            select(models.Run).where(
                models.Run.instance_id == instance.id, models.Run.token_id.in_(abandoned)
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

        new_generation = max(latest_generation, target_token.generation) + 1
        logger.info(
            "实例 %s 回退到 %s（%s）：%s", instance.id, target, branch_key, reason or "未填原因"
        )
        parent = SimpleNamespace(id=target_token.id, sort_order=target_token.sort_order)
        if redo_self:
            # 重新排一个目标节点的令牌，等人工再做一次
            self._create_tokens(db, instance, plan, parent, [target], "default", new_generation)
        else:
            self._create_tokens(
                db, instance, plan, parent, step["next"][branch_key], branch_key, new_generation
            )

        instance.status = "running"
        instance.ended_at = None

    def _executed_token(self, db, instance, plan: dict, tokens: list, node_id: str, generation: int):
        """目标节点「执行过」的判定。

        没有 call 的节点（结论、人工）按设计不产生 run，令牌走过即算执行；
        有 call 的节点必须留下一条 ok 的 run，避免回退到根本没跑成功的代码节点。
        """
        step = (plan.get("steps") or {}).get(node_id) or {}
        needs_run = bool(step.get("call"))
        candidates = [
            t
            for t in tokens
            if t.node_id == node_id
            and t.generation <= generation
            and t.status in ("done", "waiting", "running")
        ]
        candidates.sort(key=lambda t: (t.generation, t.created_at or ""), reverse=True)
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

    # ------------------------------------------------------------ 杂项

    def _plan(self, db, instance) -> dict:
        release = db.get(models.Release, instance.release_id)
        if release is None:
            return {}
        plan = _loads(release.plan_json)
        return plan if isinstance(plan, dict) else {}

    def _stored_signatures(self, db, instance) -> dict:
        release = db.get(models.Release, instance.release_id)
        if release is None:
            return {}
        stored = _loads(release.signature_json)
        return stored if isinstance(stored, dict) else {}

    def _node_meta(self, db, instance, node_id: str) -> dict:
        release = db.get(models.Release, instance.release_id)
        if release is None:
            return {}
        revision = db.get(models.Revision, release.revision_id)
        if revision is None:
            return {}
        doc = _loads(revision.json) or {}
        for node in doc.get("nodes") or []:
            if node.get("id") == node_id:
                return node
        return {}

    def _write_run(
        self,
        db,
        instance,
        token,
        *,
        status: str,
        args=None,
        output=None,
        evidence=None,
        error: str | None = None,
        started: str | None = None,
        node_id: str | None = None,
        attempt: int = 1,
    ):
        stamp = started or now_iso()
        run = models.Run(
            id=new_id(),
            instance_id=instance.id,
            token_id=token.id if token is not None else None,
            node_id=node_id or (token.node_id if token is not None else "__instance__"),
            generation=token.generation if token is not None else 1,
            attempt=attempt,
            status=status,
            input_json=_dumps(args),
            output_json=_dumps(output),
            evidence_json=_dumps(evidence),
            error=error,
            started_at=stamp,
            ended_at=now_iso(),
        )
        db.add(run)
        db.flush()
        return run

    def _close_session(self, instance_id: str) -> None:
        try:
            self._sessions.close(instance_id)
        except Exception:  # noqa: BLE001 - 关闭失败不影响实例结论
            logger.warning("实例 %s 会话关闭失败", instance_id, exc_info=True)


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


def _snapshot_value(snapshot: dict, function: str, name: str) -> tuple[bool, object]:
    """启动参数取值：该函数的作用域 > 全局 > 旧扁平载荷。

    新快照形如 {"global": {...}, "scope": {"judge_voltage": {...}}}，
    旧快照是扁平 {"voltage": 12}，两种都要能取到。
    """
    scope = snapshot.get("scope")
    if function and isinstance(scope, dict):
        section = scope.get(function)
        if isinstance(section, dict) and name in section:
            return True, section[name]

    globals_ = snapshot.get("global")
    if isinstance(globals_, dict):
        found, value = _lookup(globals_, name)
        if found:
            return True, value

    layered = isinstance(globals_, dict) or isinstance(scope, dict)
    if layered and name.split(".", 1)[0] in ("global", "scope"):
        return False, None  # 分层载荷里这两层不是字段名，避免整层被打包传进函数
    return _lookup(snapshot, name)


def _lookup(root, path: str) -> tuple[bool, object]:
    current = root
    if not path:
        return True, current
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current


def _branch_of(step: dict, value) -> str | None:
    if step.get("kind") != "investigation":
        return "default"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, str):
        text = value.strip().lower()
        if text in YES_VALUES:
            return "yes"
        if text in NO_VALUES:
            return "no"
    return None


def _opposite(branch: str | None) -> str:
    return "yes" if branch == "no" else "no"


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


scheduler = Scheduler(SessionLocal)

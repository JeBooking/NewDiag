"""双策略调度循环（spec 第 6 节）。

每个实例一个 daemon 线程；线程内每次只开一个短事务，不跨线程共享 Session。
人工节点把实例推到 waiting/paused 后线程自然退出，由 submit_review/rollback 重新拉起。
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
import threading
import time
from functools import wraps
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import db as db_module
from app import models
from app.db import SessionLocal, new_id, now_iso
from app.logging_setup import brief
from app.services import signatures, storage
from app.services.sessions import SessionCrashed, SessionError, SessionManager, SessionTimeout
from app.worker.function_pipeline import PipelineFailure, execute_pipeline, persisted_state
from app.worker import artifacts

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

# 业务生成的图片只登记位图：文件名由摘要加白名单扩展名组成，其它一律不认。
GENERATED_FILE_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpe?g|webp|gif)$")
GENERATED_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                   "webp": "image/webp", "gif": "image/gif"}


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
            if request.get("type") in ("acquisition", "judgment"):
                if request["type"] == "acquisition" and result != "done":
                    raise ValueError("提供数据待办只能提交 done")
                if request["type"] == "judgment" and result not in ("yes", "no"):
                    raise ValueError("判断待办只能选择是或否")
                if request["type"] == "judgment" and not str(payload.get("reason") or "").strip():
                    raise ValueError("请填写判断依据")
                operation = request["type"]
                runner = (self._resume_diagnosis if step.get("diagnosisMethod")
                          else self._resume_recipe if step.get("recipe")
                          else self._invoke_functions if step.get("pipeline")
                          else self._invoke_pipeline)
                configuration = step.get("recipe") or step.get("pipeline") or step.get("call") or {}
                value, failure = runner(
                    db, instance, token, configuration, request["pipeline"], operation,
                    data=payload.get("data"),
                    decision={"result": result, "reason": payload.get("reason")},
                )
                if self._is_cancelled(instance_id):
                    raise ValueError("实例正在终止，提交结果已丢弃")
                if failure is not None:
                    raise ValueError(f"{failure[0]}: {failure[1]}")
                if step.get("recipe"):
                    self._accept_recipe(db, instance, plan, token, step, value)
                else:
                    self._accept_pipeline(db, instance, plan, token, step, value)
                self._refresh(db, instance, plan)
                db.commit()
                if instance.status not in TERMINAL_INSTANCE:
                    self.start(instance_id)
                else:
                    self._close_session(instance_id)
                return
            if result == "rollback":
                branch = str(payload.get("branch") or _opposite(token.branch))
                target = str(payload.get("target") or token.node_id)
                redo_self = target == token.node_id
                self._rollback(
                    db, instance, plan, target, branch, payload.get("reason"),
                    target_token_id=token.id if redo_self else None,
                    mode="retry" if redo_self else "branch",
                    protect_other_paths=True,
                )
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
    def rollback(
        self, instance_id: str, target: str | None, branch: str, reason: str | None,
        *, target_token_id: str | None = None, mode: str = "branch",
    ) -> None:
        if self._is_cancelled(instance_id):
            raise ValueError("实例已终止，不能回退")
        db = self._open()
        try:
            instance = db.get(models.Instance, instance_id)
            if instance is None:
                raise ValueError("实例不存在")
            plan = self._plan(db, instance)
            self._rollback(
                db, instance, plan, target, branch, reason,
                target_token_id=target_token_id, mode=mode,
            )
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
            restart = False
            with self._lock:
                current = self._threads.get(instance_id)
                if current is threading.current_thread():
                    self._threads.pop(instance_id, None)
                    # 异常收尾期间失败实例也可能已回退，要交接新一代的唤醒。
                    restart = instance_id in self._wake
                    self._wake.discard(instance_id)
            if restart:
                self.start(instance_id)

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
            if finished_runs or (not plan.get("diagnosis") and not any(
                step.get("call") or step.get("pipeline") or step.get("recipe")
                for step in plan["steps"].values()
            )):
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
            if instance.status in TERMINAL_INSTANCE or (
                plan.get("diagnosis") and instance.status in ("waiting", "paused")
            ):
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
        token.visited_at = token.visited_at or datetime.now(timezone.utc).isoformat()
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
        if plan.get("diagnosis") and step.get("kind") != "start":
            reference = self._diagnosis_state(db, instance.id, token.generation)
            if reference is not None:
                self._save_diagnosis_state(db, instance, token, reference)

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
            if plan.get("diagnosis"):
                self._close_session(instance.id)
            logger.info(
                "实例 %s 等待人工：节点 %s（%s）", instance.id, token.node_id, step.get("kind")
            )
            return

        if plan.get("diagnosis") and step.get("kind") == "start":
            db.commit()
            self._run_diagnosis(db, instance, plan, token, step, initialize=True)
            return
        if step.get("diagnosisMethod"):
            db.commit()
            self._run_diagnosis(db, instance, plan, token, step)
            return

        if step.get("recipe"):
            db.commit()
            self._run_recipe(db, instance, plan, token, step)
            return

        if step.get("pipeline"):
            db.commit()
            self._run_functions(db, instance, plan, token, step)
            return
        call = step.get("call")
        if call:
            # 长耗时调用开始前持久化到访，让执行轨迹能显示正在运行的节点。
            db.commit()
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
        if call.get("protocol") == "investigation/v1":
            self._run_pipeline(db, instance, plan, token, step, call)
            return
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

    def _diagnosis_state(self, db, instance_id, generation):
        saved = db.scalars(select(models.DiagnosisState).where(
            models.DiagnosisState.instance_id == instance_id,
            models.DiagnosisState.generation == generation,
        ).order_by(models.DiagnosisState.seq.desc())).first()
        return _loads(saved.reference_json) if saved else None

    def _save_diagnosis_state(self, db, instance, token, reference, *, generation=None):
        if not artifacts.is_reference(reference):
            raise ValueError("诊断对象返回了无效的数据版本")
        db.add(models.DiagnosisState(
            id=new_id(), instance_id=instance.id,
            generation=generation if generation is not None else token.generation,
            # 平台时间戳精确到秒，随机 id 不能代表同秒多次写入的先后。
            seq=(db.scalar(select(func.max(models.DiagnosisState.seq)).where(
                models.DiagnosisState.instance_id == instance.id,
            )) or 0) + 1,
            token_id=token.id if token else None, reference_json=_dumps(reference),
            created_at=now_iso(),
        ))
        db.flush()

    def _diagnosis_visit_state(self, db, instance, token):
        seen = set()
        while token and token.id not in seen:
            seen.add(token.id)
            saved = db.scalars(select(models.DiagnosisState).where(
                models.DiagnosisState.instance_id == instance.id,
                models.DiagnosisState.token_id == token.id,
            ).order_by(models.DiagnosisState.seq.desc())).first()
            if saved:
                return _loads(saved.reference_json)
            token = db.get(models.Token, token.parent_token_id) if token.parent_token_id else None
        return None

    def _run_diagnosis(self, db, instance, plan, token, step, *, initialize=False):
        binding = plan["diagnosis"]
        method = step.get("diagnosisMethod") or {}
        function = binding["function"] + ("." + method["name"] if method else "")
        args, error = self._eval_args(db, instance, token,
                                     binding["args"] if initialize else method["args"], function)
        started = now_iso()
        reference = None if initialize else self._diagnosis_state(db, instance.id, token.generation)
        state = {"inputs": args, "stages": [], "startedAt": started,
                 "diagnosisClass": binding["function"], "diagnosisVersion": binding["version"]}
        value, failure = None, None
        if error:
            failure = (ERR_ARGUMENT, error, 1)
        else:
            request = {"class": binding["function"], "version": binding["version"],
                       "config": binding.get("config") or {}, "definition": binding["definition"],
                       "artifactRoot": str(self._artifact_root(instance.id)),
                       "fileRoot": str(self._file_root()), "state": reference,
                       "operation": "initialize" if initialize else "method",
                       "inputs": args if initialize else {}, "method": method.get("name"),
                       "arguments": args}
            value, failure = self._invoke(db, instance, {
                "project": binding["project"], "function": "__newdiag_diagnosis__",
            }, request, token)
        if self._is_cancelled(instance.id):
            return
        if value and value.get("state"):
            state["diagnosisState"] = value["state"]
            self._save_diagnosis_state(db, instance, token, value["state"])
        if value and value.get("error"):
            failure = (ERR_EXCEPTION, value["error"], 1)
        if failure:
            self._write_run(db, instance, token, status="failed", args=args,
                            error=f"{failure[0]}: {failure[1]}", started=started,
                            attempt=failure[2], evidence={"pipeline": persisted_state(state)})
            self._fail_token(db, instance, token)
            return
        if initialize:
            self._write_run(db, instance, token, status="ok", args=args,
                            output={"initialized": True}, started=started,
                            evidence={"pipeline": persisted_state(state)})
            self._spawn_successors(db, instance, plan, token, step, "default")
            token.status, token.updated_at = "done", now_iso()
            db.flush()
            return
        result = value["result"]
        state.update({"presentation": result["presentation"], "parsed": result["data"],
                      "decision": {"result": result["result"], "reason": result["reason"]}})
        self._accept_pipeline(db, instance, plan, token, step, {
            "status": "judgment" if result["result"] is None else "complete", "state": state,
        })

    def _resume_diagnosis(self, db, instance, token, configuration, state, operation, **extra):
        state = dict(state)
        state["decision"] = extra["decision"]
        state["stages"] = [*state.get("stages", []), {"stage": "manualJudgment"}]
        return {"status": "complete", "state": state}, None

    # ------------------------------------------------------------ 能力配方

    def _run_recipe(self, db, instance, plan, token, step):
        """一个节点内按声明顺序调用现成库；节点之间只经过 JSON。"""
        recipe = step["recipe"]
        started = now_iso()
        request, error = self._eval_recipe(db, instance, token, recipe)
        if error is not None:
            logger.warning("实例 %s 节点 %s 配方参数求值失败：%s", instance.id, token.node_id, error)
            self._write_run(
                db, instance, token, status="failed", error=f"{ERR_ARGUMENT}: {error}", started=started,
            )
            self._fail_token(db, instance, token)
            return

        request.update({
            "project": recipe["project"],
            "artifactRoot": str(self._artifact_root(instance.id)),
            "fileRoot": str(self._file_root()),
        })
        call = {"project": recipe["project"], "function": "__newdiag_library__"}
        value, failure = self._invoke(db, instance, call, request, token)
        if self._is_cancelled(instance.id):
            self._write_run(
                db, instance, token, status="abandoned", args=request.get("steps"),
                error="execution/terminated: 用户终止诊断", started=started,
            )
            return
        if failure is not None:
            code, message, attempts = failure
            logger.warning("实例 %s 节点 %s 调用配方失败（%s）：%s",
                           instance.id, token.node_id, code, message)
            self._write_run(
                db, instance, token, status="failed", args=request.get("steps"),
                error=f"{code}: {message}", started=started, attempt=attempts,
                evidence={"project": recipe["project"]},
            )
            self._fail_token(db, instance, token)
            return
        if not isinstance(value, dict) or value.get("status") not in ("complete", "judgment"):
            self._write_run(
                db, instance, token, status="failed", args=request.get("steps"),
                error=f"{ERR_EXCEPTION}: 配方返回了无效结果 {value!r}", started=started,
            )
            self._fail_token(db, instance, token)
            return

        state = {
            "output": value.get("output") or {},
            "presentation": value.get("presentation"),
            "result": value.get("result"),
            "reason": value.get("reason") or "",
            "steps": value.get("steps") or [],
            "notes": value.get("notes") or [],
            "project": recipe["project"],
            "inputs": request.get("steps"),
            "startedAt": started,
        }
        self._accept_recipe(db, instance, plan, token, step, {"status": value["status"], "state": state})

    def _eval_recipe(self, db, instance, token, recipe: dict):
        """只求平台该求的值：启动参数与上游输出；对象和文件路径留给业务进程。"""
        steps = []
        for index, item in enumerate(recipe.get("steps") or []):
            args, error = self._eval_recipe_value(
                db, instance, token, item.get("args") or {}, f"第 {index + 1} 步参数",
            )
            if error is not None:
                return None, error
            steps.append({**{key: value for key, value in item.items() if key != "args"}, "args": args})
        if not steps:
            return None, "配方没有可执行的步骤"
        return {
            "steps": steps,
            "out": recipe.get("out") or {},
            "result": recipe.get("result"),
            "reason": recipe.get("reason"),
        }, None

    def _eval_recipe_value(self, db, instance, token, value, where: str):
        if isinstance(value, list):
            result = []
            for item in value:
                resolved, error = self._eval_recipe_value(db, instance, token, item, where)
                if error is not None:
                    return None, error
                result.append(resolved)
            return result, None
        if not isinstance(value, dict):
            return value, None

        keys = list(value)
        kind = keys[0] if keys and isinstance(keys[0], str) else ""
        if not kind.startswith("$"):
            result = {}
            for key, item in value.items():
                resolved, error = self._eval_recipe_value(db, instance, token, item, f"{where}.{key}")
                if error is not None:
                    return None, error
                result[key] = resolved
            return result, None
        if kind == "$snapshot":
            name = str(value.get("$snapshot") or "")
            found, raw = _snapshot_value(self._snapshot_payload(db, instance), "", name)
            if not found:
                return None, f"{where} 的启动参数 {name} 没有值"
            try:
                return {"$value": _cast(raw, value.get("cast"))}, None
            except (TypeError, ValueError) as exc:
                return None, f"{where} 的启动参数 {name} 无法转换成 {value.get('cast')}：{exc}"
        if kind == "$run":
            found, raw = self._lookup_run(
                db, instance, token, str(value.get("$run")), str(value.get("path") or "")
            )
            if not found:
                return None, f"{where} 找不到节点 {value.get('$run')} 的输出 {value.get('path')}"
            if artifacts.is_reference(raw):
                try:
                    raw = artifacts.load(self._artifact_root(instance.id), raw)
                except (ValueError, OSError) as exc:
                    return None, f"{where} 读取共享数据失败：{exc}"
            return {"$value": raw}, None
        # $var / $prev / $file 由业务进程求值，这里原样带过去
        return value, None

    def _accept_recipe(self, db, instance, plan, token, step, value) -> None:
        state = value["state"]
        is_decision = step.get("kind") == "investigation"
        presentation = self._presentation_with_evidence(
            db, instance, token, step.get("recipe") or {}, state.get("presentation")
        )
        if value["status"] == "judgment" and is_decision:
            meta = self._node_meta(db, instance, token.node_id)
            db.add(models.Review(
                id=new_id(), instance_id=instance.id, token_id=token.id, node_id=token.node_id,
                kind="decision", status="pending", created_at=now_iso(),
                request_json=_dumps({
                    "title": meta.get("title") or token.node_id,
                    "detail": meta.get("detail") or "", "nodeKind": step["kind"],
                    "type": "judgment",
                    "instructions": "结合图表与依据选择是或否，并填写判断依据",
                    "presentation": presentation,
                    "evidence": state.get("output"),
                    "pipeline": {**state, "presentation": presentation},
                    "attachments": meta.get("attachments") or [],
                }),
            ))
            token.status = "waiting"
            token.updated_at = now_iso()
            db.flush()
            logger.info("实例 %s 节点 %s 等待人工判断", instance.id, token.node_id)
            return

        decision = state.get("decision") or {
            "result": state.get("result"), "reason": state.get("reason"),
        }
        branch = decision.get("result") if is_decision else "default"
        output = dict(state.get("output") or {})
        if is_decision:
            output.update({
                "result": branch, "reason": decision.get("reason") or state.get("reason") or "",
            })
        error = None
        if is_decision and branch not in ("yes", "no"):
            error = f"{ERR_ROUTE}: 判定结果是 {branch!r}"
        elif not (step.get("next") or {}).get(branch):
            error = f"{ERR_ROUTE}: 分支 {branch} 没有后继节点"
        self._write_run(
            db, instance, token, status="failed" if error else "ok",
            args=state.get("inputs"), output=output, error=error,
            started=state.get("startedAt"),
            evidence={
                "branch": branch, "presentation": presentation,
                "steps": state.get("steps"), "project": state.get("project"),
                "notes": state.get("notes") or [],
            },
        )
        if error:
            logger.warning("实例 %s 节点 %s 无法路由：%s", instance.id, token.node_id, error)
            self._fail_token(db, instance, token)
            return
        self._spawn_successors(db, instance, plan, token, step, branch)
        token.status = "done"
        token.updated_at = now_iso()
        db.flush()

    def _presentation_with_evidence(self, db, instance, token, recipe: dict, presentation) -> dict:
        """recipe.evidence 指向上游节点时，把它那次到访的图和数据一起带进来。

        判断类待办如果只写文字，人得自己去执行轨迹里翻上一张图；把上游展示拼进来，
        待办面板本身就是完整的证据链。
        """
        blocks = list(((presentation or {}).get("blocks") or []))
        source = recipe.get("evidence")
        if not source:
            return {"blocks": blocks}
        run = self._ancestor_run(db, instance, token, str(source))
        if run is None:
            logger.warning("实例 %s 节点 %s 找不到上游 %s 的展示", instance.id, token.node_id, source)
            return {"blocks": blocks}
        evidence = _loads(run.evidence_json) or {}
        upstream = ((evidence.get("presentation") or {}).get("blocks") or [])
        merged = [block for block in upstream if isinstance(block, dict)][:6]
        return {"blocks": [*merged, *blocks][:12]}

    def _ancestor_run(self, db, instance, token, node_id: str):
        parent_id = token.parent_token_id
        seen: set[str] = set()
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = db.get(models.Token, parent_id)
            if parent is None or parent.instance_id != instance.id:
                break
            if parent.node_id == node_id:
                return self._successful_run(db, instance.id, parent.id)
            parent_id = parent.parent_token_id
        return None

    def _resume_recipe(self, db, instance, token, configuration, state, operation, **extra):
        state = dict(state)
        state["decision"] = extra["decision"]
        state["steps"] = [*state.get("steps", []), {"stage": "manualJudgment"}]
        return {"status": "complete", "state": state}, None

    def _run_functions(self, db, instance, plan, token, step):
        pipeline = step["pipeline"]
        function = (pipeline.get("acquire") or {}).get("function", "")
        args, error = self._eval_args(db, instance, token, pipeline["args"], function)
        state = {"inputs": args, "stages": [], "startedAt": now_iso()}
        snapshot = self._snapshot_payload(db, instance)
        context = snapshot.get("global", snapshot)
        local = (snapshot.get("scope") or {}).get(function, {})
        state["inputContext"] = {**context, **local, **args}
        if not error and pipeline.get("dataSource"):
            source = pipeline["dataSource"]
            found, reference = self._lookup_run(db, instance, token, source["node"], "output.data")
            if not found or not artifacts.is_reference(reference):
                error = "当前诊断路径上没有可复用的数据准备结果"
            else:
                state["parsed"] = artifacts.select(reference, source.get("path", ""))
                state["dataSource"] = {**source, "artifact": state["parsed"]}
                logger.info("实例 %s 节点 %s 复用数据准备 %s 产物 %s", instance.id, token.node_id,
                            source["node"], reference["$artifact"])
        if error:
            value, failure = None, (ERR_ARGUMENT, error, 1)
        else:
            value, failure = self._invoke_functions(db, instance, token, pipeline, state, "start")
        if self._is_cancelled(instance.id):
            return
        if failure:
            self._write_run(db, instance, token, status="failed", args=args,
                            error=f"{failure[0]}: {failure[1]}", attempt=failure[2],
                            evidence={"adapter": "平台四阶段函数",
                                      "pipeline": persisted_state(state)})
            self._fail_token(db, instance, token)
        else:
            self._accept_pipeline(db, instance, plan, token, step, value)

    def _invoke_functions(self, db, instance, token, pipeline, state, operation, **extra):
        def invoke(binding, args):
            shared = pipeline.get("preparation") or pipeline.get("dataSource")
            request = {"function": binding["function"], "signature": binding["signature"],
                       "arguments": args, "fileRoot": str(self._file_root())}
            if shared:
                request["artifactRoot"] = str(self._artifact_root(instance.id))
                request["artifactArguments"] = {
                    name: value for name, value in args.items()
                    if artifacts.is_reference(value)
                    and (binding.get("arguments", {}).get(name) or {}).get("source") in ("raw", "parsed")
                }
                request["storeArtifact"] = binding.get("_stage") in ("acquire", "parse")
            call = {"project": binding["project"], "function": "__newdiag_function__"}
            value, failure = self._invoke(db, instance, call, request, token)
            if failure:
                raise PipelineFailure(failure)
            return value

        try:
            if operation == "acquisition":
                extra["data"] = self._resolve_manual_data(db, state, extra.get("data"))
            value = execute_pipeline(pipeline, state, operation, invoke, **extra)
            return value, None
        except PipelineFailure as exc:
            return None, exc.failure
        except (ValueError, TypeError, KeyError) as exc:
            return None, (ERR_EXCEPTION, str(exc), 1)

    def _artifact_root(self, instance_id):
        # 按实例分目录，业务返回的标识不能访问另一实例的数据。
        return db_module.data_dir() / "artifacts" / hashlib.sha256(instance_id.encode()).hexdigest()

    def _file_root(self):
        # 与 storage.files_dir() 同一口径；目录由 worker 自己按需创建
        return db_module.data_dir() / "files"

    def _resolve_manual_data(self, db, state, data):
        if not isinstance(data, dict):
            return data
        from app.services.storage import resolve

        result = dict(data)
        for field in state.get("manualFields", []):
            if field.get("type") != "file" or field["name"] not in result:
                continue
            ref = result[field["name"]]
            digest = ref.get("hash") if isinstance(ref, dict) else None
            path = resolve(digest) if isinstance(digest, str) else None
            if path is None or db.get(models.FileBlob, digest) is None:
                raise ValueError(f"文件 {field['name']} 不存在，请重新上传")
            result[field["name"]] = {"hash": digest, "path": str(path.resolve())}
        return result

    def _run_pipeline(self, db, instance, plan, token, step, call) -> None:
        args, error = self._eval_args(
            db, instance, token, call.get("args") or {}, str(call.get("function") or "")
        )
        if error:
            value, failure = None, (ERR_ARGUMENT, error, 1)
        else:
            state = {"inputs": args, "stages": [], "startedAt": now_iso()}
            value, failure = self._invoke_pipeline(db, instance, token, call, state, "start")
        if self._is_cancelled(instance.id):
            return
        if failure:
            self._write_run(db, instance, token, status="failed", args=args,
                            error=f"{failure[0]}: {failure[1]}", attempt=failure[2],
                            evidence={"adapter": call["function"], "version": call["version"]})
            self._fail_token(db, instance, token)
            return
        self._accept_pipeline(db, instance, plan, token, step, value)

    def _invoke_pipeline(self, db, instance, token, call, state, operation, **extra):
        request = {
            "adapter": call["function"], "version": call["version"],
            "config": call.get("config") or {}, "state": state,
            "operation": operation, "fileRoot": str(self._file_root()), **extra,
        }
        # 文件路径由存储层解析，不能把客户端提供的路径交给业务模块打开。
        if operation == "acquisition" and isinstance(request.get("data"), dict):
            from app.services.storage import resolve

            data = dict(request["data"])
            for field in state.get("manualFields", []):
                if field.get("type") != "file" or field["name"] not in data:
                    continue
                ref = data[field["name"]]
                digest = ref.get("hash") if isinstance(ref, dict) else None
                path = resolve(digest) if isinstance(digest, str) else None
                if path is None or db.get(models.FileBlob, digest) is None:
                    return None, (ERR_ARGUMENT, f"文件 {field['name']} 不存在，请重新上传", 1)
                data[field["name"]] = {"hash": digest, "path": str(path.resolve())}
            request["data"] = data
        rpc_call = {"project": call["project"], "function": "__newdiag_pipeline__"}
        return self._invoke(db, instance, rpc_call, request, token)

    def _accept_pipeline(self, db, instance, plan, token, step, value) -> None:
        if not isinstance(value, dict) or value.get("status") not in ("acquisition", "judgment", "complete", "prepared"):
            raise ValueError("适配器返回了无效的阶段响应")
        state = value["state"]
        status = value["status"]
        call = step.get("call") or {"function": "平台四阶段函数", "version": "1"}
        if step.get("diagnosisMethod"):
            call = {"function": state["diagnosisClass"] + "." + step["diagnosisMethod"]["name"],
                    "version": state["diagnosisVersion"]}
        if status == "prepared":
            self._write_run(db, instance, token, status="ok", args=state["inputs"],
                            started=state.get("startedAt"), output={"data": state["parsed"]},
                            evidence={"pipeline": persisted_state(state), "prepared": True})
            self._spawn_successors(db, instance, plan, token, step, "default")
            token.status = "done"
            token.updated_at = now_iso()
            db.flush()
            return
        if status != "complete":
            meta = self._node_meta(db, instance, token.node_id)
            state["manualFields"] = value.get("fields") or state.get("manualFields") or []
            review = models.Review(
                id=new_id(), instance_id=instance.id, token_id=token.id, node_id=token.node_id,
                kind="decision", status="pending", created_at=now_iso(),
                request_json=_dumps({
                    "title": meta.get("title") or token.node_id,
                    "detail": meta.get("detail") or "", "nodeKind": step["kind"],
                    "type": status, "pipeline": persisted_state(state, include_parsed=True),
                    "fields": state["manualFields"] if status == "acquisition" else [],
                    "evidence": state.get("parsed"), "presentation": state.get("presentation"),
                    "attachments": meta.get("attachments") or [],
                    "instructions": "请提供数据，提交后执行解析和展示" if status == "acquisition"
                                    else "请根据展示数据选择是或否，并填写判断依据",
                }),
            )
            db.add(review)
            token.status = "waiting"
            token.updated_at = now_iso()
            db.flush()
            logger.info("实例 %s 节点 %s 等待人工阶段 %s", instance.id, token.node_id, status)
            self._close_session(instance.id)
            return
        decision = state["decision"]
        branch = decision["result"]
        error = None if (step.get("next") or {}).get(branch) else f"{ERR_ROUTE}: 分支 {branch} 没有后继节点"
        self._write_run(
            db, instance, token, status="failed" if error else "ok",
            args=state["inputs"], started=state.get("startedAt"), error=error,
            output={"data": state["parsed"], **decision},
            evidence={"branch": branch, "presentation": state["presentation"],
                      "pipeline": persisted_state(state), "adapter": call["function"],
                      "version": call["version"]},
        )
        if error:
            self._fail_token(db, instance, token)
        else:
            self._spawn_successors(db, instance, plan, token, step, branch)
            token.status = "done"
            token.updated_at = now_iso()
        db.flush()

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
                response = self._sessions.call(
                    instance.id, project, function, args, timeout=self._timeout
                )
                # SessionManager 返回 (value, files)；会话替身只返回值，按空清单处理
                value, files = response if isinstance(response, tuple) else (response, [])
                self._register_files(db, files)
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

    def _register_files(self, db, files) -> None:
        """把会话新落盘的图片登记成附件记录；登记失败只影响能否下载，不影响展示。"""
        if not files:
            return
        for item in files:
            try:
                digest = item.get("hash") if isinstance(item, dict) else None
                name = item.get("name") if isinstance(item, dict) else None
                if not isinstance(digest, str) or not storage.is_digest(digest):
                    logger.warning("忽略非法附件摘要：%r", digest)
                    continue
                if not isinstance(name, str) or not GENERATED_FILE_RE.fullmatch(name) \
                        or name[:64] != digest:
                    # 文件名会进下载响应头，只认「摘要 + 白名单扩展名」，别的一律不登记
                    logger.warning("忽略非法附件文件名：%r（摘要 %s）", name, digest)
                    continue
                path = storage.resolve(digest)
                if path is None:
                    logger.warning("附件记录指向的文件不存在：%s", digest)
                    continue
                if db.get(models.FileBlob, digest) is not None:
                    continue
                size = item.get("size")
                try:
                    with db.begin_nested():
                        db.add(models.FileBlob(
                            hash=digest,
                            filename=name,
                            media_type=GENERATED_MEDIA[name.rsplit(".", 1)[1].lower()],
                            size=size if isinstance(size, int) and size >= 0 else path.stat().st_size,
                            path=str(path),
                        ))
                except IntegrityError:
                    # 并发写同一张图：另一个会话先登记了，已有记录就够用
                    logger.info("附件记录已被并发写入，复用原记录：%s", digest)
            except Exception:  # noqa: BLE001 - 登记失败不能让展示跟着失败
                logger.exception("登记会话生成的附件失败：%r", item)

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
                if artifacts.is_reference(value):
                    try:
                        value = artifacts.load(self._artifact_root(instance.id), value)
                    except (ValueError, OSError) as exc:
                        return {}, f"参数 {name} 的共享数据读取失败：{exc}"
                args[name] = value
            else:
                return {}, f"参数 {name} 的表达式不合法"
        return args, None

    def _lookup_run(self, db, instance, token, node_id: str, path: str) -> tuple[bool, object]:
        # 回到旧代时只能继承所选到访的祖先，不能混入其他尝试的同名节点结果。
        parent_id = token.parent_token_id
        seen: set[str] = set()
        run = None
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = db.get(models.Token, parent_id)
            if parent is None or parent.instance_id != instance.id:
                break
            if parent.node_id == node_id:
                run = self._successful_run(db, instance.id, parent.id)
                break
            parent_id = parent.parent_token_id
        if run is None:
            return False, None
        root = _loads(run.output_json)
        parts = [p for p in path.split(".") if p]
        if parts and parts[0] == "output":
            parts = parts[1:]
        elif parts and parts[0] == "input":
            root = _loads(run.input_json)
            parts = parts[1:]
        for index, part in enumerate(parts):
            if artifacts.is_reference(root):
                try:
                    reference = artifacts.select(root, ".".join(parts[index:]))
                    return True, artifacts.load(self._artifact_root(instance.id), reference)
                except (ValueError, OSError):
                    return False, None
            found, root = _lookup(root, part)
            if not found:
                return False, None
        return True, root

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

    def _rollback(
        self, db, instance, plan: dict, target: str | None, branch: str, reason,
        *, target_token_id: str | None = None, mode: str = "branch", protect_other_paths: bool = False,
    ) -> None:
        if instance.status in ("success", "escalated", "terminated"):
            raise ValueError("实例已结束，不能回退")
        if mode not in ("branch", "retry"):
            raise ValueError("回退方式必须是 branch 或 retry")
        tokens = self._tokens(db, instance.id)
        by_id = {token.id: token for token in tokens}
        if target_token_id:
            target_token = by_id.get(target_token_id)
            if target_token is None:
                raise ValueError("目标到访不属于当前实例")
            if target and target != target_token.node_id:
                raise ValueError("目标节点与目标到访不一致")
        else:
            candidates = [t for t in tokens if t.node_id == target and t.visited_at]
            candidates.sort(key=lambda t: (t.visited_at, t.generation, t.id), reverse=True)
            target_token = candidates[0] if candidates else None
        if target_token is None or not target_token.visited_at:
            raise ValueError("目标到访尚未执行，不能回退")
        # 旧入口没有确认整实例回退，不能取消目标子树外的待办或排队分支。
        if not target_token_id or protect_other_paths:
            subtree = _descendants(tokens, target_token.id) | {target_token.id}
            if any(t.status in ("queued", "running", "waiting") and t.id not in subtree for t in tokens):
                raise ValueError("还有其他活跃路径，请先处理完再回退")
            if target_token.status == "waiting":
                mode = "retry"
        target = target_token.node_id
        step = (plan.get("steps") or {}).get(target)
        if step is None:
            raise ValueError("目标节点不在该发布版本里")
        if step.get("terminal") or step.get("kind") == "start":
            raise ValueError("开始和终止节点不能作为回退目标")
        branch_key = branch if step.get("kind") == "investigation" else "default"
        if mode == "branch":
            if target_token.status == "failed":
                raise ValueError("该次到访已失败，请选择重新执行")
            if step.get("kind") == "investigation" and branch_key not in ("yes", "no"):
                raise ValueError("回退分支必须是是/否")
            if not (step.get("next") or {}).get(branch_key):
                raise ValueError(f"目标节点没有 {branch_key} 分支的后继")
            if (step.get("call") or step.get("pipeline") or step.get("recipe")) and self._successful_run(
                db, instance.id, target_token.id
            ) is None:
                raise ValueError("目标到访没有可复用的成功结果，请选择重新执行")
            if step.get("human") and target_token.status != "done":
                reviews = db.scalars(select(models.Review).where(
                    models.Review.instance_id == instance.id,
                    models.Review.token_id == target_token.id,
                    models.Review.status == "done",
                ))
                if not any((_loads(r.decision_json) or {}).get("result") in ("yes", "no", "done")
                           for r in reviews):
                    raise ValueError("目标人工到访尚未完成，请选择重新执行")

        # 保留选定到访的祖先上下文，所有其他分支退出本次有效诊断。
        parent_id = target_token.id if mode == "branch" else target_token.parent_token_id
        retained: set[str] = set()
        cursor = parent_id
        while cursor and cursor not in retained:
            ancestor = by_id.get(cursor)
            if ancestor is None:
                break
            retained.add(cursor)
            cursor = ancestor.parent_token_id
        stamp = now_iso()
        for token in tokens:
            if token.id not in retained or token.status in ("queued", "running", "waiting"):
                # 人工与无调用节点失败时没有 run，令牌状态是唯一失败证据。
                if token.status not in ("failed", "abandoned"):
                    token.status = "abandoned"
                    token.updated_at = stamp
        for review in db.scalars(select(models.Review).where(
            models.Review.instance_id == instance.id, models.Review.status == "pending"
        )):
            review.status = "cancelled"
            review.decided_at = stamp

        # 历史 run 的成功/失败结果不能被回退覆盖，轨迹仍需展示真实执行结论。
        new_generation = max((t.generation for t in tokens), default=1) + 1
        if plan.get("diagnosis"):
            reference = self._diagnosis_visit_state(db, instance, target_token)
            if reference is None:
                raise ValueError("所选到访缺少诊断数据版本，无法恢复")
            self._save_diagnosis_state(db, instance, None, reference, generation=new_generation)
            self._close_session(instance.id)
        parent = SimpleNamespace(id=parent_id, sort_order=target_token.sort_order)
        targets = [target] if mode == "retry" else step["next"][branch_key]
        self._create_tokens(
            db, instance, plan, parent, targets,
            (target_token.branch or "default") if mode == "retry" else branch_key,
            new_generation,
        )
        instance.status = "running"
        instance.ended_at = None
        logger.info(
            "实例 %s 回退到访 %s 节点 %s（%s/%s，新代 %s）：%s",
            instance.id, target_token.id, target, mode, branch_key, new_generation, reason or "未填原因",
        )

    def _successful_run(self, db, instance_id: str, token_id: str):
        runs = db.scalars(select(models.Run).where(
            models.Run.instance_id == instance_id,
            models.Run.token_id == token_id,
        ).order_by(models.Run.started_at.desc(), models.Run.id.desc()))
        for run in runs:
            if run.status == "ok":
                return run
            # 老版本会把成功运行覆盖为 abandoned，只接受仍有输出且无错误的记录。
            if run.status == "abandoned" and run.error is None and run.output_json is not None:
                return run
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


def _cast(value, kind):
    """启动表单只有字符串；配方用 cast 显式声明要数字还是 JSON，避免把 "12" 传给数值比较。"""
    if kind in (None, ""):
        return value
    if kind == "text":
        return value if isinstance(value, str) else str(value)
    if kind == "number":
        number = float(str(value).strip())
        return int(number) if number.is_integer() else number
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "y", "是"):
            return True
        if text in ("0", "false", "no", "n", "否"):
            return False
        raise ValueError(f"{value!r} 不是布尔值")
    if kind == "json":
        return json.loads(value) if isinstance(value, str) else value
    raise ValueError(f"未知的类型转换 {kind}")


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

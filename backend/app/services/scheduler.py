"""实例持久化基类：token、待办、运行记录与状态收敛。

每个实例一把锁；线程内每次只开一个短事务，不跨线程共享 Session。
生成执行（GeneratedRunner）继承它，解释执行循环已随旧引擎移除。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from functools import wraps
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import db as db_module
from app import models
from app.db import SessionLocal, new_id, now_iso
from app.services import storage

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0

TERMINAL_INSTANCE = ("success", "failed", "escalated", "terminated")
ACTIVE_TOKEN = ("queued", "running")

# 失败分类：route/* 属于正常排除，execution/* 属于真出错
ERR_MISSING_STEP = "plan/step-missing"

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
    def __init__(self, session_factory, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._session_factory = session_factory
        # 传进来的是模块级 SessionLocal 时，跟着 db.configure() 换库走
        self._follow_module = session_factory is db_module.SessionLocal
        self._timeout = timeout
        self._lock = threading.Lock()
        self._operation_locks: dict[str, threading.RLock] = {}
        self._termination_locks: dict[str, threading.Lock] = {}

    def _instance_lock(self, instance_id: str):
        with self._lock:
            return self._operation_locks.setdefault(instance_id, threading.RLock())

    def _open(self):
        factory = db_module.SessionLocal if self._follow_module else self._session_factory
        return factory()

    # ------------------------------------------------------------ 对外入口

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

    def _tokens(self, db, instance_id: str) -> list:
        return list(
            db.scalars(
                select(models.Token)
                .where(models.Token.instance_id == instance_id)
                .order_by(models.Token.sort_order, models.Token.created_at)
            )
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

    def _plan(self, db, instance) -> dict:
        release = db.get(models.Release, instance.release_id)
        if release is None:
            return {}
        plan = _loads(release.plan_json)
        return plan if isinstance(plan, dict) else {}

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
        generation: int | None = None,
    ):
        stamp = started or now_iso()
        run = models.Run(
            id=new_id(),
            instance_id=instance.id,
            token_id=token.id if token is not None else None,
            node_id=node_id or (token.node_id if token is not None else "__instance__"),
            generation=generation or (token.generation if token is not None else 1),
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


scheduler = Scheduler(SessionLocal)

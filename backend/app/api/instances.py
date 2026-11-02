"""实例：启动、查询、待办提交、回退、SSE 事件流。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import SessionLocal, get_db, new_id, now_iso
from app.logging_setup import brief
from app.models import Flow, Instance, Release, Review, Run, Snapshot, Token
from app.schemas import (
    InstanceCreate,
    InstanceDetail,
    InstanceSummary,
    ReviewDecisionIn,
    RollbackIn,
    make_instance_detail,
    make_instance_summary,
)

try:  # 引擎层缺席时接口仍在，只是不推进
    from app.services.runner import runner
except ImportError as exc:  # pragma: no cover
    logging.getLogger(__name__).warning("执行引擎不可用，实例不会自动推进：%s", exc)

    class _NullRunner:
        def start(self, instance_id: str) -> None:
            return None

        def submit_review(self, instance_id: str, review_id: str, decision: dict) -> None:
            return None

        def rollback(self, instance_id: str, target: str, branch: str, reason: str | None) -> None:
            return None

    runner = _NullRunner()  # type: ignore[assignment]


logger = logging.getLogger(__name__)
router = APIRouter(tags=["instances"])

SSE_POLL_SECONDS = 1.0
SSE_PING_SECONDS = 15.0


def get_instance_or_404(db: Session, instance_id: str) -> Instance:
    instance = db.get(Instance, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="实例不存在")
    return instance


def release_meta(db: Session, release_id: str) -> tuple[str | None, int | None]:
    release = db.get(Release, release_id)
    if release is None:
        return None, None
    return release.flow_id, release.version_no


def release_inputs(release: Release) -> list[dict]:
    """发布时冻结的启动表单；老版本的行里是空数组。"""
    raw = release.inputs_json
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("发布版本 %s 的启动表单无法解析", release.id)
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def normalize_start_input(raw: Any) -> dict:
    """启动上下文固定两层：global 全函数可用，scope 按函数名覆盖同名全局参数。

    前端已按两层提交时原样保留；提交扁平对象时按 global 处理（兼容老前端）。
    调度器按同一口径取值，见 services/scheduler._snapshot_value。
    """
    if isinstance(raw, dict) and ("global" in raw or "scope" in raw):
        glob = raw.get("global")
        scope = raw.get("scope")
        return {
            "global": glob if isinstance(glob, dict) else {},
            "scope": (
                {str(key): value for key, value in scope.items() if isinstance(value, dict)}
                if isinstance(scope, dict)
                else {}
            ),
        }
    return {"global": raw if isinstance(raw, dict) else {}, "scope": {}}


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def apply_defaults(inputs: list[dict], context: dict) -> dict:
    """把发布时冻结的默认值填进启动上下文，请求里已经给了的字段不动。

    函数局部参数的默认值只写进 scope[函数名]：全局层是所有函数共享的，
    一旦泄漏，别的同名参数会被悄悄改掉。
    """
    glob = context.setdefault("global", {})
    scope = context.setdefault("scope", {})
    for item in inputs:
        default = item.get("default")
        if default is None:
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        if item.get("scope") == "function":
            bucket = scope.setdefault(str(item.get("function") or ""), {})
            if not _blank(bucket.get(name)):
                continue
            bucket[name] = default
        elif _blank(glob.get(name)):
            glob[name] = default
    return context


def missing_inputs(inputs: list[dict], context: dict) -> list[str]:
    missing: list[str] = []
    for item in inputs:
        if not item.get("required", True):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        if item.get("scope") == "function":
            section = context.get("scope")
            key = str(item.get("function") or "")
            values = section.get(key) if isinstance(section, dict) else None
        else:
            values = context.get("global")
        if not isinstance(values, dict) or _blank(values.get(name)):
            missing.append(name)
    return missing


def instance_payload(db: Session, instance: Instance) -> InstanceDetail:
    tokens = list(
        db.scalars(
            select(Token)
            .where(Token.instance_id == instance.id)
            .order_by(Token.generation, Token.sort_order, Token.created_at)
        )
    )
    runs = list(
        db.scalars(
            select(Run).where(Run.instance_id == instance.id).order_by(Run.started_at, Run.attempt)
        )
    )
    reviews = list(
        db.scalars(
            select(Review)
            .where(Review.instance_id == instance.id)
            .order_by(Review.created_at)
        )
    )
    snapshot = db.get(Snapshot, instance.snapshot_id) if instance.snapshot_id else None
    flow = db.get(Flow, instance.flow_id)
    _, version_no = release_meta(db, instance.release_id)
    return make_instance_detail(
        instance,
        tokens,
        runs,
        reviews,
        snapshot,
        flow_name=flow.name if flow else None,
        release_no=version_no,
    )


@router.post("/flows/{flow_id}/instances", response_model=InstanceSummary)
def start_instance(
    flow_id: str, payload: InstanceCreate, db: Session = Depends(get_db)
) -> InstanceSummary:
    flow = db.get(Flow, flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="流程不存在")

    if payload.release_id:
        release = db.get(Release, payload.release_id)
        if release is None or release.flow_id != flow_id:
            raise HTTPException(status_code=400, detail="发布版本不存在或不属于该流程")
    else:
        release = db.scalars(
            select(Release)
            .where(Release.flow_id == flow_id)
            .order_by(Release.version_no.desc())
            .limit(1)
        ).first()
        if release is None:
            raise HTTPException(status_code=409, detail="流程还没有发布版本")

    frozen = release_inputs(release)
    context = normalize_start_input(payload.input)
    apply_defaults(frozen, context)
    missing = missing_inputs(frozen, context)
    if missing:
        # 启动表单是发布时定死的契约，缺参数直接拒绝，不要带着 None 去调业务函数
        raise HTTPException(status_code=400, detail=f"缺少启动参数：{', '.join(missing)}")

    stamp = now_iso()
    instance = Instance(
        id=new_id(),
        flow_id=flow_id,
        release_id=release.id,
        status="running",
        started_by="local",
        started_at=stamp,
    )
    snapshot = Snapshot(
        id=new_id(),
        instance_id=instance.id,
        seq=1,
        payload_json=json.dumps(context, ensure_ascii=False),
        source="start",
        params_json="{}",
        captured_at=stamp,
    )
    instance.snapshot_id = snapshot.id
    db.add(instance)
    db.add(snapshot)
    db.commit()
    db.refresh(instance)

    logger.info(
        "实例 %s 启动：flow=%s release=v%s 输入 %s",
        instance.id,
        flow_id,
        release.version_no,
        brief(context),
    )

    try:
        runner.start(instance.id)
    except Exception as exc:  # 调度起不来只影响推进，实例记录仍保留给人排查
        logger.exception("实例 %s 启动调度失败", instance.id)
        instance.status = "failed"
        instance.ended_at = now_iso()
        db.commit()
        raise HTTPException(status_code=500, detail=f"调度启动失败：{exc}") from exc

    return make_instance_summary(instance, flow.name if flow else None, release.version_no)


@router.get("/instances", response_model=list[InstanceSummary])
def list_instances(
    flow_id: str | None = Query(default=None, alias="flowId"),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[InstanceSummary]:
    statement = select(Instance)
    if flow_id:
        statement = statement.where(Instance.flow_id == flow_id)
    if status:
        statement = statement.where(Instance.status == status)
    rows = list(db.scalars(statement.order_by(Instance.started_at.desc())))
    names: dict[str, str] = {}
    versions: dict[str, int] = {}
    items = []
    for instance in rows:
        if instance.flow_id not in names:
            flow = db.get(Flow, instance.flow_id)
            names[instance.flow_id] = flow.name if flow else None  # type: ignore[assignment]
        if instance.release_id not in versions:
            _, version_no = release_meta(db, instance.release_id)
            versions[instance.release_id] = version_no or 0
        items.append(
            make_instance_summary(
                instance,
                names.get(instance.flow_id),
                versions.get(instance.release_id) or None,
            )
        )
    return items


@router.get("/instances/{instance_id}", response_model=InstanceDetail)
def read_instance(instance_id: str, db: Session = Depends(get_db)) -> InstanceDetail:
    instance = get_instance_or_404(db, instance_id)
    return instance_payload(db, instance)


@router.post("/instances/{instance_id}/reviews/{review_id}", response_model=InstanceDetail)
def submit_review(
    instance_id: str,
    review_id: str,
    payload: ReviewDecisionIn,
    db: Session = Depends(get_db),
) -> InstanceDetail:
    get_instance_or_404(db, instance_id)
    review = db.get(Review, review_id)
    if review is None or review.instance_id != instance_id:
        raise HTTPException(status_code=404, detail="待办不存在")
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="该待办已处理")

    decision: dict[str, Any] = {"result": payload.result}
    if payload.target is not None:
        decision["target"] = payload.target
    if payload.reason is not None:
        decision["reason"] = payload.reason

    stamp = now_iso()
    review.status = "done"
    review.decision_json = json.dumps(decision, ensure_ascii=False)
    review.decided_at = stamp
    review.decided_by = "local"
    db.commit()

    logger.info(
        "实例 %s 待办 %s 提交：%s（节点 %s）",
        instance_id,
        review_id,
        decision.get("result"),
        review.node_id,
    )

    try:
        runner.submit_review(instance_id, review_id, decision)
    except Exception as exc:
        logger.exception("实例 %s 待办 %s 提交调度失败", instance_id, review_id)
        raise HTTPException(status_code=500, detail=f"提交待办失败：{exc}") from exc

    # 返回整份详情：前端拿到后直接替换 store，不必再发一次详情请求
    db.expire_all()
    instance = get_instance_or_404(db, instance_id)
    return instance_payload(db, instance)


@router.post("/instances/{instance_id}/rollback", response_model=InstanceDetail)
def rollback(
    instance_id: str, payload: RollbackIn, db: Session = Depends(get_db)
) -> InstanceDetail:
    get_instance_or_404(db, instance_id)
    logger.info(
        "实例 %s 回退请求：target=%s branch=%s reason=%s",
        instance_id,
        payload.target,
        payload.branch,
        (payload.reason or "")[:80],
    )
    try:
        runner.rollback(instance_id, payload.target, payload.branch, payload.reason)
    except Exception as exc:
        logger.warning("实例 %s 回退失败：%s", instance_id, exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    db.expire_all()
    instance = get_instance_or_404(db, instance_id)
    return instance_payload(db, instance)


def _digest_payload(instance_id: str) -> dict[str, Any] | None:
    """一次轮询：把实例的可见状态压成一个可比较的小对象。"""
    session = SessionLocal()
    try:
        instance = session.get(Instance, instance_id)
        if instance is None:
            return None
        tokens = session.execute(
            select(Token.status, func.count(Token.id)).where(Token.instance_id == instance_id).group_by(Token.status)
        ).all()
        runs_count = session.scalar(
            select(func.count(Run.id)).where(Run.instance_id == instance_id)
        )
        pending = session.scalar(
            select(func.count(Review.id)).where(
                Review.instance_id == instance_id, Review.status == "pending"
            )
        )
        last_touch = session.scalar(
            select(func.max(Token.updated_at)).where(Token.instance_id == instance_id)
        )
        return {
            "instanceId": instance.id,
            "status": instance.status,
            "endedAt": instance.ended_at,
            "tokens": {status: count for status, count in tokens},
            "runCount": int(runs_count or 0),
            "pendingReviews": int(pending or 0),
            "lastTokenAt": last_touch,
        }
    finally:
        session.close()


@router.get("/instances/{instance_id}/events")
async def stream_events(instance_id: str, request: Request) -> StreamingResponse:
    probe = SessionLocal()
    try:
        if probe.get(Instance, instance_id) is None:
            raise HTTPException(status_code=404, detail="实例不存在")
    finally:
        probe.close()

    async def event_stream() -> AsyncIterator[str]:
        previous: str | None = None
        last_ping = 0.0
        while True:
            if await request.is_disconnected():
                break
            payload = await run_in_threadpool(_digest_payload, instance_id)
            if payload is None:
                break
            digest = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            now = time.monotonic()
            if digest != previous:
                previous = digest
                last_ping = now
                yield f"data: {digest}\n\n"
            elif now - last_ping >= SSE_PING_SECONDS:
                last_ping = now
                yield ": ping\n\n"
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

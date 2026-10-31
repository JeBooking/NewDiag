"""流程与在线版本（revision）相关接口。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db, new_id, now_iso
from app.models import Flow, Instance, Release, Revision
from app.schemas import (
    CommitResult,
    FlowCreate,
    FlowDetail,
    FlowPatch,
    FlowSummary,
    ImportFlowIn,
    SavedAt,
    WorkingDocIn,
    make_flow_detail,
    make_flow_summary,
)

try:  # 引擎层缺席时后端仍要能起来
    from app.services.compiler import ValidationError, validate_doc
except ImportError as exc:  # pragma: no cover
    logging.getLogger(__name__).warning("compiler 不可用，提交时跳过校验：%s", exc)
    validate_doc = None  # type: ignore[assignment]

    class ValidationError(Exception):  # type: ignore[no-redef]
        issues: list[dict] = []


logger = logging.getLogger(__name__)
router = APIRouter(tags=["flows"])

LOCAL_ACTOR = "local"


def empty_doc(name: str, mode: str = "parallel") -> dict:
    return {"format": 1, "name": name, "mode": mode, "nodes": [], "edges": []}


def get_flow_or_404(db: Session, flow_id: str) -> Flow:
    flow = db.get(Flow, flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="流程不存在")
    return flow


def revision_doc(revision: Revision | None) -> dict:
    if revision is None or not revision.json:
        return {}
    try:
        doc = json.loads(revision.json)
    except ValueError:
        logger.warning("revision %s 的 json 无法解析", revision.id)
        return {}
    return doc if isinstance(doc, dict) else {}


def current_revision(db: Session, flow: Flow) -> Revision | None:
    if not flow.current_revision_id:
        return None
    return db.get(Revision, flow.current_revision_id)


def working_revision(db: Session, flow: Flow) -> Revision | None:
    if not flow.editing_revision_id:
        return None
    revision = db.get(Revision, flow.editing_revision_id)
    if revision is None or revision.status != "working":
        return None
    return revision


def release_count(db: Session, flow_id: str) -> int:
    return db.scalar(select(func.count(Release.id)).where(Release.flow_id == flow_id)) or 0


def last_run_status(db: Session, flow_id: str) -> str | None:
    return db.scalar(
        select(Instance.status)
        .where(Instance.flow_id == flow_id)
        .order_by(Instance.started_at.desc())
        .limit(1)
    )


def last_published_at(db: Session, flow_id: str) -> str | None:
    return db.scalar(select(func.max(Release.published_at)).where(Release.flow_id == flow_id))


def flow_detail(db: Session, flow: Flow) -> FlowDetail:
    releases = list(
        db.scalars(
            select(Release)
            .where(Release.flow_id == flow.id)
            .order_by(Release.version_no.desc())
        )
    )
    working = working_revision(db, flow)
    # 检出期间返回草稿：设计页刷新后要看到自己刚暂存的内容（管理员预览同理）
    doc = revision_doc(working) if working is not None else revision_doc(current_revision(db, flow))
    return make_flow_detail(
        flow,
        doc,
        releases,
        release_count=len(releases),
        last_run_status=last_run_status(db, flow.id),
        working_revision_id=working.id if working else None,
    )


def require_lock(db: Session, flow: Flow, *, admin: bool = False) -> Revision:
    working = working_revision(db, flow)
    if working is None:
        raise HTTPException(status_code=409, detail="没有检出中的草稿")
    if not admin and flow.editing_by != LOCAL_ACTOR:
        raise HTTPException(status_code=409, detail="当前流程未被本端检出")
    return working


def validate_or_422(doc: dict, *, strict_binding: bool = True) -> list[dict]:
    """有阻断级问题就抛 422，否则返回可以放过、但值得提示的问题（warning）。"""
    if validate_doc is None:
        return []
    raw = validate_doc(doc, strict_binding=strict_binding) or []
    issues = [item for item in raw if isinstance(item, dict)]
    errors = [item for item in issues if item.get("level") == "error"]
    # 有的人只返回 message 不给 level，按错误处理，宁可拦下也不放过
    if not errors:
        errors = [item for item in issues if "level" not in item]
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return [item for item in issues if item.get("level") == "warning"]


def commit_working(
    db: Session, flow: Flow, working: Revision, *, admin: bool = False
) -> CommitResult:
    doc = revision_doc(working)
    # 保存允许半成品：参数绑定问题降级为 warning，真正阻断留给发布
    warnings = validate_or_422(doc, strict_binding=False)
    stamp = now_iso()
    working.status = "committed"
    working.committed_at = stamp
    flow.current_revision_id = working.id
    flow.updated_at = stamp
    # 模式（并行/串行）属于图本身，以画布提交为准，保证发布与画布一致
    mode = doc.get("mode")
    if mode in ("parallel", "serial"):
        flow.mode = mode
    if flow.editing_by == LOCAL_ACTOR or admin:
        flow.editing_by = None
        flow.editing_revision_id = None
        flow.editing_since = None
    db.commit()
    db.refresh(flow)
    detail = flow_detail(db, flow)
    logger.info(
        "流程 %s 提交 revision=%s：%d 个节点，%d 条 warning%s",
        flow.id,
        working.id,
        len(doc.get("nodes") or []),
        len(warnings),
        "（管理员代存）" if admin else "",
    )
    return CommitResult(**detail.model_dump(), revision_id=working.id, issues=warnings)


def discard_working(db: Session, flow: Flow, working: Revision, *, admin: bool = False) -> FlowDetail:
    working.status = "discarded"
    flow.updated_at = now_iso()
    if flow.editing_by == LOCAL_ACTOR or admin:
        flow.editing_by = None
        flow.editing_revision_id = None
        flow.editing_since = None
    db.commit()
    db.refresh(flow)
    logger.info("流程 %s 放弃检出 revision=%s%s", flow.id, working.id, "（管理员）" if admin else "")
    return flow_detail(db, flow)


@router.get("/flows", response_model=list[FlowSummary])
def list_flows(db: Session = Depends(get_db)) -> list[FlowSummary]:
    flows = list(db.scalars(select(Flow).order_by(Flow.updated_at.desc())))
    items = []
    for flow in flows:
        items.append(
            make_flow_summary(
                flow,
                release_count(db, flow.id),
                last_run_status(db, flow.id),
                last_published_at(db, flow.id),
            )
        )
    return items


@router.post("/flows", response_model=FlowDetail)
def create_flow(payload: FlowCreate, db: Session = Depends(get_db)) -> FlowDetail:
    stamp = now_iso()
    doc = empty_doc(payload.name, payload.mode)
    flow = Flow(
        id=new_id(),
        name=payload.name,
        description=payload.description or "",
        mode=payload.mode,
        owner_id=LOCAL_ACTOR,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(flow)
    db.flush()
    revision = Revision(
        id=new_id(),
        flow_id=flow.id,
        json=json.dumps(doc, ensure_ascii=False),
        status="committed",
        author=LOCAL_ACTOR,
        created_at=stamp,
        committed_at=stamp,
    )
    db.add(revision)
    db.flush()
    flow.current_revision_id = revision.id
    db.commit()
    db.refresh(flow)
    return flow_detail(db, flow)


@router.post("/flows/import", response_model=FlowDetail)
def import_flow(payload: ImportFlowIn, db: Session = Depends(get_db)) -> FlowDetail:
    """导入一份流程文档：一次建流程并写入在线版本，不占用检出锁。

    绑定问题按半成品放行（同 commit），发布时才强制校验。
    """
    doc = payload.doc
    validate_or_422(doc, strict_binding=False)

    stamp = now_iso()
    mode = doc.get("mode") if doc.get("mode") in ("parallel", "serial") else "parallel"
    flow = Flow(
        id=new_id(),
        name=str(doc.get("name") or "").strip() or "未命名流程",
        description=str(doc.get("description") or ""),
        mode=mode,
        owner_id=LOCAL_ACTOR,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(flow)
    db.flush()
    revision = Revision(
        id=new_id(),
        flow_id=flow.id,
        json=json.dumps(doc, ensure_ascii=False),
        status="committed",
        author=LOCAL_ACTOR,
        created_at=stamp,
        committed_at=stamp,
    )
    db.add(revision)
    db.flush()
    flow.current_revision_id = revision.id
    db.commit()
    db.refresh(flow)
    logger.info("导入流程 %s：%d 节点", flow.id, len(doc.get("nodes") or []))
    return flow_detail(db, flow)


@router.get("/flows/{flow_id}", response_model=FlowDetail)
def read_flow(flow_id: str, db: Session = Depends(get_db)) -> FlowDetail:
    flow = get_flow_or_404(db, flow_id)
    return flow_detail(db, flow)


@router.patch("/flows/{flow_id}", response_model=FlowDetail)
def patch_flow(flow_id: str, payload: FlowPatch, db: Session = Depends(get_db)) -> FlowDetail:
    flow = get_flow_or_404(db, flow_id)
    if payload.name is not None:
        flow.name = payload.name
    if payload.description is not None:
        flow.description = payload.description
    if payload.mode is not None:
        flow.mode = payload.mode
        # 检出中的草稿也同步模式，否则下次提交会把旧模式写回来
        working = working_revision(db, flow)
        if working is not None:
            doc = revision_doc(working)
            doc["mode"] = payload.mode
            working.json = json.dumps(doc, ensure_ascii=False)
    flow.updated_at = now_iso()
    db.commit()
    db.refresh(flow)
    return flow_detail(db, flow)


@router.post("/flows/{flow_id}/checkout", response_model=FlowDetail)
def checkout(flow_id: str, db: Session = Depends(get_db)) -> FlowDetail:
    flow = get_flow_or_404(db, flow_id)
    if flow.editing_by and flow.editing_by != LOCAL_ACTOR:
        raise HTTPException(status_code=409, detail=f"{flow.editing_by} 正在编辑")
    working = working_revision(db, flow)
    if working is not None:
        return flow_detail(db, flow)

    source = current_revision(db, flow)
    stamp = now_iso()
    revision = Revision(
        id=new_id(),
        flow_id=flow.id,
        json=source.json if source else json.dumps(empty_doc(flow.name), ensure_ascii=False),
        status="working",
        author=LOCAL_ACTOR,
        created_at=stamp,
    )
    db.add(revision)
    db.flush()
    flow.editing_by = LOCAL_ACTOR
    flow.editing_revision_id = revision.id
    flow.editing_since = stamp
    db.commit()
    db.refresh(flow)
    logger.info("流程 %s 检出：working=%s", flow.id, revision.id)
    return flow_detail(db, flow)


@router.put("/flows/{flow_id}/working", response_model=SavedAt)
def save_working(
    flow_id: str, payload: WorkingDocIn, db: Session = Depends(get_db)
) -> SavedAt:
    flow = get_flow_or_404(db, flow_id)
    working = require_lock(db, flow)
    working.json = json.dumps(payload.doc, ensure_ascii=False)
    stamp = now_iso()
    flow.updated_at = stamp
    db.commit()
    return SavedAt(revision_id=working.id, saved_at=stamp)


@router.post("/flows/{flow_id}/commit", response_model=CommitResult)
def commit(
    flow_id: str, payload: WorkingDocIn | None = None, db: Session = Depends(get_db)
) -> CommitResult:
    flow = get_flow_or_404(db, flow_id)
    working = require_lock(db, flow)
    # 前端可能带着尚未暂存的 doc 直接保存，用它覆盖草稿，避免丢最后 2 秒的编辑
    if payload is not None:
        working.json = json.dumps(payload.doc, ensure_ascii=False)
    return commit_working(db, flow, working)


@router.post("/flows/{flow_id}/discard", response_model=FlowDetail)
def discard(flow_id: str, db: Session = Depends(get_db)) -> FlowDetail:
    flow = get_flow_or_404(db, flow_id)
    working = require_lock(db, flow)
    return discard_working(db, flow, working)


@router.post("/flows/{flow_id}/admin/commit", response_model=CommitResult)
def admin_commit(flow_id: str, db: Session = Depends(get_db)) -> CommitResult:
    flow = get_flow_or_404(db, flow_id)
    working = require_lock(db, flow, admin=True)
    return commit_working(db, flow, working, admin=True)


@router.post("/flows/{flow_id}/admin/discard", response_model=FlowDetail)
def admin_discard(flow_id: str, db: Session = Depends(get_db)) -> FlowDetail:
    flow = get_flow_or_404(db, flow_id)
    working = require_lock(db, flow, admin=True)
    return discard_working(db, flow, working, admin=True)

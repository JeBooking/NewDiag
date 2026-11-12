"""流程与在线版本（revision）相关接口。"""

from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import folders
from app.db import get_db, new_id, now_iso
from app.models import FileBlob, Flow, Folder, Instance, Release, Revision
from app.services.flow_format import compact, expand
from app.services import packages, storage
from app.schemas import (
    CommitResult,
    FlowCreate,
    FlowDetail,
    FlowPatch,
    FlowSummary,
    ImportFlowIn,
    ProbeIn,
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


try:  # 设计期探测：仅开始节点面板展示入参用，缺席时该接口 503
    from app.services.signatures import probe_doc
except ImportError as exc:  # pragma: no cover
    logging.getLogger(__name__).warning("签名探测不可用，probe 接口将返回 503：%s", exc)
    probe_doc = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
router = APIRouter(tags=["flows"])

LOCAL_ACTOR = "local"
# 未分类的过滤值：root 是约定值，none/null 兼容前端直接传字面量（目录 id 是 16 位 hex，不会撞）
UNCATEGORIZED_ALIASES = frozenset({"root", "none", "null"})


def empty_doc(name: str, mode: str = "parallel") -> dict:
    return {"format": 1, "name": name, "mode": mode, "nodes": [], "edges": []}


def instance_doc(name: str, mode: str = "parallel") -> dict:
    """新建流程用的诊断实例模板：骨架齐全、没有业务内容。

    组合配置留空，由编排团队选择原工程接口；模板不预设业务对象或启动参数。
    """
    return {
        "format": 1,
        "name": name,
        "mode": mode,
        'composition': {'objects': [], 'resources': [], 'operations': []},
        "nodes": [],
        "edges": [],
    }


def get_flow_or_404(db: Session, flow_id: str) -> Flow:
    """取可用流程：回收站里的（含墓碑）一律当作不存在。

    单流程接口全走这里，软删后详情 / 检出 / 发布 / 导出自然全部 404。
    """
    flow = db.get(Flow, flow_id)
    if flow is None or flow.deleted_at is not None:
        raise HTTPException(status_code=404, detail="流程不存在")
    return flow


def revision_doc(revision: Revision | None) -> dict:
    if revision is None or not revision.json:
        return {}
    try:
        doc = expand(json.loads(revision.json))
    except ValueError as exc:
        logger.warning("revision %s 的文档无法读取：%s", revision.id, exc)
        raise HTTPException(status_code=409, detail=f"流程文档无法读取：{exc}") from exc
    return doc if isinstance(doc, dict) else {}


def submitted_doc(value: dict) -> dict:
    try:
        return expand(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


def collect_issues(doc: dict, *, strict_binding: bool = True) -> list[dict]:
    """收集校验问题，不抛错：保存要落盘，问题只作提示；发布侧自己再拦。"""
    if validate_doc is None:
        return []
    return [
        item for item in (validate_doc(doc, strict_binding=strict_binding) or []) if isinstance(item, dict)
    ]


def commit_working(
    db: Session, flow: Flow, working: Revision, *, admin: bool = False
) -> CommitResult:
    doc = revision_doc(working)
    # 保存只落盘：所有问题随响应返回，是否阻断交给发布
    issues = collect_issues(doc, strict_binding=False)
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
        "流程 %s 提交 revision=%s：%d 个节点，%d 条问题%s",
        flow.id,
        working.id,
        len(doc.get("nodes") or []),
        len(issues),
        "（管理员代存）" if admin else "",
    )
    return CommitResult(**detail.model_dump(), revision_id=working.id, issues=issues)


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
def list_flows(
    folder_id: str | None = Query(default=None, alias="folderId"),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[FlowSummary]:
    """folderId 传 root 表示未分类，传具体 id 只看该目录（不含子目录），不传看全部。"""
    statement = select(Flow).where(Flow.deleted_at.is_(None))
    if folder_id:
        if folder_id in UNCATEGORIZED_ALIASES:
            statement = statement.where(Flow.folder_id.is_(None))
        else:
            statement = statement.where(Flow.folder_id == folder_id)
    term = (search or "").strip()
    if term:
        pattern = f"%{term}%"
        statement = statement.where(or_(Flow.name.ilike(pattern), Flow.description.ilike(pattern)))

    flows = list(db.scalars(statement.order_by(Flow.updated_at.desc())))
    return _flow_summaries(db, flows)


def _flow_summaries(db: Session, flows: list[Flow]) -> list[FlowSummary]:
    return [
        make_flow_summary(
            flow,
            release_count(db, flow.id),
            last_run_status(db, flow.id),
            last_published_at(db, flow.id),
        )
        for flow in flows
    ]


# 必须注册在 /flows/{flow_id} 之前：先注册的规则先匹配，"trash" 否则会被当成流程 id
@router.get("/flows/trash", response_model=list[FlowSummary])
def list_trash(db: Session = Depends(get_db)) -> list[FlowSummary]:
    """回收站：只列软删未彻底删除的流程，最近删除的在前。"""
    flows = list(
        db.scalars(
            select(Flow)
            .where(Flow.deleted_at.is_not(None), Flow.purged_at.is_(None))
            .order_by(Flow.deleted_at.desc())
        )
    )
    return _flow_summaries(db, flows)


@router.delete("/flows/trash/{flow_id}", status_code=204)
def purge_flow(flow_id: str, db: Session = Depends(get_db)) -> None:
    """彻底删除只做墓碑：flow 行必须留着，否则 revisions / releases / instances 会被级联删掉。"""
    flow = db.get(Flow, flow_id)
    if flow is None or flow.deleted_at is None or flow.purged_at is not None:
        raise HTTPException(status_code=404, detail="回收站里没有该流程")
    stamp = now_iso()
    # 名字留个可追溯的标记，历史实例详情要靠 flow.name 回填
    flow.name = f"已删除流程·{flow.id[:6]}"
    flow.description = ""
    flow.folder_id = None
    flow.purged_at = stamp
    flow.updated_at = stamp
    db.commit()
    logger.info("流程 %s 彻底删除（墓碑）", flow.id)


def require_folder(db: Session, folder_id: str | None) -> None:
    if folder_id is not None and db.get(Folder, folder_id) is None:
        raise HTTPException(status_code=404, detail="目录不存在")


@router.post("/flows", response_model=FlowDetail)
def create_flow(payload: FlowCreate, db: Session = Depends(get_db)) -> FlowDetail:
    require_folder(db, payload.folder_id)
    stamp = now_iso()
    # 新建即实例模板；导入 / 复制走各自的文档，不经过这里
    doc = instance_doc(payload.name, payload.mode)
    flow = Flow(
        id=new_id(),
        name=payload.name,
        description=payload.description or "",
        mode=payload.mode,
        folder_id=payload.folder_id,
        owner_id=LOCAL_ACTOR,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(flow)
    db.flush()
    revision = Revision(
        id=new_id(),
        flow_id=flow.id,
        json=json.dumps(compact(doc), ensure_ascii=False),
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
    doc = submitted_doc(payload.doc)
    # 导入同保存：问题只提示，不阻断
    collect_issues(doc, strict_binding=False)

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
        json=json.dumps(compact(doc), ensure_ascii=False),
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


def _attachment_refs(doc: dict):
    """按出现顺序列出 doc 里所有附件引用；同一哈希只算一次。"""
    seen: set[str] = set()
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        refs = node.get("attachments")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            digest = ref.get("hash")
            if not isinstance(digest, str) or not digest or digest in seen:
                continue
            seen.add(digest)
            yield ref


def _bindings(doc: dict) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict] = []
    bindings = [doc.get("diagnosis")]
    composition = doc.get('composition')
    if isinstance(composition, dict):
        objects = composition.get('objects', [])
        if isinstance(objects, list):
            bindings.extend(objects)
        for name in ('resources', 'operations'):
            groups = composition.get(name, [])
            if isinstance(groups, list):
                for group in groups:
                    if isinstance(group, dict) and isinstance(group.get('calls'), list):
                        bindings.extend(group['calls'])
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        pipeline = node.get("pipeline")
        if isinstance(pipeline, dict):
            bindings.extend(pipeline.get(stage) for stage in ("acquire", "parse", "present", "judge"))
        else:
            bindings.append(node.get("binding"))
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        project = str(binding.get("project") or "")
        function = str(binding.get('interface') or binding.get("function") or "")
        module = str(binding.get('module') or '')
        if not project or not function or (project, module, function) in seen:
            continue
        seen.add((project, module, function))
        result.append({"project": project, "function": function, **({'module': module} if module else {})})
    return result


@router.get("/flows/{flow_id}/export")
def export_flow(
    flow_id: str,
    release_id: str | None = Query(default=None, alias="releaseId"),
    db: Session = Depends(get_db),
) -> Response:
    """导出流程包：workflow.json + manifest.json + attachments/。

    带了 releaseId 导发布版本的快照，否则导当前在线版本。
    """
    flow = get_flow_or_404(db, flow_id)
    release: Release | None = None
    if release_id:
        release = db.get(Release, release_id)
        if release is None or release.flow_id != flow.id:
            raise HTTPException(status_code=404, detail="发布版本不存在或不属于该流程")
        revision = db.get(Revision, release.revision_id)
        label = f"v{release.version_no}"
        version = {
            "kind": "release",
            "releaseId": release.id,
            "versionNo": release.version_no,
            "publishedAt": release.published_at,
        }
    else:
        revision = current_revision(db, flow)
        label = "current"
        version = {
            "kind": "current",
            "revisionId": revision.id if revision is not None else None,
        }

    doc = revision_doc(revision)
    entries: list[dict] = []
    attachments: list[tuple[str, bytes]] = []
    used_names: set[str] = set()
    total = 0
    for ref in _attachment_refs(doc):
        digest = str(ref["hash"])
        blob = db.get(FileBlob, digest)
        name = str(ref.get("name") or (blob.filename if blob else "") or digest)
        entry: dict = {"hash": digest, "name": name}
        path = storage.resolve(digest) if blob is not None else None
        if path is None:
            # 记录在、文件丢了也要能导出，manifest 里标出来，别让整包失败
            entry["missing"] = True
            entries.append(entry)
            continue
        data = path.read_bytes()
        total += len(data)
        if total > packages.MAX_PACKAGE_BYTES:
            raise HTTPException(status_code=413, detail="附件总量超过 200MB，无法导出")
        filename = packages.safe_name(name, digest[:12])
        if filename in used_names:
            filename = f"{digest[:8]}-{filename}"
        used_names.add(filename)
        entry.update(
            {
                "file": f"attachments/{filename}",
                "size": len(data),
                "mediaType": blob.media_type if blob is not None else None,
            }
        )
        entries.append(entry)
        attachments.append((entry["file"], data))

    manifest = {
        "format": 1,
        "kind": packages.PACKAGE_KIND,
        "flow": {"id": flow.id, "name": flow.name},
        "version": version,
        "exportedAt": now_iso(),
        "docFile": packages.DOC_NAME,
        "attachments": entries,
        "bindings": _bindings(doc),
    }
    if release is not None and release.signature_json:
        try:
            manifest["signature"] = json.loads(release.signature_json)
        except ValueError:
            logger.warning("发布版本 %s 的签名表无法解析，跳过", release.id)

    payload = packages.build_package(compact(doc), manifest, attachments)
    filename = f"{packages.safe_name(flow.name, 'flow')}-{label}.zip"
    ascii_name = f"flow-{label}.zip"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
    }
    logger.info(
        "导出流程 %s（%s）：%d 个附件，%d 字节", flow.id, label, len(attachments), len(payload)
    )
    return Response(content=payload, media_type="application/zip", headers=headers)


@router.post("/flows/import-package", response_model=FlowDetail)
async def import_package(file: UploadFile = File(...), db: Session = Depends(get_db)) -> FlowDetail:
    """导入导出过的 zip 包：附件按内容重新入库，哈希不一致时改写引用。"""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="流程包为空")
    try:
        doc, manifest, files = packages.read_package(data)
        doc = expand(doc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not isinstance(doc.get("nodes"), list):
        raise HTTPException(status_code=422, detail="流程包缺少 nodes 数组")

    manifest_files: dict[str, str] = {}
    if manifest is not None:
        for item in manifest.get("attachments") or []:
            if isinstance(item, dict) and isinstance(item.get("hash"), str):
                path = item.get("file")
                if isinstance(path, str) and path:
                    manifest_files[item["hash"]] = path

    for node in doc["nodes"]:
        if not isinstance(node, dict):
            continue
        refs = node.get("attachments")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            digest = ref.get("hash")
            name = str(ref.get("name") or "")
            if not isinstance(digest, str) or not digest:
                continue
            zip_path = manifest_files.get(digest)
            if zip_path is None:
                zip_path = f"attachments/{packages.safe_name(name, digest[:12])}"
            payload = files.get(zip_path)
            if payload is None:
                logger.warning("流程包缺少附件 %s（%s），保留原引用", name or digest, zip_path)
                continue
            stored, path = storage.store(payload)
            if db.get(FileBlob, stored) is None:
                db.add(
                    FileBlob(
                        hash=stored,
                        filename=name or Path(zip_path).name,
                        media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                        size=len(payload),
                        path=str(path),
                    )
                )
            # 内容被改过时哈希会变，引用要跟着改，否则下载 404
            if stored != digest:
                ref["hash"] = stored
    db.flush()

    # 导入同保存：问题只提示，不阻断
    collect_issues(doc, strict_binding=False)

    stamp = now_iso()
    mode = doc.get("mode") if doc.get("mode") in ("parallel", "serial") else "parallel"
    name = str(doc.get("name") or "").strip() or Path(file.filename or "").stem or "导入的流程"
    flow = Flow(
        id=new_id(),
        name=name,
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
        json=json.dumps(compact(doc), ensure_ascii=False),
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
    logger.info("导入流程包 %s：%d 节点", flow.id, len(doc.get("nodes") or []))
    return flow_detail(db, flow)


@router.get("/flows/{flow_id}", response_model=FlowDetail)
def read_flow(flow_id: str, db: Session = Depends(get_db)) -> FlowDetail:
    flow = get_flow_or_404(db, flow_id)
    return flow_detail(db, flow)


@router.delete("/flows/{flow_id}", status_code=204)
def delete_flow(flow_id: str, db: Session = Depends(get_db)) -> None:
    """删除到回收站：只写 deleted_at，历史实例与发布版本原样保留。

    folder_id 故意不动：回收站要显示流程原来在哪个目录。
    """
    flow = get_flow_or_404(db, flow_id)
    stamp = now_iso()
    flow.deleted_at = stamp
    flow.updated_at = stamp
    # 释放编辑锁，否则恢复后别人再也检不出来
    flow.editing_by = None
    flow.editing_revision_id = None
    flow.editing_since = None
    db.commit()
    logger.info("流程 %s 移入回收站", flow.id)


@router.post("/flows/{flow_id}/restore", response_model=FlowDetail)
def restore_flow(flow_id: str, db: Session = Depends(get_db)) -> FlowDetail:
    flow = db.get(Flow, flow_id)
    if flow is None or flow.deleted_at is None or flow.purged_at is not None:
        raise HTTPException(status_code=404, detail="回收站里没有该流程")
    flow.deleted_at = None
    flow.updated_at = now_iso()
    db.commit()
    db.refresh(flow)
    logger.info("流程 %s 从回收站恢复", flow.id)
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
            working.json = json.dumps(compact(doc), ensure_ascii=False)
    # 显式传 folderId: null 才是「移到未分类」，不传表示不动
    if "folder_id" in payload.model_fields_set:
        require_folder(db, payload.folder_id)
        flow.folder_id = payload.folder_id
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
    working.json = json.dumps(compact(submitted_doc(payload.doc)), ensure_ascii=False)
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
        working.json = json.dumps(compact(submitted_doc(payload.doc)), ensure_ascii=False)
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


@router.post("/flows/{flow_id}/probe")
def probe_flow(flow_id: str, payload: ProbeIn, db: Session = Depends(get_db)) -> dict:
    """设计期探测：返回 {工程:函数: 参数表}，开始节点的入参面板靠它展示真实签名。

    探测的是 body 里的草稿 doc，不落盘；工程路径不可导入时该函数值为 {"error": …}。
    """
    get_flow_or_404(db, flow_id)
    if probe_doc is None:
        raise HTTPException(status_code=503, detail="编译引擎尚未就绪")
    try:
        return probe_doc(submitted_doc(payload.doc)) or {}
    except Exception as exc:  # 探针本身异常也不能让设计页 500
        logger.warning("探测失败（flow=%s）：%s", flow_id, exc)
        raise HTTPException(
            status_code=422, detail=[{"level": "error", "message": f"接口探测失败：{exc}"}]
        ) from exc


# 目录接口是本模块的从属路由，由 main 统一挂在 /api 下，避免 main 再引一个模块
router.include_router(folders.router)

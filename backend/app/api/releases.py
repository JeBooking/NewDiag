"""发布：把在线版本编译成不可变步骤表。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.flows import (
    LOCAL_ACTOR,
    current_revision,
    get_flow_or_404,
    revision_doc,
)
from app.db import get_db, new_id, now_iso
from app.models import Release, Revision
from app.schemas import ReleaseCreate, ReleaseDetail, ReleaseSummary, make_release_detail, make_release_summary

try:  # 引擎层缺席时仍允许后端启动
    from app.services.compiler import ValidationError, compile_doc
except ImportError as exc:  # pragma: no cover
    logging.getLogger(__name__).warning("编译器不可用，发布接口将返回 503：%s", exc)
    compile_doc = None  # type: ignore[assignment]

    class ValidationError(Exception):  # type: ignore[no-redef]
        issues: list[dict] = []


try:  # 探测不到就发布不了，见 probe_issues
    from app.services.signatures import collect_signatures, probe_doc
except ImportError as exc:  # pragma: no cover
    logging.getLogger(__name__).warning("签名探测不可用，发布接口将返回 503：%s", exc)
    collect_signatures = None  # type: ignore[assignment]
    probe_doc = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
router = APIRouter(tags=["releases"])


def probe_issues(probed: dict) -> list[dict]:
    """probe_doc 对失败的工程/函数塞 {"error": …}，一律当错误拦下。

    探测失败意味着「不知道函数要什么参数」，放过去等于把错误推迟到执行期，
    所以这里不降级为 warning。
    """
    issues: list[dict] = []
    for key, value in probed.items():
        if isinstance(value, list):
            continue
        detail = value.get("error") if isinstance(value, dict) else None
        issues.append(
            {
                "level": "error",
                "message": f"接口探测失败（{key}）：{detail or '探测结果格式异常'}",
            }
        )
    return issues


@router.post("/flows/{flow_id}/releases", response_model=ReleaseSummary)
def publish(flow_id: str, payload: ReleaseCreate, db: Session = Depends(get_db)) -> ReleaseSummary:
    flow = get_flow_or_404(db, flow_id)
    revision = current_revision(db, flow)
    if revision is None:
        raise HTTPException(status_code=409, detail="流程还没有在线版本")
    if compile_doc is None or probe_doc is None:
        raise HTTPException(status_code=503, detail="编译引擎尚未就绪")

    doc = revision_doc(revision)
    # 先按 doc 里的 binding 探一遍真实签名：编译期靠它过滤参数，探测失败直接拦发布
    try:
        probed = probe_doc(doc)
    except Exception as exc:
        logger.warning("接口探测异常（flow=%s）：%s", flow_id, exc)
        raise HTTPException(
            status_code=422, detail=[{"level": "error", "message": f"接口探测失败：{exc}"}]
        ) from exc

    issues = probe_issues(probed)
    if issues:
        raise HTTPException(status_code=422, detail=issues)

    version_no = (db.scalar(select(func.max(Release.version_no)).where(Release.flow_id == flow_id)) or 0) + 1
    try:
        plan = compile_doc(doc, flow.id, version_no, functions=probed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=getattr(exc, "issues", []) or []) from exc
    except Exception as exc:  # 编译器的其他失败也按校验失败回给作者
        logger.warning("编译失败（flow=%s）：%s", flow_id, exc)
        raise HTTPException(status_code=422, detail=[{"level": "error", "message": str(exc)}]) from exc

    if not isinstance(plan, dict):
        raise HTTPException(status_code=422, detail=[{"level": "error", "message": "编译产物不是对象"}])

    signature: dict[str, str] = {}
    if collect_signatures is not None:
        try:
            signature = collect_signatures(plan, probed) or {}
        except Exception as exc:  # 签名表缺失不阻塞发布，执行前还会再比对
            logger.warning("签名采集失败（flow=%s）：%s", flow_id, exc)

    release = Release(
        id=new_id(),
        flow_id=flow.id,
        revision_id=revision.id,
        version_no=version_no,
        plan_json=json.dumps(plan, ensure_ascii=False),
        signature_json=json.dumps(signature, ensure_ascii=False),
        published_by=LOCAL_ACTOR,
        published_at=now_iso(),
        note=payload.note,
    )
    db.add(release)
    flow.updated_at = release.published_at
    db.commit()
    db.refresh(release)
    logger.info(
        "发布 flow=%s 版本 v%s：%d 步，%d 个函数签名，note=%s",
        flow_id,
        version_no,
        len(plan.get("steps") or {}),
        len(signature),
        (payload.note or "")[:60],
    )
    return make_release_summary(release)


@router.get("/flows/{flow_id}/releases", response_model=list[ReleaseSummary])
def list_releases(flow_id: str, db: Session = Depends(get_db)) -> list[ReleaseSummary]:
    get_flow_or_404(db, flow_id)
    releases = db.scalars(
        select(Release).where(Release.flow_id == flow_id).order_by(Release.version_no.desc())
    )
    return [make_release_summary(item) for item in releases]


@router.get("/releases/{release_id}", response_model=ReleaseDetail)
def read_release(release_id: str, db: Session = Depends(get_db)) -> ReleaseDetail:
    release = db.get(Release, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="发布版本不存在")
    revision = db.get(Revision, release.revision_id)
    return make_release_detail(release, revision_doc(revision))

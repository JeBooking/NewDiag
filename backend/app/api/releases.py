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
from app.worker.input_types import INPUT_TYPES, input_type
from app.schemas import ReleaseCreate, ReleaseDetail, ReleaseSummary, make_release_detail, make_release_summary

try:  # 引擎层缺席时仍允许后端启动
    from app.services.compiler import StartParams, ValidationError, compile_doc
except ImportError as exc:  # pragma: no cover
    logging.getLogger(__name__).warning("编译器不可用，发布接口将返回 503：%s", exc)
    compile_doc = None  # type: ignore[assignment]
    StartParams = None  # type: ignore[assignment]

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

PARAM_TYPES = INPUT_TYPES
# 注解只决定输入控件，不追求类型系统完备：认不出的都当文本框
ANNOTATION_TO_PARAM = {
    "date": "datetime",
    "datetime": "datetime",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "boolean": "boolean",
}


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


def start_node(doc: dict) -> dict | None:
    """doc 里唯一的开始节点；多于一个时取第一个并记警告（合法性由校验兜）。"""
    nodes = [n for n in (doc.get("nodes") or []) if isinstance(n, dict) and n.get("kind") == "start"]
    if len(nodes) > 1:
        logger.warning("文档里有 %d 个开始节点，只冻结第一个的启动参数", len(nodes))
    return nodes[0] if nodes else None


def _signature_index(probed: dict) -> tuple[dict[str, list[dict]], dict[str, dict[str, dict]]]:
    """把 probe_doc 的结果拆成「全局按参数名」和「按函数」两张索引。"""
    by_name: dict[str, list[dict]] = {}
    by_function: dict[str, dict[str, dict]] = {}
    for key, value in (probed or {}).items():
        if not isinstance(value, list):
            continue
        function = str(key).rpartition(":")[2]
        table = by_function.setdefault(function, {})
        for param in value:
            if not isinstance(param, dict):
                continue
            name = str(param.get("name") or "")
            if not name:
                continue
            table[name] = param
            by_name.setdefault(name, []).append(param)
    return by_name, by_function


def _declared_type(value) -> str | None:
    text = str(value or "").strip()
    return text if text in PARAM_TYPES else None


def _annotation_type(annotation) -> str:
    text = str(annotation or "").strip().lower().replace(" ", "")
    # Optional[X] / X | None 只是可空标记，控件类型仍看 X
    if text.startswith("optional[") and text.endswith("]"):
        text = text[len("optional[") : -1]
    if text.endswith("|none"):
        text = text.rsplit("|", 1)[0]
    if text.startswith("none|"):
        text = text.split("|", 1)[1]
    return ANNOTATION_TO_PARAM.get(text, input_type(text))


def freeze_inputs(doc: dict, probed: dict) -> list[dict]:
    """把开始节点声明的参数与真实签名合成执行页的启动表单。

    参数清单直接用编译器的 StartParams，保证「发布时冻结的表单」和「编译期绑定的来源」
    是同一份口径（老文档的 snapshotFields 也会被当作全局参数）。
    冻结的意义：发布后改 doc 或改函数签名，老版本的表单不能跟着变。
    required 取签名该参数有无默认值；作者在设计期给了默认值就不算必填，执行页预填即可。
    签名默认值只有 defaultTrusted 时才写进表单：repr 兜底的值不是真值，写进去等于预填假数据。
    """
    specs = StartParams(doc).items
    if not specs:
        return []

    declared = _declared_inputs(start_node(doc))
    by_name, by_function = _signature_index(probed)
    frozen: list[dict] = []
    for spec in specs:
        name = str(spec.get("name") or "")
        if not name:
            continue
        scope = "function" if spec.get("scope") == "function" else "global"
        function = (str(spec.get("function") or "").strip() or None) if scope == "function" else None
        if function and _runtime_only(doc, function, name):
            continue

        item = declared.get((scope, function or "", name), {})
        has_declared = "default" in item
        declared_default = item.get("default")

        if scope == "function" and function:
            signature = (by_function.get(function) or {}).get(name)
            matched = [signature] if signature else []
        else:
            matched = by_name.get(name, [])

        if matched:
            required = any(bool(param.get("required", True)) for param in matched)
            annotation = next((param.get("type") for param in matched if param.get("type")), None)
            # 探针用 repr 兜底的默认值（datetime / 枚举 / 自定义对象）不是真值，
            # 冻结进表单只会在执行页预填一个假值再静默传给函数，所以宁可不写
            signature_default = next(
                (
                    param.get("default")
                    for param in matched
                    if not param.get("required", True) and param.get("defaultTrusted", True)
                ),
                None,
            )
        else:
            # 签名里查不到（函数没被绑定 / 探不到），只能按作者是否给了默认值判断
            required = True
            annotation = None
            signature_default = None

        frozen.append(
            {
                "name": name,
                "scope": scope,
                "function": function,
                "type": _declared_type(item.get("type")) or _annotation_type(annotation),
                "required": required and not has_declared,
                "default": declared_default if has_declared else signature_default,
            }
        )
    return frozen


def _runtime_only(doc: dict, function: str, name: str) -> bool:
    diagnosis = doc.get("diagnosis") or {}
    nodes = [node for node in doc.get("nodes", [])
             if node.get("method") and function == f"{diagnosis.get('function')}.{node['method']}"]
    # 同一方法的其他节点仍可能在启动时取参，不能因为一个节点改成现场输入就一起移除。
    return bool(nodes) and all(any(field.get("name") == name
                                  for field in node.get("runtimeInputs", [])) for node in nodes)


def _declared_inputs(node: dict | None) -> dict[tuple[str, str, str], dict]:
    """开始节点原始 inputs，按 (scope, function, name) 索引，供冻结时取作者填的类型与默认值。"""
    raw = (node or {}).get("inputs")
    result: dict[tuple[str, str, str], dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        scope = "function" if item.get("scope") == "function" else "global"
        function = str(item.get("function") or "").strip() if scope == "function" else ""
        result.setdefault((scope, function, name), item)
    return result


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

    # 启动表单在这里定稿：执行页只读它，之后改 doc 或改签名都不会影响已发布版本
    inputs = freeze_inputs(doc, probed)

    release = Release(
        id=new_id(),
        flow_id=flow.id,
        revision_id=revision.id,
        version_no=version_no,
        plan_json=json.dumps(plan, ensure_ascii=False),
        signature_json=json.dumps(signature, ensure_ascii=False),
        inputs_json=json.dumps(inputs, ensure_ascii=False),
        published_by=LOCAL_ACTOR,
        published_at=now_iso(),
        note=payload.note,
    )
    db.add(release)
    flow.updated_at = release.published_at
    db.commit()
    db.refresh(release)
    logger.info(
        "发布 flow=%s 版本 v%s：%d 步，%d 个函数签名，%d 个启动参数，note=%s",
        flow_id,
        version_no,
        len(plan.get("steps") or {}),
        len(signature),
        len(inputs),
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

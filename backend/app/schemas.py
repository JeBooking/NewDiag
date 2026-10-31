"""请求 / 响应模型。

对外一律 camelCase（与 src/domain/types.ts 对齐），内部 snake_case。
行对象到响应对象的转换集中在文件末尾的 make_* 函数里，避免别名推断踩坑。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.models import Flow, Instance, Project, Release, Review, Run, Snapshot, Token

FlowMode = Literal["parallel", "serial"]
NodeKind = Literal[
    "investigation",
    "investigationAction",
    "conclusion",
    "remediation",
    "problemSolved",
    "escalation",
    "deepDiagnosis",
    "jump",
]
Branch = Literal["yes", "no"]
InstanceStatus = Literal["running", "waiting", "paused", "success", "failed", "escalated"]
TokenStatus = Literal["queued", "running", "waiting", "done", "failed", "abandoned"]
RunStatus = Literal["ok", "failed", "abandoned"]
ReviewKind = Literal["decision", "action"]
ReviewStatus = Literal["pending", "done", "cancelled"]
ReviewResult = Literal["yes", "no", "done", "rollback", "continue"]


def to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word[:1].upper() + word[1:] for word in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        serialize_by_alias=True,
    )


# ---------------------------------------------------------------- 流程


class AttachmentRef(ApiModel):
    hash: str
    name: str


class FlowSummary(ApiModel):
    id: str
    name: str
    description: str = ""
    mode: FlowMode = "parallel"
    updated_at: str
    editing_by: str | None = None
    release_count: int = 0
    last_run_status: InstanceStatus | None = None
    last_published_at: str | None = None


class FlowDetail(FlowSummary):
    doc: dict[str, Any] = Field(default_factory=dict)
    current_revision_id: str | None = None
    working_revision_id: str | None = None
    releases: list["ReleaseSummary"] = Field(default_factory=list)


class FlowCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    mode: FlowMode = "parallel"


class FlowPatch(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    mode: FlowMode | None = None


class WorkingDocIn(ApiModel):
    doc: dict[str, Any]


class ImportFlowIn(ApiModel):
    """导入流程：body 携带完整 doc，一次建流程并写入在线版本。"""

    doc: dict[str, Any]


class SavedAt(ApiModel):
    revision_id: str
    saved_at: str


class CommitResult(FlowDetail):
    """提交响应：前端拿到新版本号即可，同时保留完整流程详情。

    `issues` 只装 warning 级问题（保存放过的半成品提示），阻断级问题走 422 detail。
    """

    revision_id: str | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------- 发布


class ReleaseSummary(ApiModel):
    id: str
    version_no: int
    published_at: str
    published_by: str
    note: str | None = None


class ReleaseDetail(ReleaseSummary):
    flow_id: str
    revision_id: str
    plan: dict[str, Any] = Field(default_factory=dict)
    signature: dict[str, str] = Field(default_factory=dict)
    # 执行页用发布时的 doc 渲染只读画布，避免流程后来被改过导致图与步骤不一致
    doc: dict[str, Any] | None = None


class ReleaseCreate(ApiModel):
    note: str | None = None


# ---------------------------------------------------------------- 实例


class TokenOut(ApiModel):
    id: str
    node_id: str
    status: TokenStatus
    branch: Branch | None = None
    order: int | None = None
    generation: int = 1
    parent_token_id: str | None = None


class RunOut(ApiModel):
    id: str
    node_id: str
    token_id: str | None = None
    generation: int = 1
    attempt: int = 1
    status: RunStatus = "ok"
    input: Any = None
    output: Any = None
    evidence: Any = None
    error: str | None = None
    started_at: str
    ended_at: str | None = None


class ReviewTask(ApiModel):
    id: str
    node_id: str
    kind: ReviewKind
    status: ReviewStatus
    request: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    decision: dict[str, Any] | None = None


class SnapshotOut(ApiModel):
    id: str
    seq: int = 1
    payload: Any = None
    source: str | None = None
    captured_at: str


class InstanceSummary(ApiModel):
    id: str
    flow_id: str
    flow_name: str | None = None
    release_id: str
    release_no: int | None = None
    status: InstanceStatus
    started_at: str
    ended_at: str | None = None
    started_by: str = "local"


class InstanceDetail(InstanceSummary):
    tokens: list[TokenOut] = Field(default_factory=list)
    runs: list[RunOut] = Field(default_factory=list)
    reviews: list[ReviewTask] = Field(default_factory=list)
    snapshot: SnapshotOut | None = None


class InstanceCreate(ApiModel):
    release_id: str | None = None
    input: Any = None


class ReviewDecisionIn(ApiModel):
    result: ReviewResult
    target: str | None = None
    reason: str | None = None


class RollbackIn(ApiModel):
    # 前端用 targetNodeId，spec 写 target，两个都收
    target: str = Field(validation_alias=AliasChoices("target", "targetNodeId"))
    # 分支键由目标节点类型决定：排查是 yes/no，其余是 default（引擎侧归一，这里只挡空值）
    branch: str = Field(min_length=1)
    reason: str | None = None


# ---------------------------------------------------------------- 工程 / 文件


class ProjectParam(ApiModel):
    name: str
    type: str | None = None
    default: Any = None
    required: bool = True


class ProjectFunction(ApiModel):
    name: str
    params: list[ProjectParam] = Field(default_factory=list)
    returns: str | None = None


class ProjectOut(ApiModel):
    id: str
    name: str
    path: str
    module: str
    python: str | None = None
    factory: str | None = None
    created_at: str


class ProjectCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1)
    module: str = Field(min_length=1, max_length=200)
    python: str | None = None
    factory: str | None = None


class ProjectFunctions(ApiModel):
    functions: list[ProjectFunction] = Field(default_factory=list)
    error: str | None = None


class FileOut(ApiModel):
    hash: str
    name: str
    media_type: str
    size: int


# ---------------------------------------------------------------- 转换


_MISSING = object()


def _loads(raw: str | None, fallback: Any = _MISSING) -> Any:
    empty = {} if fallback is _MISSING else fallback
    if not raw:
        return empty
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return empty


def make_flow_summary(
    flow: Flow,
    release_count: int = 0,
    last_run_status: str | None = None,
    last_published_at: str | None = None,
) -> FlowSummary:
    return FlowSummary(
        id=flow.id,
        name=flow.name,
        description=flow.description or "",
        mode=flow.mode,  # type: ignore[arg-type]
        updated_at=flow.updated_at,
        editing_by=flow.editing_by,
        release_count=release_count,
        last_run_status=last_run_status,  # type: ignore[arg-type]
        last_published_at=last_published_at,
    )


def make_flow_detail(
    flow: Flow,
    doc: dict[str, Any],
    releases: list[Release],
    release_count: int = 0,
    last_run_status: str | None = None,
    working_revision_id: str | None = None,
) -> FlowDetail:
    last_published = max((item.published_at for item in releases), default=None)
    summary = make_flow_summary(flow, release_count, last_run_status, last_published)
    return FlowDetail(
        **summary.model_dump(),
        doc=doc,
        current_revision_id=flow.current_revision_id,
        working_revision_id=working_revision_id,
        releases=[make_release_summary(item) for item in releases],
    )


def make_release_summary(release: Release) -> ReleaseSummary:
    return ReleaseSummary(
        id=release.id,
        version_no=release.version_no,
        published_at=release.published_at,
        published_by=release.published_by,
        note=release.note,
    )


def make_release_detail(release: Release, doc: dict[str, Any] | None = None) -> ReleaseDetail:
    return ReleaseDetail(
        **make_release_summary(release).model_dump(),
        flow_id=release.flow_id,
        revision_id=release.revision_id,
        plan=_loads(release.plan_json),
        signature=_loads(release.signature_json),
        doc=doc,
    )


def make_token(token: Token) -> TokenOut:
    return TokenOut(
        id=token.id,
        node_id=token.node_id,
        status=token.status,  # type: ignore[arg-type]
        branch=token.branch,  # type: ignore[arg-type]
        order=token.sort_order,
        generation=token.generation,
        parent_token_id=token.parent_token_id,
    )


def make_run(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        node_id=run.node_id,
        token_id=run.token_id,
        generation=run.generation,
        attempt=run.attempt,
        status=run.status,  # type: ignore[arg-type]
        input=_loads(run.input_json, None),
        output=_loads(run.output_json, None),
        evidence=_loads(run.evidence_json, None),
        error=run.error,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )


def make_review(review: Review) -> ReviewTask:
    decision = _loads(review.decision_json, None) if review.decision_json else None
    return ReviewTask(
        id=review.id,
        node_id=review.node_id,
        kind=review.kind,  # type: ignore[arg-type]
        status=review.status,  # type: ignore[arg-type]
        request=_loads(review.request_json),
        created_at=review.created_at,
        decided_at=review.decided_at,
        decided_by=review.decided_by,
        decision=decision,
    )


def make_snapshot(snapshot: Snapshot) -> SnapshotOut:
    return SnapshotOut(
        id=snapshot.id,
        seq=snapshot.seq,
        payload=_loads(snapshot.payload_json),
        source=snapshot.source,
        captured_at=snapshot.captured_at,
    )


def make_instance_summary(
    instance: Instance, flow_name: str | None = None, release_no: int | None = None
) -> InstanceSummary:
    return InstanceSummary(
        id=instance.id,
        flow_id=instance.flow_id,
        flow_name=flow_name,
        release_id=instance.release_id,
        release_no=release_no,
        status=instance.status,  # type: ignore[arg-type]
        started_at=instance.started_at,
        ended_at=instance.ended_at,
        started_by=instance.started_by,
    )


def make_instance_detail(
    instance: Instance,
    tokens: list[Token],
    runs: list[Run],
    reviews: list[Review],
    snapshot: Snapshot | None,
    flow_name: str | None = None,
    release_no: int | None = None,
) -> InstanceDetail:
    summary = make_instance_summary(instance, flow_name, release_no)
    return InstanceDetail(
        **summary.model_dump(),
        tokens=[make_token(item) for item in tokens],
        runs=[make_run(item) for item in runs],
        reviews=[make_review(item) for item in reviews],
        snapshot=make_snapshot(snapshot) if snapshot else None,
    )


def make_project(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        path=project.path,
        module=project.module,
        python=project.python,
        factory=project.factory,
        created_at=project.created_at,
    )


FlowDetail.model_rebuild()

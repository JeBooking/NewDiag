"""与前端 src/domain/kinds.ts、src/domain/rules.ts 一一对应的连接与必填规则。

两端共用同一张表，避免「前端放过、后端拒绝」。规则改动必须同时改两处。
节点类型的属性（是否分是/否、是否单后继、是否终止）只写在 KIND_SPECS 里，
上面的集合与判定函数全部由它派生，不再按类型名散落写死。
"""

from __future__ import annotations

# 连接矩阵：key 为来源类型，value 为允许的目标类型。
# start 只用于入口判定，generic 只用于非诊断元素的边界（本模型无此节点）。
CONNECTION_RULES: dict[str, list[str]] = {
    "start": ["investigation", "generic"],
    "generic": ["remediation", "problemSolved", "escalation", "generic"],
    "investigation": ["investigation", "investigationAction", "conclusion", "escalation"],
    "investigationAction": ["investigation"],
    "conclusion": ["remediation", "escalation"],
    "remediation": ["remediation", "problemSolved", "escalation"],
    "problemSolved": [],
    "escalation": [],
}

START_KIND = "start"

# 单个类型的出线语义与能力声明，对应前端 KindMeta。
# deepDiagnosis / jump 已在前端 KIND_META 里声明，但 V1 面板不暴露，矩阵里也没有它们，
# 因此导入含这两种节点的文档仍会在编译期被拒绝（快速失败，不留到执行时）。
KIND_SPECS: dict[str, dict[str, bool | str]] = {
    START_KIND: {"branch": "single", "entry": True},
    "investigation": {"branch": "yesno", "code_capable": True},
    "investigationAction": {"branch": "single", "human_only": True},
    "conclusion": {"branch": "single", "single_successor": True},
    "remediation": {"branch": "single", "human_only": True},
    "problemSolved": {"branch": "none", "terminal": True, "success": True},
    "escalation": {"branch": "none", "terminal": True},
    "deepDiagnosis": {"branch": "single"},
    "jump": {"branch": "single"},
}

V1_KINDS = (
    START_KIND,
    "investigation",
    "investigationAction",
    "conclusion",
    "remediation",
    "problemSolved",
    "escalation",
)

TERMINAL_KINDS = frozenset(k for k, spec in KIND_SPECS.items() if spec.get("terminal"))
SUCCESS_KINDS = frozenset(k for k, spec in KIND_SPECS.items() if spec.get("success"))
# 只由人完成、不调代码的节点
HUMAN_ONLY_KINDS = frozenset(k for k, spec in KIND_SPECS.items() if spec.get("human_only"))
# 可绑定 Python 函数的节点
CODE_CAPABLE_KINDS = frozenset(k for k, spec in KIND_SPECS.items() if spec.get("code_capable"))

BRANCHES = ("yes", "no")
DEFAULT_BRANCH = "default"

# 人工节点必须填写的说明字段
REQUIRED_TEXT = {"title": "标题", "detail": "说明"}
# 排查节点必须填写的四个描述字段（title/detail + fields 三项）
INVESTIGATION_FIELDS = {
    "observation": "观测对象",
    "acquisition": "取数方式",
    "assessment": "判定依据",
}


def can_connect(from_kind: str, to_kind: str) -> bool:
    targets = CONNECTION_RULES.get(from_kind)
    if targets is None:
        return False
    return to_kind in targets


def is_terminal_kind(kind: str) -> bool:
    return kind in TERMINAL_KINDS


def is_success_kind(kind: str) -> bool:
    return kind in SUCCESS_KINDS


def is_start_kind(kind: str) -> bool:
    return kind == START_KIND


def is_start_param_scope(scope: object) -> bool:
    return scope in ("global", "function")


def needs_branch(kind: str) -> bool:
    """排查按是/否两组出线，其余节点单出口。判定与前端 KIND_META.branch 同源。"""
    return KIND_SPECS.get(kind, {}).get("branch") == "yesno"


def allows_multiple_targets(kind: str) -> bool:
    """开始节点把出边一次性全部投递，不算隐式分叉。"""
    return kind == START_KIND


def is_single_successor(kind: str) -> bool:
    """结论只允许一条出边，其余节点可多条（诊断节点按自身语义豁免）。"""
    return bool(KIND_SPECS.get(kind, {}).get("single_successor"))


def outgoing_groups(edges: list[dict], node_id: str, kind: str) -> dict[str, list[dict]]:
    """按分支把出边分组，组内保持 order 升序（未填 order 按数组顺序）。"""
    indexed = [(i, e) for i, e in enumerate(edges) if e.get("from") == node_id]
    indexed.sort(key=lambda pair: (_order_key(pair[1]), pair[0]))
    groups: dict[str, list[dict]] = {}
    for _, edge in indexed:
        key = (edge.get("branch") or DEFAULT_BRANCH) if needs_branch(kind) else DEFAULT_BRANCH
        groups.setdefault(key, []).append(edge)
    return groups


def _order_key(edge: dict) -> float:
    order = edge.get("order")
    return order if isinstance(order, (int, float)) else float("inf")

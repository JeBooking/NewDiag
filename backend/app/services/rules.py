"""与前端 src/domain/rules.ts 一一对应的连接与必填规则。

两端共用同一张表，避免「前端放过、后端拒绝」。规则改动必须同时改两处。
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

V1_KINDS = (
    START_KIND,
    "investigation",
    "investigationAction",
    "conclusion",
    "remediation",
    "problemSolved",
    "escalation",
)

TERMINAL_KINDS = frozenset({"problemSolved", "escalation"})
SUCCESS_KINDS = frozenset({"problemSolved"})
# 只由人完成、不调代码的节点
HUMAN_ONLY_KINDS = frozenset({"investigationAction", "remediation"})
# 可绑定 Python 函数的节点
CODE_CAPABLE_KINDS = frozenset({"investigation"})

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
    """排查按是/否两组出线，其余节点单出口。"""
    return kind == "investigation"


def allows_multiple_targets(kind: str) -> bool:
    """开始节点把出边一次性全部投递，不算隐式分叉。"""
    return kind == START_KIND


def is_single_successor(kind: str) -> bool:
    """结论只允许一条出边，其余节点可多条（诊断节点按自身语义豁免）。"""
    return kind == "conclusion"


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

"""与前端 src/domain/kinds.ts、src/domain/rules.ts 一一对应的连接与必填规则。

两端共用同一张表，避免「前端放过、后端拒绝」。规则改动必须同时改两处。
节点类型的属性（是否分是/否、是否单后继、是否终止）只写在 KIND_SPECS 里，
上面的集合与判定函数全部由它派生，不再按类型名散落写死。
"""

from __future__ import annotations

# 连接矩阵：key 为来源类型，value 为允许的目标类型。
# start 只用于入口判定。
CONNECTION_RULES: dict[str, list[str]] = {
    "start": ["investigation", "category"],
    # 分类节点是可读的分流说明：出线只能落到排查节点，由人挑一条
    "category": ["investigation"],
    "investigation": [
        "investigation", "category", "investigationAction", "conclusion", "escalation", "jump",
    ],
    "investigationAction": ["investigation", "category"],
    "conclusion": ["remediation", "escalation", "jump"],
    "remediation": ["remediation", "problemSolved", "escalation", "jump"],
    "problemSolved": [],
    "escalation": [],
    # 转交成功后可以接除 start 以外的任意节点类型；转交失败由人工待办回退，不另设分支
    "jump": [
        "investigation",
        "category",
        "investigationAction",
        "conclusion",
        "remediation",
        "problemSolved",
        "escalation",
        "jump",
    ],
}

START_KIND = "start"
CATEGORY_KIND = "category"

# 单个类型的出线语义与能力声明，对应前端 KindMeta。
KIND_SPECS: dict[str, dict[str, bool | str]] = {
    START_KIND: {"branch": "single", "entry": True},
    # 分类纯文本分流：出线全走 default（branch=single 即不分组），多条由 allows_multiple_targets 放行
    "category": {"branch": "single", "human_only": True},
    "investigation": {"branch": "yesno", "code_capable": True},
    "investigationAction": {"branch": "single", "human_only": True},
    "conclusion": {"branch": "single", "single_successor": True},
    "remediation": {"branch": "single", "human_only": True},
    "problemSolved": {"branch": "none", "terminal": True, "success": True},
    "escalation": {"branch": "none", "terminal": True},
    # 转交给另一条诊断链路：当前是人工确认的文本节点，确认成功后接任意非 start 节点
    "jump": {"branch": "single", "human_only": True, "single_successor": True},
}

V1_KINDS = (
    START_KIND,
    "category",
    "investigation",
    "investigationAction",
    "conclusion",
    "remediation",
    "jump",
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


def needs_branch(kind: str) -> bool:
    """排查按是/否两组出线，其余节点单出口。判定与前端 KIND_META.branch 同源。"""
    return KIND_SPECS.get(kind, {}).get("branch") == "yesno"


def allows_multiple_targets(kind: str) -> bool:
    """开始节点把出边一次性全部投递，不算隐式分叉；分类节点同理，出线是给人的选项。"""
    return kind in (START_KIND, CATEGORY_KIND)


def is_category_kind(kind: str) -> bool:
    return kind == CATEGORY_KIND


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

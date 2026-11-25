"""doc → plan 校验与编译（spec 第 4、5、6 节）。

发布时执行一次，产物不可变；执行期不再读 doc。
v1.17 起只有一种接入方式：节点槽位，每个槽位填一个外接工程的公开方法。
"""

from __future__ import annotations

import logging

from . import rules
from .slots import calls_in_node, freeze_slots, slots_of, validate as validate_slots, walk_argument
from app.worker.input_types import INPUT_TYPES

logger = logging.getLogger(__name__)

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"


class ValidationError(Exception):
    """doc 校验未通过。issues 与前端 LintIssue 对齐。"""

    def __init__(self, issues: list[dict]):
        self.issues = list(issues)
        message = "；".join(str(i.get("message", "")) for i in self.issues)
        super().__init__(message or "文档校验未通过")


def validate_doc(
    doc: dict,
    functions: dict | None = None,
    strict_binding: bool = True,
) -> list[dict]:
    """返回 issue 列表；level=error 才算阻断。

    strict_binding=False 供 commit 使用：槽位问题降级为 warning，半成品能存。
    """
    issues: list[dict] = []
    if not isinstance(doc, dict):
        return [_issue(LEVEL_ERROR, "文档格式不正确")]

    raw_nodes = doc.get("nodes")
    raw_edges = doc.get("edges")
    nodes = [n for n in raw_nodes if isinstance(n, dict)] if isinstance(raw_nodes, list) else []
    edges = [e for e in raw_edges if isinstance(e, dict)] if isinstance(raw_edges, list) else []

    nodes_by_id: dict[str, dict] = {}
    for node in nodes:
        nid = node.get("id")
        if not nid or not isinstance(nid, str):
            issues.append(_issue(LEVEL_ERROR, "节点缺少 id"))
            continue
        if nid in nodes_by_id:
            issues.append(_issue(LEVEL_ERROR, f"节点 id 重复：{nid}", node_id=nid))
            continue
        nodes_by_id[nid] = node
    if not nodes_by_id:
        issues.append(_issue(LEVEL_ERROR, "文档至少需要一个排查节点"))
        return issues

    # 空值不算旧格式：清掉内容的残留字段不该把流程永久锁死
    if doc.get("diagnosis") or doc.get("snapshotFields"):
        issues.append(_issue(LEVEL_ERROR, "这是旧格式流程：诊断类与快照字段已移除，请用节点槽位重新编排"))
    for nid, node in nodes_by_id.items():
        kind = node.get("kind")
        if node.get("runtimeInputs") and not node.get("slots"):
            issues.append(_issue(LEVEL_ERROR, "节点现场参数需要先配置槽位", node_id=nid))
        if kind == "dataPreparation":
            issues.append(_issue(LEVEL_ERROR, "数据准备节点已移除：取数请配置在开始节点或排查节点的拉取槽", node_id=nid))
        elif kind not in rules.V1_KINDS:
            issues.append(_issue(LEVEL_ERROR, f"v1 不支持的节点类型：{kind}", node_id=nid))
        legacy = [key for key in ("binding", "pipeline", "method", "params", "outputs",
                                  "operation", "operationArguments") if node.get(key)]
        if legacy:
            issues.append(_issue(
                LEVEL_ERROR,
                f"节点仍在使用已移除的旧接入字段（{', '.join(legacy)}）：请改为在槽位里选择接口",
                node_id=nid,
            ))
        if doc.get("composition"):
            issues.append(_issue(LEVEL_ERROR, "流程级的对象／资源／操作配置已移除，请改用节点槽位"))

    valid_edges = _check_edges(edges, nodes_by_id, issues)
    shaped_edges = [e for e in edges if e.get("from") in nodes_by_id and e.get("to") in nodes_by_id]
    outgoing = _outgoing_map(nodes_by_id, valid_edges)
    _check_node_fields(nodes_by_id, issues)
    _check_out_edges(nodes_by_id, valid_edges, outgoing, issues)
    entry = _check_start(nodes_by_id, shaped_edges, valid_edges, issues)
    if entry is not None:
        _check_reachability(entry, nodes_by_id, outgoing, issues)
        _check_cycles(entry, outgoing, issues)
    start = StartParams(doc)
    found = _check_slots(doc, nodes_by_id, start, functions)
    for issue in found:
        if not strict_binding and issue.get("level") == LEVEL_ERROR:
            issue = {**issue, "level": LEVEL_WARNING}
        issues.append(issue)
    return issues


class NodeDefinition:
    """节点类只编译执行描述，人工等待和持久化仍由调度器负责。"""

    def compile(self, node, nodes, edges, start, functions):
        step = {"kind": node["kind"], "next": _next_map(node["id"], node["kind"], edges)}
        if node.get("runtimeInputs"):
            # 现场参数在执行前收集，槽位里的 parameter 来源按名字取
            step["runtimeInputs"] = node["runtimeInputs"]
        return step


class StartNode(NodeDefinition):
    def compile(self, node, nodes, edges, start, functions):
        step = super().compile(node, nodes, edges, start, functions)
        slots = {key: value for key, value in slots_of(node).items()
                 if key in ("constructs", "fetch", "process")}
        if slots:
            step["slots"] = slots
        return step


class InvestigationNode(NodeDefinition):
    def compile(self, node, nodes, edges, start, functions):
        step = super().compile(node, nodes, edges, start, functions)
        slots = {key: value for key, value in slots_of(node).items()
                 if key in ("fetch", "process", "present", "decide")}
        if slots:
            step["slots"] = slots
        else:
            # 没有任何槽位的历史文档按人工节点执行
            step["human"] = True
        return step


class HumanNode(NodeDefinition):
    def compile(self, node, nodes, edges, start, functions):
        return {**super().compile(node, nodes, edges, start, functions), "human": True}


class CategoryNode(NodeDefinition):
    """纯文本分流：没有槽位、不跑代码，只有拓扑。文案由执行侧从 doc 取。"""

    def compile(self, node, nodes, edges, start, functions):
        return {"kind": node["kind"], "next": _next_map(node["id"], node["kind"], edges)}


class InvestigationActionNode(HumanNode):
    pass


class RemediationNode(HumanNode):
    pass


class JumpNode(HumanNode):
    pass


class ConclusionNode(NodeDefinition):
    def compile(self, node, nodes, edges, start, functions):
        return {**super().compile(node, nodes, edges, start, functions),
                "text": _text(node.get("detail")) or _text(node.get("title"))}


class TerminalNode(NodeDefinition):
    def compile(self, node, nodes, edges, start, functions):
        return {"kind": node["kind"], "terminal": True}


class ProblemSolvedNode(TerminalNode):
    pass


class EscalationNode(TerminalNode):
    pass


NODE_DEFINITIONS = {
    "start": StartNode(), "category": CategoryNode(), "investigation": InvestigationNode(),
    "investigationAction": InvestigationActionNode(), "remediation": RemediationNode(),
    "jump": JumpNode(), "conclusion": ConclusionNode(),
    "problemSolved": ProblemSolvedNode(), "escalation": EscalationNode(),
}


def compile_doc(
    doc: dict,
    flow_id: str,
    release_no: int,
    functions: dict | None = None,
) -> dict:
    """把 doc 编译成 plan。校验不过抛 ValidationError。

    functions 为 probe_doc 的结果；组合配置按它校验接口与参数。
    """
    issues = [
        i
        for i in validate_doc(doc, functions)
        if i.get("level") == LEVEL_ERROR
    ]
    if issues:
        raise ValidationError(issues)

    nodes_by_id = {n["id"]: n for n in doc["nodes"]}
    edges = [e for e in doc.get("edges", []) if isinstance(e, dict)]
    valid_edges = [e for e in edges if e.get("from") in nodes_by_id and e.get("to") in nodes_by_id]
    start = StartParams(doc)
    entry = start.id

    steps: dict[str, dict] = {}
    for nid in sorted(nodes_by_id):
        node = nodes_by_id[nid]
        steps[nid] = NODE_DEFINITIONS[node["kind"]].compile(
            node, nodes_by_id, valid_edges, start, functions,
        )

    logger.info(
        "编译 flow=%s release=%s：%d 个节点，%d 个人工节点",
        flow_id,
        release_no,
        len(nodes_by_id),
        sum(1 for step in steps.values() if step.get("human")),
    )

    plan = {
        "format": 1,
        "flowId": flow_id,
        "release": release_no,
        "entry": entry,
        "steps": steps,
        "signature": {},
    }
    freeze_slots(plan, functions)
    return plan


# ---------------------------------------------------------------- 校验细节


def _issue(level: str, message: str, node_id: str | None = None, edge_id: str | None = None) -> dict:
    item: dict = {"level": level, "message": message}
    if node_id:
        item["nodeId"] = node_id
    if edge_id:
        item["edgeId"] = edge_id
    return item


def _check_edges(edges: list[dict], nodes_by_id: dict[str, dict], issues: list[dict]) -> list[dict]:
    """连边 id 唯一、端点存在、类型可连。返回可用边。"""
    seen: set[str] = set()
    valid: list[dict] = []
    for edge in edges:
        eid = edge.get("id")
        if not eid or not isinstance(eid, str):
            issues.append(_issue(LEVEL_ERROR, "连边缺少 id"))
            continue
        if eid in seen:
            issues.append(_issue(LEVEL_ERROR, f"连边 id 重复：{eid}", edge_id=eid))
            continue
        seen.add(eid)

        src, dst = edge.get("from"), edge.get("to")
        if src not in nodes_by_id:
            issues.append(_issue(LEVEL_ERROR, f"连边起点不存在：{src}", edge_id=eid))
            continue
        if dst not in nodes_by_id:
            issues.append(_issue(LEVEL_ERROR, f"连边终点不存在：{dst}", edge_id=eid))
            continue

        from_kind = nodes_by_id[src].get("kind", "")
        to_kind = nodes_by_id[dst].get("kind", "")
        if not rules.can_connect(from_kind, to_kind):
            # 分类节点的目标是它给人的选项，文案单列，避免和普通连线报错混在一起
            message = (
                "分类节点的出线只能连到排查节点"
                if rules.is_category_kind(from_kind)
                else f"不允许的连接：{_label(nodes_by_id[src])} → {_label(nodes_by_id[dst])}"
            )
            issues.append(_issue(LEVEL_ERROR, message, edge_id=eid))
            continue
        valid.append(edge)
    return valid


def _check_node_fields(nodes_by_id: dict[str, dict], issues: list[dict]) -> None:
    for nid, node in nodes_by_id.items():
        kind = node.get("kind", "")
        if rules.is_start_kind(kind):
            # 开始节点只有参数声明，没有诊断描述，标题/说明都不强制
            continue
        if rules.is_category_kind(kind):
            # 分类节点只是可读的分流说明：必须起标题，说明可以不填
            if not _text(node.get("title")):
                issues.append(_issue(LEVEL_ERROR, "分类节点缺少标题", node_id=nid))
            continue

        if not _text(node.get("title")):
            issues.append(_issue(LEVEL_ERROR, f"{_kind_label(kind)}缺少标题", node_id=nid))
        # 结论、上升、问题解决只写一句结论，正文不是必填
        if kind not in rules.TITLE_ONLY_KINDS:
            detail_label = rules.DETAIL_LABEL.get(kind, "说明")
            if not _text(node.get("detail")):
                issues.append(
                    _issue(LEVEL_ERROR, f"{_kind_label(kind)}缺少{detail_label}", node_id=nid)
                )

        if kind == "investigation":
            fields = node.get("fields")
            fields = fields if isinstance(fields, dict) else {}
            for key, label in rules.INVESTIGATION_FIELDS.items():
                if not _text(fields.get(key)):
                    issues.append(_issue(LEVEL_ERROR, f"排查节点缺少{label}", node_id=nid))


def _check_out_edges(
    nodes_by_id: dict[str, dict],
    edges: list[dict],
    outgoing: dict[str, list[str]],
    issues: list[dict],
) -> None:
    for nid, node in nodes_by_id.items():
        kind = node.get("kind", "")
        targets = outgoing.get(nid, [])
        out_edges = [e for e in edges if e.get("from") == nid]

        if rules.is_start_kind(kind):
            continue  # 出线由 _check_start 统一检查，避免重复报错

        if rules.is_category_kind(kind):
            _check_category_edges(nid, out_edges, issues)
            continue

        if rules.is_terminal_kind(kind):
            for edge in out_edges:
                issues.append(
                    _issue(LEVEL_ERROR, "终止节点不允许有出线", node_id=nid, edge_id=edge.get("id"))
                )
            continue

        if not out_edges:
            issues.append(_issue(LEVEL_ERROR, "节点至少需要一条出线", node_id=nid))
            continue

        if rules.needs_branch(kind):
            for edge in out_edges:
                branch = edge.get("branch")
                if branch not in rules.BRANCHES:
                    issues.append(
                        _issue(
                            LEVEL_ERROR,
                            "排查节点的出线必须指定是/否分支",
                            node_id=nid,
                            edge_id=edge.get("id"),
                        )
                    )
            # 同一节点两条平行边指向同一目标属于重复连接
            pairs = [(edge.get("to"), edge.get("branch")) for edge in out_edges]
            for pair in {p for p in pairs if pairs.count(p) > 1}:
                issues.append(
                    _issue(LEVEL_ERROR, f"重复连接：{pair[0]}（{pair[1]}）", node_id=nid)
                )
        elif rules.is_single_successor(kind):
            if len(out_edges) != 1:
                issues.append(
                    _issue(LEVEL_ERROR, f"{_kind_label(kind)}节点必须恰好一条出线", node_id=nid)
                )
        elif len(_unique(targets)) > 1 and not rules.allows_multiple_targets(kind):
            issues.append(_issue(LEVEL_ERROR, "不支持隐式分叉，请为每个后继单独建节点", node_id=nid))


def _check_category_edges(node_id: str, out_edges: list[dict], issues: list[dict]) -> None:
    """分类节点的选项：至少两条、全走 default、目标已在 _check_edges 确保是排查。"""
    if len(out_edges) < 2:
        issues.append(_issue(LEVEL_ERROR, "分类节点至少需要两条出线", node_id=node_id))
    for edge in out_edges:
        branch = edge.get("branch")
        if branch not in (None, "", rules.DEFAULT_BRANCH):
            issues.append(
                _issue(
                    LEVEL_ERROR,
                    "分类节点的出线不能带是/否分支",
                    node_id=node_id,
                    edge_id=edge.get("id"),
                )
            )


def _check_start(
    nodes_by_id: dict[str, dict],
    shaped_edges: list[dict],
    valid_edges: list[dict],
    issues: list[dict],
) -> str | None:
    """开始节点：全局唯一、无入边、至少一条出边。

    返回入口 id；没有开始节点时返回 None。
    """
    starts = sorted(
        nid for nid, node in nodes_by_id.items() if rules.is_start_kind(node.get("kind", ""))
    )
    if not starts:
        issues.append(
            _issue(LEVEL_ERROR, "缺少开始节点：流程必须有且只有一个开始节点，请新建后连到第一个排查节点")
        )
        return None

    if len(starts) > 1:
        issues.append(
            _issue(LEVEL_ERROR, f"开始节点必须全局唯一，当前有 {len(starts)} 个：{', '.join(starts)}")
        )

    for nid in starts:
        for edge in shaped_edges:
            if edge.get("to") == nid:
                issues.append(
                    _issue(LEVEL_ERROR, "开始节点不能有入线", node_id=nid, edge_id=edge.get("id"))
                )
        targets = [edge.get("to") for edge in valid_edges if edge.get("from") == nid]
        if not targets:
            issues.append(_issue(LEVEL_ERROR, "开始节点至少需要一条出线", node_id=nid))
        # 开始节点的出线会被一次性全部投递，重复连接等于同一个节点跑两遍
        for target in _unique([t for t in targets if targets.count(t) > 1]):
            issues.append(_issue(LEVEL_ERROR, f"开始节点重复连接到 {target}", node_id=nid))

        node = nodes_by_id[nid]
        _check_start_inputs(nid, node, issues)
    return starts[0]


def _check_start_inputs(nid: str, node: dict, issues: list[dict]) -> None:
    raw = node.get("inputs")
    if raw is None:
        return
    if not isinstance(raw, list):
        issues.append(_issue(LEVEL_ERROR, "开始节点的启动参数格式不正确", node_id=nid))
        return

    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            issues.append(_issue(LEVEL_ERROR, "启动参数格式不正确", node_id=nid))
            continue
        name = _text(item.get("name"))
        if not name:
            issues.append(_issue(LEVEL_ERROR, "启动参数缺少名称", node_id=nid))
            continue
        if item.get("type", "text") not in INPUT_TYPES:
            issues.append(_issue(LEVEL_ERROR, f"启动参数 {name} 的类型不支持", node_id=nid))
        if name in seen:
            issues.append(_issue(LEVEL_ERROR, f"启动参数 {name} 重复", node_id=nid))
            continue
        seen.add(name)


def _check_reachability(
    entry: str, nodes_by_id: dict[str, dict], outgoing: dict[str, list[str]], issues: list[dict]
) -> None:
    seen = {entry}
    queue = [entry]
    while queue:
        node = queue.pop()
        for nxt in outgoing.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)

    terminals = {nid for nid, n in nodes_by_id.items() if rules.is_terminal_kind(n.get("kind", ""))}
    if not terminals:
        issues.append(_issue(LEVEL_ERROR, "流程缺少终止节点（问题解决或上升）"))
    for nid in nodes_by_id:
        if nid not in seen:
            issues.append(_issue(LEVEL_ERROR, "节点不可达：从入口无法到达，且到不了终止节点", node_id=nid))


def _check_cycles(entry: str, outgoing: dict[str, list[str]], issues: list[dict]) -> None:
    """环允许存在，但必须能从环内走出去；无出口的环会让执行无法收敛。"""
    white, gray, black = 0, 1, 2
    color = {node: white for node in outgoing}
    path: list[str] = []
    stack: list[tuple[str, object]] = [(entry, iter(outgoing.get(entry, [])))]
    color[entry] = gray
    path.append(entry)
    cycles: list[list[str]] = []

    while stack:
        node, it = stack[-1]
        descended = False
        for nxt in it:  # type: ignore[union-attr]
            if color.get(nxt, black) == white:
                color[nxt] = gray
                path.append(nxt)
                stack.append((nxt, iter(outgoing.get(nxt, []))))
                descended = True
                break
            if color.get(nxt) == gray:
                cycles.append(path[path.index(nxt) :])
        if descended:
            continue
        color[node] = black
        stack.pop()
        path.pop()

    reported: set[frozenset[str]] = set()
    for cycle in cycles:
        key = frozenset(cycle)
        if key in reported:
            continue
        reported.add(key)
        if not any(outgoing.get(n, []) and any(t not in key for t in outgoing[n]) for n in cycle):
            issues.append(
                _issue(LEVEL_ERROR, f"存在没有出口的环：{' → '.join(cycle)}", node_id=cycle[0])
            )


def _check_slots(doc, nodes, start, functions):
    """槽位校验：接口是否可探测、参数来源是否合法、启动输入是否已声明。"""
    errors = validate_slots(doc, functions)
    declared = start.global_names()
    for node in nodes.values():
        for name, call in calls_in_node(node):
            for value in call.get("arguments", {}).values():
                for item in walk_argument(value):
                    if item.get("source") == "input" and item.get("name") not in declared:
                        errors.append(
                            f"节点 {node.get('id')} 的{name}槽引用了未在开始节点声明的启动输入 {item.get('name')}"
                        )
    return [_issue(LEVEL_ERROR, message) for message in errors]


# ---------------------------------------------------------------- 启动参数


class StartParams:
    """开始节点声明的启动参数；组合配置下都是全局输入。"""

    def __init__(self, doc: dict) -> None:
        node = _find_start_node(doc)
        self.id = str(node.get("id")) if node else None
        self.items = _collect_start_params(doc, node)

    def global_names(self) -> set[str]:
        return {item["name"] for item in self.items}


def _find_start_node(doc: dict) -> dict | None:
    raw = doc.get("nodes")
    for node in raw if isinstance(raw, list) else []:
        if isinstance(node, dict) and rules.is_start_kind(node.get("kind", "")):
            return node
    return None


def _collect_start_params(doc: dict, start: dict | None) -> list[dict]:
    """开始节点声明的启动参数。"""
    items: list[dict] = []
    seen: set[str] = set()
    raw = start.get("inputs") if start else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        items.append({"name": name})
    return items


def _upstream_ids(node_id: str, edges: list[dict]) -> list[str]:
    """反向可达集合，近的在前。"""
    reverse: dict[str, list[str]] = {}
    for edge in edges:
        src, dst = edge.get("from"), edge.get("to")
        if src and dst:
            reverse.setdefault(dst, []).append(src)
    seen: list[str] = []
    queue = list(reverse.get(node_id, []))
    while queue:
        node = queue.pop(0)
        if node in seen:
            continue
        seen.append(node)
        queue.extend(reverse.get(node, []))
    return seen


# ---------------------------------------------------------------- 小工具


def _next_map(node_id: str, kind: str, edges: list[dict]) -> dict[str, list[str]]:
    groups = rules.outgoing_groups(edges, node_id, kind)
    return {branch: [e["to"] for e in group] for branch, group in groups.items()}


def _outgoing_map(nodes_by_id: dict[str, dict], edges: list[dict]) -> dict[str, list[str]]:
    outgoing: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    for edge in edges:
        outgoing[edge["from"]].append(edge["to"])
    return outgoing


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _label(node: dict) -> str:
    kind_label = _kind_label(node.get("kind", ""))
    title = _text(node.get("title"))
    return f"{kind_label}「{title}」" if title else kind_label


def _kind_label(kind: str) -> str:
    return {
        "start": "开始",
        "category": "分类",
        "investigation": "排查",
        "investigationAction": "排查动作",
        "conclusion": "结论",
        "remediation": "处置措施",
        "jump": "跳转",
        "problemSolved": "问题解决",
        "escalation": "上升",
    }.get(kind, str(kind))

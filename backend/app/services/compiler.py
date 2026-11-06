"""doc → plan 校验与编译（spec 第 4、5 节）。

发布时执行一次，产物不可变；执行期不再读 doc。
"""

from __future__ import annotations

import logging
import json
import re

from . import rules
from app.worker.function_pipeline import STAGES, SOURCES, default_argument

logger = logging.getLogger(__name__)

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

SNAPSHOT_SOURCE = "$snapshot"

LIBRARY_PROTOCOL = "library/v1"
LIBRARY_SHOWS = ("auto", "text", "table", "line", "image", "none")
LIBRARY_CASTS = ("text", "number", "boolean", "json")
LIBRARY_EXPRESSIONS = ("$snapshot", "$run", "$var", "$prev", "$file", "$value")
_LIBRARY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LIBRARY_FILE = re.compile(r"^[A-Za-z0-9._-]+$")

# 探针给出注解名（float/int/str/bool），平台类型为 number/text/boolean/datetime，两套都认
SCALAR_PARAM_TYPES = frozenset(
    {"text", "number", "boolean", "datetime", "str", "int", "float", "bool", "date", "time", "decimal"}
)

# 人工文件字段在平台里是 {"hash": ..., "path": ...}；这些参数类型要整份引用，其余按路径传
CONTAINER_PARAM_TYPES = frozenset({"dict", "json", "list"})


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
    require_start: bool = True,
) -> list[dict]:
    """返回 issue 列表；level=error 才算阻断。

    functions 传 probe_doc 的结果时按真实参数名校验绑定，否则按候选字段全量校验。
    strict_binding=False 供 commit 使用：参数绑定问题一律降级为 warning，半成品能存。
    require_start=False 供老版本文档重编译使用：没有 start 节点时退回旧的入口算法。
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

    for nid, node in nodes_by_id.items():
        kind = node.get("kind")
        if "pipeline" in node and kind not in ("investigation", "dataPreparation"):
            issues.append(_issue(LEVEL_ERROR, "只有排查或数据准备节点支持阶段函数", node_id=nid))
        if node.get("recipe") and kind not in ("investigation", "capability"):
            issues.append(_issue(LEVEL_ERROR, "只有排查或能力步骤节点支持能力配方", node_id=nid))
        if kind not in rules.V1_KINDS:
            issues.append(_issue(LEVEL_ERROR, f"v1 不支持的节点类型：{kind}", node_id=nid))

    valid_edges = _check_edges(edges, nodes_by_id, issues)
    shaped_edges = [e for e in edges if e.get("from") in nodes_by_id and e.get("to") in nodes_by_id]
    outgoing = _outgoing_map(nodes_by_id, valid_edges)
    incoming: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    for src, targets in outgoing.items():
        for dst in targets:
            incoming[dst].append(src)

    _check_node_fields(nodes_by_id, issues)
    _check_out_edges(nodes_by_id, valid_edges, outgoing, issues)
    entry = _check_start(nodes_by_id, shaped_edges, valid_edges, issues, require_start)
    if entry is None:
        entry = _check_entry(nodes_by_id, incoming, issues)
    if entry is not None:
        _check_reachability(entry, nodes_by_id, outgoing, issues)
        _check_cycles(entry, outgoing, issues)
    start = StartParams(doc)
    _check_bindings(nodes_by_id, valid_edges, start, issues, functions, strict_binding)
    _, found = _compile_diagnosis(doc, nodes_by_id, valid_edges, start, functions)
    issues.extend({**issue, "level": LEVEL_WARNING} if not strict_binding else issue for issue in found)
    return issues


class NodeDefinition:
    """节点类只编译执行描述，人工等待和持久化仍由调度器负责。"""

    def compile(self, node, nodes, edges, start, functions):
        return {"kind": node["kind"], "next": _next_map(node["id"], node["kind"], edges)}


class StartNode(NodeDefinition):
    pass


class InvestigationNode(NodeDefinition):
    def compile(self, node, nodes, edges, start, functions):
        step = super().compile(node, nodes, edges, start, functions)
        if node.get("recipe"):
            step["recipe"], _ = _compile_recipe(node, nodes, edges, start, functions)
            return step
        if node.get("method"):
            return step
        if "pipeline" in node:
            step["pipeline"], _ = _compile_function_pipeline(node, nodes, edges, start, functions)
            return step
        binding = node.get("binding")
        if not binding:
            step["human"] = True
            return step
        args, _ = _resolve_args(node, nodes, edges, start, functions)
        step["call"] = {
            "project": str(binding.get("project") or ""),
            "function": str(binding.get("function") or ""), "args": args,
        }
        if binding.get("protocol") == "investigation/v1":
            for key in ("protocol", "version", "config"):
                step["call"][key] = binding.get(key)
        return step


class DataPreparationNode(NodeDefinition):
    def compile(self, node, nodes, edges, start, functions):
        step = super().compile(node, nodes, edges, start, functions)
        step["pipeline"], _ = _compile_function_pipeline(node, nodes, edges, start, functions)
        return step


class CapabilityNode(NodeDefinition):
    """能力步骤：执行配方后沿默认出线继续，本身不产生是/否结论。"""

    def compile(self, node, nodes, edges, start, functions):
        step = super().compile(node, nodes, edges, start, functions)
        step["recipe"], _ = _compile_recipe(node, nodes, edges, start, functions)
        return step


class HumanNode(NodeDefinition):
    def compile(self, node, nodes, edges, start, functions):
        return {**super().compile(node, nodes, edges, start, functions), "human": True}


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
    "start": StartNode(), "investigation": InvestigationNode(),
    "dataPreparation": DataPreparationNode(),
    "capability": CapabilityNode(),
    "investigationAction": InvestigationActionNode(), "remediation": RemediationNode(),
    "jump": JumpNode(), "conclusion": ConclusionNode(),
    "problemSolved": ProblemSolvedNode(), "escalation": EscalationNode(),
}


def compile_doc(
    doc: dict,
    flow_id: str,
    release_no: int,
    functions: dict | None = None,
    require_start: bool = True,
) -> dict:
    """把 doc 编译成 plan。校验不过抛 ValidationError。

    functions 为 probe_doc 的结果；给了签名就只为函数真实参数绑定。
    """
    issues = [
        i
        for i in validate_doc(doc, functions, require_start=require_start)
        if i.get("level") == LEVEL_ERROR
    ]
    if issues:
        raise ValidationError(issues)

    nodes_by_id = {n["id"]: n for n in doc["nodes"]}
    edges = [e for e in doc.get("edges", []) if isinstance(e, dict)]
    valid_edges = [e for e in edges if e.get("from") in nodes_by_id and e.get("to") in nodes_by_id]
    start = StartParams(doc)
    entry = start.id or _find_entry(nodes_by_id, valid_edges)

    steps: dict[str, dict] = {}
    for nid in sorted(nodes_by_id):
        node = nodes_by_id[nid]
        steps[nid] = NODE_DEFINITIONS[node["kind"]].compile(
            node, nodes_by_id, valid_edges, start, functions,
        )
        if node.get("method") and doc.get("diagnosis"):
            binding = doc["diagnosis"]
            method_binding = {"project": binding["project"],
                              "function": binding["function"] + "." + node["method"]}
            args, _ = _resolve_args({**node, "binding": method_binding}, nodes_by_id,
                                    valid_edges, start, functions)
            steps[nid]["diagnosisMethod"] = {"name": node["method"], "args": args}

    calls = [
        f"{step['call'].get('project')}.{step['call'].get('function')}"
        for step in steps.values()
        if step.get("call")
    ]
    for step in steps.values():
        pipeline = step.get("pipeline") or {}
        calls.extend(
            f"{pipeline[stage]['project']}.{pipeline[stage]['function']}"
            for stage in STAGES if pipeline.get(stage)
        )
        recipe = step.get("recipe") or {}
        calls.extend(
            f"{recipe.get('project')}.{item.get('call')}" for item in recipe.get("steps") or []
        )
    logger.info(
        "编译 flow=%s release=%s：%d 个节点，%d 个调用，%d 个人工节点，函数 %s",
        flow_id,
        release_no,
        len(nodes_by_id),
        len(calls),
        sum(1 for step in steps.values() if step.get("human")),
        sorted(set(calls)) or "无",
    )

    plan = {
        "format": 1,
        "flowId": flow_id,
        "release": release_no,
        "mode": doc.get("mode") or "parallel",
        "entry": entry,
        "steps": steps,
        "signature": {},
    }
    if doc.get("diagnosis"):
        plan["diagnosis"], _ = _compile_diagnosis(doc, nodes_by_id, valid_edges, start, functions)
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
            issues.append(
                _issue(
                    LEVEL_ERROR,
                    f"不允许的连接：{_label(nodes_by_id[src])} → {_label(nodes_by_id[dst])}",
                    edge_id=eid,
                )
            )
            continue
        valid.append(edge)
    return valid


def _check_node_fields(nodes_by_id: dict[str, dict], issues: list[dict]) -> None:
    for nid, node in nodes_by_id.items():
        kind = node.get("kind", "")
        if rules.is_start_kind(kind):
            # 开始节点只有参数声明，没有诊断描述，标题/说明都不强制
            continue

        for key, label in rules.REQUIRED_TEXT.items():
            if not _text(node.get(key)):
                issues.append(_issue(LEVEL_ERROR, f"{_kind_label(kind)}缺少{label}", node_id=nid))

        _check_recipe_fields(nid, node, issues)
        if kind == "investigation":
            fields = node.get("fields")
            fields = fields if isinstance(fields, dict) else {}
            for key, label in rules.INVESTIGATION_FIELDS.items():
                if not _text(fields.get(key)):
                    issues.append(_issue(LEVEL_ERROR, f"排查节点缺少{label}", node_id=nid))
            binding = node.get("binding")
            if binding is not None:
                if not isinstance(binding, dict):
                    issues.append(_issue(LEVEL_ERROR, "排查节点绑定格式不正确", node_id=nid))
                else:
                    if not _text(binding.get("project")):
                        issues.append(_issue(LEVEL_ERROR, "排查节点绑定的工程为空", node_id=nid))
                    if not _text(binding.get("function")):
                        issues.append(_issue(LEVEL_ERROR, "排查节点绑定的函数为空", node_id=nid))
                    if binding.get("protocol") not in (None, "investigation/v1"):
                        issues.append(_issue(LEVEL_ERROR, "不支持的节点适配协议", node_id=nid))


def _check_recipe_fields(nid: str, node: dict, issues: list[dict]) -> None:
    """能力配方与其它接入方式互斥；同一个节点只能有一种执行描述。"""
    if not node.get("recipe"):
        return
    if node.get("binding"):
        issues.append(_issue(LEVEL_ERROR, "能力配方与函数绑定不能同时配置", node_id=nid))
    if node.get("pipeline"):
        issues.append(_issue(LEVEL_ERROR, "能力配方与四阶段函数不能同时配置", node_id=nid))
    if node.get("method"):
        issues.append(_issue(LEVEL_ERROR, "能力配方与诊断类实例方法不能同时配置", node_id=nid))


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
        elif kind == "dataPreparation":
            targets = [edge.get("to") for edge in out_edges]
            if len(targets) != len(set(targets)):
                issues.append(_issue(LEVEL_ERROR, "数据准备节点不能重复连接同一目标", node_id=nid))
            if any(edge.get("branch") is not None for edge in out_edges):
                issues.append(_issue(LEVEL_ERROR, "数据准备节点的出线不能配置是/否分支", node_id=nid))
        elif rules.is_single_successor(kind):
            if len(out_edges) != 1:
                issues.append(
                    _issue(LEVEL_ERROR, f"{_kind_label(kind)}节点必须恰好一条出线", node_id=nid)
                )
        elif len(_unique(targets)) > 1 and not rules.allows_multiple_targets(kind):
            issues.append(_issue(LEVEL_ERROR, "不支持隐式分叉，请为每个后继单独建节点", node_id=nid))


def _check_start(
    nodes_by_id: dict[str, dict],
    shaped_edges: list[dict],
    valid_edges: list[dict],
    issues: list[dict],
    require_start: bool,
) -> str | None:
    """开始节点：全局唯一、无入边、至少一条出边、不绑函数。

    返回入口 id；没有开始节点时返回 None，由 _check_entry 走旧规则。
    """
    starts = sorted(
        nid for nid, node in nodes_by_id.items() if rules.is_start_kind(node.get("kind", ""))
    )
    if not starts:
        if require_start:
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
        if node.get("binding"):
            issues.append(_issue(LEVEL_ERROR, "开始节点不允许绑定函数", node_id=nid))
        if node.get("params"):
            issues.append(_issue(LEVEL_ERROR, "开始节点不允许配置参数绑定", node_id=nid))
        _check_start_inputs(nid, node, issues)
    return starts[0]


def _check_start_inputs(nid: str, node: dict, issues: list[dict]) -> None:
    raw = node.get("inputs")
    if raw is None:
        return
    if not isinstance(raw, list):
        issues.append(_issue(LEVEL_ERROR, "开始节点的启动参数格式不正确", node_id=nid))
        return

    seen: set[tuple[str, str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            issues.append(_issue(LEVEL_ERROR, "启动参数格式不正确", node_id=nid))
            continue
        name = _text(item.get("name"))
        if not name:
            issues.append(_issue(LEVEL_ERROR, "启动参数缺少名称", node_id=nid))
            continue
        scope = item.get("scope") or "global"
        if not rules.is_start_param_scope(scope):
            issues.append(_issue(LEVEL_ERROR, f"启动参数 {name} 的 scope 不合法：{scope}", node_id=nid))
            continue
        function = _text(item.get("function")) if scope == "function" else ""
        if scope == "function" and not function:
            issues.append(
                _issue(LEVEL_ERROR, f"启动参数 {name} 的 scope=function 但没有指定函数", node_id=nid)
            )
            continue
        key = (scope, function, name)
        if key in seen:
            where = f"函数 {function} 的" if function else "全局"
            issues.append(_issue(LEVEL_ERROR, f"{where}启动参数 {name} 重复", node_id=nid))
            continue
        seen.add(key)


def _check_entry(
    nodes_by_id: dict[str, dict], incoming: dict[str, list[str]], issues: list[dict]
) -> str | None:
    """旧文档（没有开始节点）的入口规则：入度为 0 且只允许一个。"""
    entries = [nid for nid in nodes_by_id if not incoming.get(nid)]
    if not entries:
        issues.append(_issue(LEVEL_ERROR, "没有入口节点（缺少入度为 0 的节点）"))
        return None
    if len(entries) > 1:
        issues.append(
            _issue(LEVEL_ERROR, f"入口节点必须恰好一个，当前有 {len(entries)} 个：{', '.join(entries)}")
        )
        return None
    entry = entries[0]
    if nodes_by_id[entry].get("kind") != "investigation":
        issues.append(_issue(LEVEL_ERROR, "入口节点必须是排查节点", node_id=entry))
        return None
    return entry


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


def _check_bindings(
    nodes_by_id: dict[str, dict],
    edges: list[dict],
    start: "StartParams",
    issues: list[dict],
    functions: dict | None = None,
    strict_binding: bool = True,
) -> None:
    """spec 4.8：参数来源必须在当前版本存在，且位于该节点上游。"""
    for nid, node in nodes_by_id.items():
        if node.get("method"):
            continue
        if node.get("kind") == "capability" and not node.get("recipe"):
            issues.append(_issue(LEVEL_ERROR, "能力步骤必须配置能力配方", node_id=nid))
            continue
        if node.get("kind") not in ("investigation", "dataPreparation", "capability"):
            continue
        if node.get("kind") == "dataPreparation" and "pipeline" not in node:
            issues.append(_issue(LEVEL_ERROR, "数据准备节点必须配置获取与解析阶段", node_id=nid))
            continue
        if "pipeline" in node:
            _, found = _compile_function_pipeline(node, nodes_by_id, edges, start, functions)
            for issue in found:
                if not strict_binding and issue["level"] == LEVEL_ERROR:
                    issue = {**issue, "level": LEVEL_WARNING}
                issues.append(issue)
            continue
        if node.get("recipe"):
            _, found = _compile_recipe(node, nodes_by_id, edges, start, functions)
            for issue in found:
                if not strict_binding and issue["level"] == LEVEL_ERROR:
                    issue = {**issue, "level": LEVEL_WARNING}
                issues.append(issue)
            continue
        if not node.get("binding"):
            continue
        binding = node["binding"]
        if isinstance(binding, dict) and binding.get("protocol") == "investigation/v1":
            message = None
            if not isinstance(binding.get("version"), str) or not binding["version"].strip():
                message = "四阶段适配器缺少版本"
            elif not isinstance(binding.get("config", {}), dict):
                message = "适配器初始化配置必须为对象"
            elif not functions:
                message = "四阶段适配器发布前必须完成接口探测"
            else:
                entry = functions.get(f"{binding.get('project')}:{binding.get('function')}")
                if not isinstance(entry, list):
                    message = (entry or {}).get("error", "适配器接口不可用")
            if message:
                issues.append(_issue(LEVEL_ERROR if strict_binding else LEVEL_WARNING, message, node_id=nid))
        _, arg_issues = _resolve_args(node, nodes_by_id, edges, start, functions)
        for issue in arg_issues:
            if not strict_binding and issue.get("level") == LEVEL_ERROR:
                issue = {**issue, "level": LEVEL_WARNING}
            issues.append(issue)


def _compile_diagnosis(doc, nodes, edges, start, functions):
    issues = []
    binding = doc.get("diagnosis")
    method_nodes = [node for node in nodes.values() if "method" in node]
    if binding is None:
        if method_nodes:
            issues.append(_issue(LEVEL_ERROR, "调用实例方法前必须配置流程诊断类"))
        return None, issues
    if (not isinstance(binding, dict) or binding.get("protocol") != "diagnosis/v1"
            or not all(isinstance(binding.get(key), str) and binding[key].strip()
                       for key in ("project", "function", "version"))
            or not isinstance(binding.get("config", {}), dict)):
        return None, [_issue(LEVEL_ERROR, "诊断类配置需要工程、类、协议、版本及合法初始化配置")]
    prefix = f"{binding['project']}:{binding['function']}"
    definition = (functions or {}).get(prefix + ".__definition__")
    try:
        meta = json.loads(definition[0]["type"])
        if meta["protocol"] != "diagnosis/v1" or meta["version"] != binding["version"]:
            raise ValueError("诊断类版本不一致")
    except (ValueError, TypeError, IndexError, KeyError):
        return None, [_issue(LEVEL_ERROR, "诊断类接口尚未探测或版本不一致，请重新选择诊断类")]
    names = {param["name"] for param in meta["configParams"]}
    config = binding.get("config") or {}
    if set(config) - names or any(param["required"] and param["name"] not in config
                                  for param in meta["configParams"]):
        issues.append(_issue(LEVEL_ERROR, "诊断类初始化配置缺少必填参数或包含未知参数"))
    methods = {entry["name"] for entry in meta["methods"]}
    for node in method_nodes:
        if (node.get("kind") != "investigation" or not isinstance(node.get("method"), str)
                or node.get("method") not in methods
                or node.get("pipeline") or node.get("binding")):
            issues.append(_issue(LEVEL_ERROR, "实例方法只支持排查节点，须选择诊断类公开方法且不能混用其他绑定",
                                 node_id=node["id"]))
            continue
        method_binding = {"project": binding["project"],
                          "function": binding["function"] + "." + node["method"]}
        _, found = _resolve_args({**node, "binding": method_binding}, nodes, edges, start, functions)
        issues.extend(found)
    if any(node.get("binding") or node.get("pipeline") or node.get("recipe")
           or node.get("kind") == "dataPreparation"
           for node in nodes.values()):
        issues.append(_issue(LEVEL_ERROR, "诊断类流程的自动排查统一调用实例方法；旧接入方式请用于未配置诊断类的流程"))
    init_node = {"id": start.id, "binding": binding}
    args, found = _resolve_args(init_node, nodes, edges, start, functions)
    issues.extend(found)
    return {**binding, "args": args, "definition": meta}, issues


def _compile_recipe(node, nodes, edges, start, functions):
    """能力配方：一个节点内按顺序调用现成库，节点之间只传 JSON。

    编译期只做能确定的事：工程/调用是否存在、参数名是否在签名里、引用的对象是否已经产生。
    对象方法的参数不在发布期猜（对象来自运行期），由运行期按真实签名试绑并给出可读错误。
    """
    issues: list[dict] = []
    node_id = str(node.get("id") or "")
    recipe = node.get("recipe")
    upstream = set(_upstream_ids(node_id, edges))
    declared_inputs = start.global_names()

    def error(message: str) -> None:
        issues.append(_issue(LEVEL_ERROR, message, node_id=node_id))

    def warning(message: str) -> None:
        issues.append(_issue(LEVEL_WARNING, message, node_id=node_id))

    if not isinstance(recipe, dict):
        error("能力配方必须是对象")
        return {}, issues
    if recipe.get("protocol") not in (None, LIBRARY_PROTOCOL):
        error(f"能力配方的协议必须是 {LIBRARY_PROTOCOL}")
    project = _text(recipe.get("project"))
    if not project:
        error("能力配方必须选择工程")
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        error("能力配方至少需要一个调用步骤")
        return {}, issues

    known: set[str] = set()
    compiled_steps: list[dict] = []
    for index, step in enumerate(steps):
        where = f"第 {index + 1} 步"
        if not isinstance(step, dict):
            error(f"{where}格式不正确")
            continue
        call = _text(step.get("call"))
        if not call:
            error(f"{where}缺少调用名")
            continue
        on = _text(step.get("on"))
        if on and on not in known:
            error(f"{where}引用的对象 {on} 还不存在（只能引用前面步骤保存的对象）")
        if not on:
            _check_recipe_signature(project, call, step.get("args"), functions, issues, node_id)
        args, found = _compile_recipe_args(
            step.get("args"), known, upstream, declared_inputs, node_id, f"{where}的参数",
        )
        issues.extend(found)
        show = step.get("show")
        if show is not None and show not in LIBRARY_SHOWS:
            error(f"{where}的展示方式只能是 {'/'.join(LIBRARY_SHOWS)}")
        label = step.get("label")
        if label is not None and not isinstance(label, str):
            error(f"{where}的展示标题必须是字符串")
        slot = step.get("as")
        if slot is not None:
            slot = _text(slot)
            if not _LIBRARY_NAME.fullmatch(slot):
                error(f"{where}的保存名 {slot!r} 不合法：需为英文字母、数字、下划线")
            elif slot in known:
                error(f"{where}的保存名 {slot} 与前面的步骤重复")
            else:
                known.add(slot)
        entry = {"index": index + 1, "call": call, "args": args}
        if on:
            entry["on"] = on
        if slot:
            entry["as"] = slot
        if show:
            entry["show"] = show
        if label:
            entry["label"] = label
        compiled_steps.append(entry)

    compiled: dict = {"protocol": LIBRARY_PROTOCOL, "project": project, "steps": compiled_steps}

    out = recipe.get("out")
    if out is not None:
        compiled["out"] = {}
        if not isinstance(out, dict):
            error("输出映射必须是对象")
        else:
            for name, source in out.items():
                field = _text(name)
                if not _LIBRARY_NAME.fullmatch(field):
                    error(f"输出名 {name!r} 不合法：需为英文字母、数字、下划线")
                    continue
                if not isinstance(source, dict) or not source.get("from"):
                    error(f"输出 {field} 必须写成 {{from: 步骤保存名, path?: 字段}}")
                    continue
                origin = _text(source.get("from"))
                if origin not in known:
                    error(f"输出 {field} 引用的对象 {origin} 不存在")
                    continue
                compiled["out"][field] = {"from": origin, "path": str(source.get("path") or "")}

    result = recipe.get("result")
    if result is not None:
        if not isinstance(result, dict) or not result.get("from"):
            error("判定来源必须写成 {from: 步骤保存名, path?: 字段}")
        else:
            origin = _text(result.get("from"))
            if origin not in known:
                error(f"判定来源引用的对象 {origin} 不存在")
            else:
                compiled["result"] = {"from": origin, "path": str(result.get("path") or "")}
        if not _recipe_reason(recipe.get("reason")):
            error("自动判定的节点必须填写依据（reason），说明凭什么判为是/否")

    reason = recipe.get("reason")
    if reason is not None:
        if isinstance(reason, dict) and "from" in reason:
            if _text(reason.get("from")) not in known:
                error("依据来源引用的对象不存在")
        elif isinstance(reason, str):
            if not reason.strip():
                warning("依据是空字符串")
        elif isinstance(reason, dict) and isinstance(reason.get("template"), str):
            pass
        else:
            error("依据必须是字符串、{template} 或 {from}")
        compiled["reason"] = reason

    evidence = recipe.get("evidence")
    if evidence is not None:
        source = _text(evidence)
        if not source:
            error("evidence 必须是上游节点 id")
        elif source == node_id:
            error("evidence 不能指向自己")
        elif source not in upstream:
            error(f"evidence 指向的节点 {source} 不存在或不在上游")
        else:
            compiled["evidence"] = source
    return compiled, issues


def _check_recipe_signature(project, call, args, functions, issues, node_id) -> None:
    """按探测到的签名校验参数名；没有签名（未探测）时放行，交给运行期试绑。"""
    if not functions:
        return
    params = functions.get(f"{project}:{call}")
    if not isinstance(params, list):
        detail = params.get("error", "接口尚未探测") if isinstance(params, dict) else "接口尚未探测"
        issues.append(_issue(LEVEL_ERROR, f"调用 {call} 不可用：{detail}", node_id=node_id))
        return
    known = {str(item.get("name")) for item in params}
    configured = args if isinstance(args, dict) else {}
    unknown = sorted(str(name) for name in configured if str(name) not in known)
    if unknown:
        issues.append(_issue(
            LEVEL_ERROR,
            f"调用 {call} 没有参数 {', '.join(unknown)}（可用：{', '.join(sorted(known)) or '无'}）",
            node_id=node_id,
        ))
    missing = [
        str(item.get("name")) for item in params
        if item.get("required", True) and str(item.get("name")) not in configured
    ]
    if missing:
        issues.append(_issue(
            LEVEL_ERROR, f"调用 {call} 缺少必填参数 {', '.join(missing)}", node_id=node_id,
        ))


def _compile_recipe_args(spec, known, upstream, declared_inputs, node_id, where):
    """递归校验参数表达式；原样保留 $ 表达式交给运行期求值。"""
    issues: list[dict] = []
    if spec is None:
        return {}, issues
    if not isinstance(spec, dict):
        issues.append(_issue(LEVEL_ERROR, f"{where}必须是对象", node_id=node_id))
        return {}, issues

    def walk(value, path):
        if isinstance(value, list):
            return [walk(item, path) for item in value]
        if not isinstance(value, dict):
            return value
        keys = list(value)
        if keys and str(keys[0]).startswith("$"):
            kind = keys[0]
            if kind not in LIBRARY_EXPRESSIONS:
                issues.append(_issue(LEVEL_ERROR, f"{path} 使用了未知表达式 {kind}", node_id=node_id))
                return value
            origin = value.get(kind)
            if kind == "$snapshot":
                name = _text(origin)
                if not name:
                    issues.append(_issue(LEVEL_ERROR, f"{path} 的启动参数名为空", node_id=node_id))
                elif name not in declared_inputs:
                    issues.append(_issue(
                        LEVEL_WARNING,
                        f"{path} 引用的启动参数 {name} 没有在开始节点声明，运行时取不到值",
                        node_id=node_id,
                    ))
                cast = value.get("cast")
                if cast is not None and cast not in LIBRARY_CASTS:
                    issues.append(_issue(
                        LEVEL_ERROR, f"{path} 的类型转换只能是 {'/'.join(LIBRARY_CASTS)}", node_id=node_id,
                    ))
            elif kind == "$run":
                origin_id = _text(origin)
                if origin_id not in upstream:
                    issues.append(_issue(
                        LEVEL_ERROR, f"{path} 的来源节点 {origin_id} 不存在或不在上游", node_id=node_id,
                    ))
                if not _text(value.get("path")):
                    issues.append(_issue(LEVEL_ERROR, f"{path} 必须写出字段路径", node_id=node_id))
            elif kind == "$var":
                name = _text(origin)
                if name not in known:
                    issues.append(_issue(
                        LEVEL_ERROR, f"{path} 引用的对象 {name} 还不存在", node_id=node_id,
                    ))
            elif kind == "$prev":
                if not known:
                    issues.append(_issue(LEVEL_ERROR, f"{path} 之前还没有任何步骤结果", node_id=node_id))
            elif kind == "$file":
                name = _text(origin)
                if not name or not _LIBRARY_FILE.fullmatch(name):
                    issues.append(_issue(
                        LEVEL_ERROR,
                        f"{path} 的文件名 {name!r} 不合法：只允许字母、数字、点、下划线和短横线",
                        node_id=node_id,
                    ))
            return value
        return {key: walk(item, f"{path}.{key}") for key, item in value.items()}

    return walk(spec, where), issues


def _recipe_reason(spec) -> bool:
    if spec is None:
        return False
    if isinstance(spec, str):
        return bool(spec.strip())
    if isinstance(spec, dict):
        return bool(spec.get("from")) or isinstance(spec.get("template"), str)
    return False


def _compile_function_pipeline(node, nodes, edges, start, functions):
    issues = []
    result = {"args": {}, "manualFields": []}

    def error(message):
        issues.append(_issue(LEVEL_ERROR, message, node_id=node["id"]))

    pipeline = node.get("pipeline")
    if not isinstance(pipeline, dict):
        error("四阶段函数配置必须为对象")
        return result, issues
    if node.get("binding"):
        error("阶段函数与旧入口绑定不能同时配置")
    preparation = node.get("kind") == "dataPreparation"
    data_source = pipeline.get("dataSource")
    if preparation:
        result["preparation"] = True
        if data_source is not None or pipeline.get("present") or pipeline.get("judge"):
            error("数据准备节点只支持获取与解析阶段")
    if data_source is not None:
        if not isinstance(data_source, dict) or not isinstance(data_source.get("node"), str):
            error("共享数据来源必须指定数据准备节点")
        else:
            producer = nodes.get(data_source["node"], {})
            if producer.get("kind") != "dataPreparation" or data_source["node"] == node["id"]:
                error("共享数据来源必须是其他数据准备节点")
            elif not _dominates(data_source["node"], node["id"], nodes, edges, start.id):
                error("数据准备节点必须在所有进入当前节点的路径上先执行，不能引用旁路或未来结果")
            if not isinstance(data_source.get("path", ""), str):
                error("共享数据字段路径必须为字符串")
            result["dataSource"] = data_source
        if pipeline.get("acquire") or pipeline.get("parse") or pipeline.get("manualFields"):
            error("使用共享数据时不能同时配置获取、解析或人工数据字段")
    fields = pipeline.get("manualFields", [])
    if not isinstance(fields, list):
        error("人工数据字段必须为数组")
        fields = []
    names = set()
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str) or not field["name"]:
            error("人工数据字段必须填写名称")
            continue
        if field["name"] in names:
            error(f"人工数据字段重名：{field['name']}")
        names.add(field["name"])
        if field.get("type", "text") not in ("text", "number", "boolean", "datetime", "json", "file"):
            error(f"不支持的人工字段类型：{field.get('type')}")
    result["manualFields"] = fields
    if data_source is None and not pipeline.get("acquire") and not fields:
        error("人工取数时必须配置数据字段")
    for stage in STAGES:
        if preparation and stage in ("present", "judge"):
            continue
        if data_source is not None and stage in ("acquire", "parse"):
            continue
        binding = pipeline.get(stage)
        if not binding:
            if stage in ("parse", "present"):
                error(f"四阶段排查必须选择 {'解析' if stage == 'parse' else '展示'} 函数")
            continue
        if not isinstance(binding, dict) or not binding.get("project") or not binding.get("function"):
            error(f"{stage} 必须选择工程和函数")
            continue
        if binding.get("protocol"):
            error(f"{stage} 请选择普通函数或方法，不能选择整套适配器类")
            continue
        key = f"{binding['project']}:{binding['function']}"
        params = (functions or {}).get(key)
        if not isinstance(params, list):
            detail = params.get("error", "接口尚未探测") if isinstance(params, dict) else "接口尚未探测"
            error(f"{stage} 接口不可用：{detail}")
            continue
        entry = {"project": binding["project"], "function": binding["function"], "signature": params}
        result[stage] = entry
        if stage == "acquire":
            args, found = _resolve_args({**node, "binding": binding}, nodes, edges, start, functions)
            result["args"] = args
            issues.extend(found)
            continue
        configured = binding.get("arguments", {})
        if not isinstance(configured, dict):
            error(f"{stage} 的参数映射必须为对象")
            continue
        known = {param["name"] for param in params}
        if set(configured) - known:
            error(f"{stage} 包含函数未声明的参数")
        arguments = {}
        for index, param in enumerate(params):
            name = param["name"]
            automatic = default_argument(stage, index)
            if stage == "present" and index == 1 and name not in ("decision", "judgment", "result"):
                automatic = None
            expression = configured.get(name, automatic)
            if isinstance(expression, dict) and expression.get("omit") is True:
                expression = None
            if expression is None:
                if param.get("required", True):
                    error(f"{stage} 的必填参数 {name} 缺少数据来源")
                continue
            if not isinstance(expression, dict):
                error(f"{stage}.{name} 的参数映射格式错误")
                continue
            if "lit" not in expression:
                source = expression.get("source")
                # raw 只属于解析阶段：人工判断恢复只重跑展示、不重新解析，引用它会拿到空值
                allowed = {"raw", "inputs"} if stage == "parse" else SOURCES - {"raw"}
                if stage == "judge":
                    allowed = allowed - {"decision"}
                if source not in allowed or not isinstance(expression.get("path", ""), str):
                    error(f"{stage}.{name} 的数据来源无效")
                    continue
                # 判断参数多为标量：不指定字段路径就会把整段上游数据传进去，运行期才炸
                if (
                    stage == "judge"
                    and not str(expression.get("path") or "").strip()
                    and _scalar_type(param.get("type"))
                    and not (source == "parsed" and isinstance(data_source, dict)
                             and str(data_source.get("path") or "").strip())
                ):
                    error(
                        f"judge 的参数 {name} 需要指定上游数据的字段路径，"
                        f"不能把整段 {source} 传给 {param.get('type')} 类型的参数"
                    )
                    continue
            # 人工上传的文件字段是 {hash, path} 引用。映射只写字段名时按路径传字符串，
            # 否则函数拿到的是字典；这一步写进不可变 plan，不在运行期猜参数类型。
            field = None
            if stage == "parse" and expression.get("source") == "raw":
                field = _file_field(expression.get("path"), result["manualFields"])
            if field is not None and not _container_type(param.get("type")):
                expression = {**expression, "path": f"{field}.path"}
                issues.append(
                    _issue(
                        LEVEL_WARNING,
                        f"{stage}.{name} 的来源 {field} 是文件字段，已按路径传入（{field}.path）；"
                        f"需要整份文件引用请把参数类型声明为 dict",
                        node_id=node["id"],
                    )
                )
            arguments[name] = expression
        entry["arguments"] = arguments
    if not pipeline.get("acquire"):
        result["args"] = {name: {"snapshot": name, "optional": True} for name in start.global_names()}
    return result, issues


def _dominates(producer, consumer, nodes, edges, entry):
    if not entry or producer not in _upstream_ids(consumer, edges):
        return False
    outgoing = _outgoing_map(nodes, edges)
    visited, pending = set(), [entry]
    while pending:
        current = pending.pop()
        if current == producer or current in visited:
            continue
        if current == consumer:
            return False
        visited.add(current)
        pending.extend(outgoing.get(current, []))
    return True


def _file_field(path, manual_fields) -> str | None:
    """path 恰好是人工文件字段名时返回该字段名。

    精确比较即可满足幂等：已写 `字段名.path` 或多段下钻 `字段.hash` 都不等于字段名，不会二次加后缀。
    """
    name = str(path or "").strip()
    if not name:
        return None
    for field in manual_fields if isinstance(manual_fields, list) else []:
        if (isinstance(field, dict) and field.get("type") == "file"
                and field.get("name") == name):
            return name
    return None


def _container_type(kind) -> bool:
    """注解是否要求整份引用：dict / json / list（含 list[...]、dict[...]、Dict/List）。

    认不出来的（any、裸 Optional——探针会把 Optional[dict] 折叠成 Optional）按标量处理，
    宁可传路径并在发布结果里提示，也不把字典硬塞进未标注参数。
    """
    name = str(kind or "").strip().lower().replace("typing.", "")
    if name.startswith(("optional[", "union[")) and name.endswith("]"):
        name = name[name.index("[") + 1 : -1]
    parts = [part.strip() for part in name.replace("|", ",").split(",") if part.strip()]
    return any(part.split("[", 1)[0].strip() in CONTAINER_PARAM_TYPES for part in parts)


def _scalar_type(kind) -> bool:
    """注解文本是否为标量；Optional/Union 只要非 None 的成员都是标量就算标量。

    认不出来的类型（any、Optional、dict、list[...]、Literal[...]）一律放行，不猜。
    """
    name = str(kind or "").strip().lower().replace("typing.", "")
    if name.startswith(("optional[", "union[")) and name.endswith("]"):
        name = name[name.index("[") + 1 : -1]
    parts = [part.strip() for part in name.replace("|", ",").split(",") if part.strip()]
    return bool(parts) and all(part == "none" or part in SCALAR_PARAM_TYPES for part in parts)


# ---------------------------------------------------------------- 参数绑定

# 同一参数名的多个来源按层比较，只有最靠前的一层参与绑定
TIER_FUNCTION = 0
TIER_GLOBAL = 1
TIER_UPSTREAM = 2


class StartParams:
    """开始节点声明的启动参数。

    旧文档没有 start 节点，`snapshotFields` 一律视为全局参数，保证老版本仍能编译。
    """

    def __init__(self, doc: dict) -> None:
        node = _find_start_node(doc)
        self.id = str(node.get("id")) if node else None
        self.items = _collect_start_params(doc, node)

    def local(self, function: str) -> set[str]:
        """只对该函数可见的启动参数名；不泄漏给别的函数。"""
        return {
            item["name"]
            for item in self.items
            if item["scope"] == "function" and item["function"] == function
        }

    def global_names(self) -> set[str]:
        return {item["name"] for item in self.items if item["scope"] == "global"}


def _resolve_args(
    node: dict,
    nodes_by_id: dict[str, dict],
    edges: list[dict],
    start: StartParams,
    functions: dict | None = None,
) -> tuple[dict, list[dict]]:
    """解析调用参数：显式 params 优先，其次按启动参数、上游输出、函数默认值兜底。

    有签名时只为真实参数绑定，多余候选一律不传；无签名时（向前兼容）按候选全量绑定并给警告。
    """
    node_id = str(node.get("id"))
    binding = node.get("binding") or {}
    function = str(binding.get("function") or "")
    issues: list[dict] = []
    args: dict = {}
    candidates = _candidate_map(node_id, nodes_by_id, edges, start, function)
    upstream = set(_upstream_ids(node_id, edges))
    params = _function_params(node, functions)

    explicit = node.get("params")
    explicit = explicit if isinstance(explicit, dict) else {}
    for name, spec in explicit.items():
        expr = _expr_from_spec(str(name), spec, node_id, candidates, upstream, issues)
        if expr is not None:
            args[str(name)] = expr

    if params is not None:
        param_names = [str(p.get("name") or "") for p in params]
        for name in explicit:
            if str(name) not in param_names:
                issues.append(
                    _issue(
                        LEVEL_ERROR,
                        f"参数 {name} 不在函数签名里（可用：{', '.join(n for n in param_names if n) or '无'}）",
                        node_id=node_id,
                    )
                )
        for param in params:
            name = str(param.get("name") or "")
            if not name or name in args:
                continue
            sources = _best_sources(candidates.get(name, []))
            if len(sources) > 1:
                issues.append(
                    _issue(
                        LEVEL_ERROR,
                        f"参数 {name} 有多个来源（{', '.join(sources)}），无法自动绑定，请在节点上显式指定 params",
                        node_id=node_id,
                    )
                )
                continue
            if not sources:
                if param.get("required", True):
                    issues.append(
                        _issue(
                            LEVEL_ERROR,
                            f"函数 {function} 的必填参数 {name} 没有来源，候选：{_available(candidates)}",
                            node_id=start.id or node_id,
                        )
                    )
                continue  # 可选参数交给 Python 默认值
            args[name] = _expr_for_source(
                name, sources[0], optional=not param.get("required", True)
            )
        unused = sorted(set(candidates) - set(param_names))
        if unused:
            issues.append(
                _issue(
                    LEVEL_WARNING,
                    f"字段 {', '.join(unused)} 不在函数签名里，已忽略（检查节点 outputs / snapshotFields 是否写多了）",
                    node_id=node_id,
                )
            )
        return args, issues

    if explicit:
        return args, issues

    for name in sorted(candidates):
        sources = _best_sources(candidates[name])
        if len(sources) > 1:
            issues.append(
                _issue(
                    LEVEL_ERROR,
                    f"参数 {name} 有多个来源（{', '.join(sources)}），无法自动绑定，请在节点上显式指定 params",
                    node_id=node_id,
                )
            )
            continue
        args[name] = _expr_for_source(name, sources[0])
    issues.append(
        _issue(
            LEVEL_WARNING,
            "未提供函数签名，按候选字段全量绑定，可能传入多余参数",
            node_id=node_id,
        )
    )
    return args, issues


def _function_params(node: dict, functions: dict | None) -> list[dict] | None:
    """从 probe_doc 结果里取该节点函数的参数表；没有签名信息时返回 None。"""
    if not functions:
        return None
    binding = node.get("binding") or {}
    project = str(binding.get("project") or "")
    function = str(binding.get("function") or "")
    if not project or not function:
        return None
    params = functions.get(f"{project}:{function}")
    if not isinstance(params, list):
        return None  # 探测失败或函数不存在，退化为全量绑定
    return [item for item in params if isinstance(item, dict)]


def _available(candidates: dict[str, object]) -> str:
    return ", ".join(sorted(candidates)) or "无"


def _candidate_map(
    node_id: str,
    nodes_by_id: dict[str, dict],
    edges: list[dict],
    start: StartParams,
    function: str,
) -> dict[str, list[tuple[int, str]]]:
    """候选来源按层收集：函数局部启动参数 > 全局启动参数 > 上游同名字段。"""
    local = start.local(function)
    globals_ = start.global_names()
    candidates: dict[str, list[tuple[int, str]]] = {}
    for name in sorted(local):
        candidates.setdefault(name, []).append((TIER_FUNCTION, SNAPSHOT_SOURCE))
    for name in sorted(globals_ - local):
        candidates.setdefault(name, []).append((TIER_GLOBAL, SNAPSHOT_SOURCE))
    for src in _upstream_ids(node_id, edges):
        upstream_node = nodes_by_id.get(src)
        if upstream_node is None:
            continue
        for field in _declared_outputs(upstream_node):
            if field in local or field in globals_:
                continue  # 有启动参数时上游同名字段不参与竞争
            candidates.setdefault(field, []).append((TIER_UPSTREAM, src))
    return candidates


def _best_sources(entries: list[tuple[int, str]]) -> list[str]:
    """取最高优先级那一层的来源；同层多个才算歧义。"""
    if not entries:
        return []
    tier = min(entry[0] for entry in entries)
    return _unique([source for entry_tier, source in entries if entry_tier == tier])


def _expr_from_spec(
    name: str,
    spec: object,
    node_id: str,
    candidates: dict[str, list[tuple[int, str]]],
    upstream: set[str],
    issues: list[dict],
) -> dict | None:
    if isinstance(spec, str):
        sources = _best_sources(candidates.get(spec, []))
        if not sources:
            available = ", ".join(sorted(candidates)) or "无"
            issues.append(
                _issue(
                    LEVEL_ERROR,
                    f"参数 {name} 的来源 {spec} 不存在或不在上游，可用字段：{available}",
                    node_id=node_id,
                )
            )
            return None
        if len(sources) > 1:
            issues.append(
                _issue(
                    LEVEL_ERROR,
                    f"参数 {name} 的来源 {spec} 有多个候选节点：{', '.join(sources)}",
                    node_id=node_id,
                )
            )
            return None
        return _expr_for_source(spec, sources[0])

    if not isinstance(spec, dict):
        issues.append(_issue(LEVEL_ERROR, f"参数 {name} 的绑定格式不正确", node_id=node_id))
        return None
    if "lit" in spec:
        return {"lit": spec["lit"]}
    if "snapshot" in spec:
        return {"snapshot": str(spec["snapshot"])}
    if "run" in spec:
        src = str(spec["run"])
        if src not in upstream:
            issues.append(
                _issue(LEVEL_ERROR, f"参数 {name} 的来源节点 {src} 不存在或不在上游", node_id=node_id)
            )
            return None
        return {"run": src, "path": str(spec.get("path") or f"output.{name}")}
    issues.append(_issue(LEVEL_ERROR, f"参数 {name} 的绑定格式不正确", node_id=node_id))
    return None


def _expr_for_source(name: str, source: str, optional: bool = False) -> dict:
    if source == SNAPSHOT_SOURCE:
        expr: dict = {"snapshot": name}
    else:
        expr = {"run": source, "path": f"output.{name}"}
    if optional:
        # 取不到值时由调度器省略该参数，交给函数自身默认值
        expr["optional"] = True
    return expr


def _declared_outputs(node: dict) -> list[str]:
    """节点可选的 outputs 声明；v1 的 types.ts 未定义，缺省视为空。"""
    raw = node.get("outputs")
    if isinstance(raw, dict):
        fields = [str(k) for k in raw]
    elif isinstance(raw, list):
        fields = [str(v) for v in raw if isinstance(v, str)]
    else:
        fields = []
    recipe = node.get("recipe")
    if isinstance(recipe, dict) and isinstance(recipe.get("out"), dict):
        # 配方的 out 就是它对外的字段，作者不必再手工抄一遍 outputs
        for name in recipe["out"]:
            if str(name) not in fields:
                fields.append(str(name))
    return fields


def _find_start_node(doc: dict) -> dict | None:
    raw = doc.get("nodes")
    for node in raw if isinstance(raw, list) else []:
        if isinstance(node, dict) and rules.is_start_kind(node.get("kind", "")):
            return node
    return None


def _collect_start_params(doc: dict, start: dict | None) -> list[dict]:
    """开始节点的 inputs；旧文档的 snapshotFields 一并当作全局参数。"""
    items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    raw = start.get("inputs") if start else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        scope = item.get("scope") if rules.is_start_param_scope(item.get("scope")) else "global"
        function = _text(item.get("function")) if scope == "function" else ""
        key = (scope, function, name)
        if key in seen:
            continue
        seen.add(key)
        items.append({"name": name, "scope": scope, "function": function})

    global_names = {item["name"] for item in items if item["scope"] == "global"}
    for field in _snapshot_fields(doc):
        if field in global_names:
            continue
        global_names.add(field)
        items.append({"name": field, "scope": "global", "function": ""})
    return items


def _snapshot_fields(doc: dict) -> list[str]:
    raw = doc.get("snapshotFields")
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    if isinstance(raw, list):
        return [str(v) for v in raw if isinstance(v, str)]
    return []


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


def _find_entry(nodes_by_id: dict[str, dict], edges: list[dict]) -> str:
    """没有开始节点时的旧算法：入度为 0 且可达节点最多的那个。"""
    incoming = {nid: 0 for nid in nodes_by_id}
    for edge in edges:
        if edge.get("to") in incoming:
            incoming[edge["to"]] += 1
    roots = sorted(nid for nid in nodes_by_id if incoming[nid] == 0)
    if not roots:
        raise ValidationError([_issue(LEVEL_ERROR, "没有入口节点")])

    outgoing: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    for edge in edges:
        outgoing[edge["from"]].append(edge["to"])

    def reachable(nid: str) -> int:
        seen, queue = {nid}, [nid]
        while queue:
            for nxt in outgoing.get(queue.pop(), []):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return len(seen)

    return min(roots, key=lambda nid: (-reachable(nid), nid))


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
        "investigation": "排查",
        "dataPreparation": "数据准备",
        "investigationAction": "排查动作",
        "conclusion": "结论",
        "remediation": "处置措施",
        "jump": "跳转",
        "problemSolved": "问题解决",
        "escalation": "上升",
    }.get(kind, str(kind))

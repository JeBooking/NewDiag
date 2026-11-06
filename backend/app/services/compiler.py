"""doc → plan 校验与编译（spec 第 4、5 节）。

发布时执行一次，产物不可变；执行期不再读 doc。
"""

from __future__ import annotations

import logging
import json

from . import rules
from app.worker.function_pipeline import STAGES, SOURCES, default_argument

logger = logging.getLogger(__name__)

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

SNAPSHOT_SOURCE = "$snapshot"

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
    _, found = _compile_composition(doc, nodes_by_id, functions)
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
        if node.get("method") or node.get("operation"):
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
    composition, _ = _compile_composition(doc, nodes_by_id, functions)
    if composition is not None:
        plan["composition"] = composition
        start_id = start.id or entry
        start_step = steps.get(start_id) or {}
        start_step["composeResources"] = composition["startup"]
        for nid, node in nodes_by_id.items():
            if not node.get("operation"):
                continue
            steps[nid]["composeOperation"] = str(node["operation"])
            steps[nid]["operationArgs"] = dict(node.get("operationArguments") or {})
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
        if node.get("kind") not in ("investigation", "dataPreparation"):
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
    if any(node.get("binding") or node.get("pipeline") or node.get("kind") == "dataPreparation"
           for node in nodes.values()):
        issues.append(_issue(LEVEL_ERROR, "诊断类流程的自动排查统一调用实例方法；旧接入方式请用于未配置诊断类的流程"))
    init_node = {"id": start.id, "binding": binding}
    args, found = _resolve_args(init_node, nodes, edges, start, functions)
    issues.extend(found)
    return {**binding, "args": args, "definition": meta}, issues


# ---------------------------------------------------------------- 组合配置
#
# doc.composition 声明工程对象、启动资源和节点操作；节点只引用 operation 并提供参数。
# 编译期把「谁调用谁、参数从哪来」全部写死，运行期不再按名字猜。


COMPOSITION_CALL_KINDS = ("constructor", "function", "method")
COMPOSITION_OUTPUTS = ("auto", "value", "object", "file", "files", "image")
COMPOSITION_TRIGGERS = ("start", "lazy")
ARGUMENT_KEYS = ("lit", "input", "param", "step", "resource", "object", "system", "items", "fields")
NODE_ARGUMENT_KEYS = ("lit", "input", "object", "system", "items", "fields")


def _compile_composition(doc: dict, nodes: dict, functions: dict | None):
    """把 composition 编译成 plan.composition；没有配置时返回 (None, [])。"""
    issues: list[dict] = []
    raw = doc.get("composition")
    if raw is None:
        for nid, node in nodes.items():
            if node.get("operation"):
                issues.append(_issue(LEVEL_ERROR, "节点引用了操作，但流程没有组合配置", node_id=nid))
        return None, issues

    if doc.get("diagnosis"):
        issues.append(_issue(LEVEL_ERROR, "组合配置与诊断类不能同时使用"))
    if not isinstance(raw, dict):
        return None, [_issue(LEVEL_ERROR, "组合配置必须是对象")]
    for nid, node in nodes.items():
        if node.get("kind") == "dataPreparation":
            issues.append(_issue(LEVEL_ERROR, "组合流程不再使用数据准备节点，请改为启动资源", node_id=nid))
        if node.get("pipeline"):
            issues.append(_issue(LEVEL_ERROR, "组合流程不再使用四阶段函数配置", node_id=nid))

    objects = _compile_objects(raw.get("objects"), issues)
    resources, resource_order, startup_objects, startup_owners = _compile_resources(
        raw.get("resources"), objects, issues)
    operations = _compile_operations(raw.get("operations"), objects, resources,
                                     startup_objects, startup_owners, issues)
    _compile_operation_nodes(nodes, operations, issues)
    _check_composition_signatures(objects, resources, operations, functions, issues)

    startup = [rid for rid in resource_order
               if resources[rid]["trigger"] == COMPOSITION_TRIGGERS[0]]
    return {
        "objects": objects,
        "resources": resources,
        "operations": operations,
        "startup": startup,
    }, issues


def _compile_objects(items, issues: list[dict]) -> dict:
    result: dict[str, dict] = {}
    if items is None:
        return result
    if not isinstance(items, list):
        issues.append(_issue(LEVEL_ERROR, "组合对象必须是数组"))
        return result
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(_issue(LEVEL_ERROR, f"组合对象第 {index + 1} 项必须是对象"))
            continue
        object_id = _text(item.get("id"))
        if not object_id:
            issues.append(_issue(LEVEL_ERROR, "组合对象缺少 id"))
            continue
        if object_id in result:
            issues.append(_issue(LEVEL_ERROR, f"组合对象 id 重复：{object_id}"))
            continue
        project = _text(item.get("project"))
        module = _text(item.get("module"))
        entry = _text(item.get("entry"))
        for name, value in (("工程", project), ("模块", module), ("构造器", entry)):
            if not value:
                issues.append(_issue(LEVEL_ERROR, f"组合对象 {object_id} 缺少{name}"))
        args = item.get("args") or {}
        if not isinstance(args, dict):
            issues.append(_issue(LEVEL_ERROR, f"组合对象 {object_id} 的构造参数必须是对象"))
            args = {}
        for name, expression in args.items():
            _check_argument(expression, f"对象 {object_id}.{name}", issues,
                            step_ids=set(), object_ids=set(result), resource_ids=set(),
                            parameters=set(), node_id=None,
                            allowed=("lit", "input", "system", "items", "fields"))
        if project and module and entry:
            result[object_id] = {"project": project, "module": module, "entry": entry, "args": args}
    return result


def _compile_resources(items, objects: dict, issues: list[dict]):
    result: dict[str, dict] = {}
    order: list[str] = []
    # 启动资源产出的对象，供后续操作直接引用；按需资源产出不进入这个集合。
    startup_objects: set[str] = set()
    startup_owners: dict[str, str] = {}
    if items is None:
        return result, order, startup_objects, startup_owners
    if not isinstance(items, list):
        issues.append(_issue(LEVEL_ERROR, "组合资源必须是数组"))
        return result, order, startup_objects, startup_owners
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(_issue(LEVEL_ERROR, f"组合资源第 {index + 1} 项必须是对象"))
            continue
        resource_id = _text(item.get("id"))
        if not resource_id:
            issues.append(_issue(LEVEL_ERROR, "组合资源缺少 id"))
            continue
        if resource_id in result:
            issues.append(_issue(LEVEL_ERROR, f"组合资源 id 重复：{resource_id}"))
            continue
        trigger = _text(item.get("trigger")) or "lazy"
        if trigger not in COMPOSITION_TRIGGERS:
            issues.append(_issue(LEVEL_ERROR, f"资源 {resource_id} 的触发方式必须是 start 或 lazy"))
            trigger = "lazy"
        available = set(objects) | startup_objects
        owners = {object_id: item["entry"] for object_id, item in objects.items()}
        owners.update(startup_owners)
        steps = _compile_steps(
            item.get("steps"), label=f"资源 {resource_id}", objects=objects,
            available=available, owners=owners, resource_ids=set(result), parameters=set(),
            allow_params=False, node_id=None, issues=issues,
        )
        if not steps:
            issues.append(_issue(LEVEL_ERROR, f"资源 {resource_id} 至少需要一个步骤"))
        result[resource_id] = {"id": resource_id, "trigger": trigger, "steps": steps}
        order.append(resource_id)
        if trigger == "start":
            startup_objects |= available - set(objects)
            startup_owners.update({key: value for key, value in owners.items()
                                   if key not in objects})
    return result, order, startup_objects, startup_owners


def _compile_operations(items, objects: dict, resources: dict, startup_objects: set[str],
                        startup_owners: dict, issues: list[dict]) -> dict:
    result: dict[str, dict] = {}
    if items is None:
        return result
    if not isinstance(items, dict):
        issues.append(_issue(LEVEL_ERROR, "组合操作必须是对象，键为操作 id"))
        return result
    for operation_id, item in items.items():
        operation_id = _text(operation_id)
        if not operation_id:
            issues.append(_issue(LEVEL_ERROR, "组合操作 id 不能为空"))
            continue
        if not isinstance(item, dict):
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 必须是对象"))
            continue
        declared = item.get("parameters")
        if declared is None:
            declared = _used_parameters(item.get("steps"))
        elif not isinstance(declared, list) or any(not _text(name) for name in declared):
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的 parameters 必须是名称数组"))
            declared = []
        declared = list(dict.fromkeys(_text(name) for name in declared if _text(name)))
        owners = {object_id: item["entry"] for object_id, item in objects.items()}
        owners.update(startup_owners)
        steps = _compile_steps(
            item.get("steps"), label=f"操作 {operation_id}", objects=objects,
            available=set(objects) | set(startup_objects), owners=owners,
            resource_ids=set(resources),
            parameters=set(declared), allow_params=True, node_id=None, issues=issues,
        )
        used = _used_parameters(item.get("steps"))
        for name in sorted(used - set(declared)):
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 使用了未声明的参数 {name}"))
        for name in sorted(set(declared) - used):
            issues.append(_issue(LEVEL_WARNING, f"操作 {operation_id} 声明了参数 {name} 但没有任何步骤使用"))
        if not steps:
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 至少需要一个步骤"))
        result[operation_id] = {
            "id": operation_id, "parameters": declared, "steps": steps,
            "result": _compile_result(item.get("result"), operation_id, steps, issues),
            "presentation": _compile_presentation(item.get("presentation"), operation_id, steps, issues),
        }
    return result


def _compile_steps(items, *, label: str, objects: dict, available: set[str],
                   owners: dict[str, str], resource_ids: set[str], parameters: set[str],
                   allow_params: bool, node_id, issues: list[dict]) -> list[dict]:
    if items is None:
        return []
    if not isinstance(items, list):
        issues.append(_issue(LEVEL_ERROR, f"{label}的 steps 必须是数组", node_id=node_id))
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(_issue(LEVEL_ERROR, f"{label}第 {index + 1} 步必须是对象", node_id=node_id))
            continue
        step_id = _text(item.get("id"))
        if not step_id:
            issues.append(_issue(LEVEL_ERROR, f"{label}第 {index + 1} 步缺少 id", node_id=node_id))
            continue
        if step_id in seen:
            issues.append(_issue(LEVEL_ERROR, f"{label}的步骤 id 重复：{step_id}", node_id=node_id))
            continue
        seen.add(step_id)

        project = _text(item.get("project"))
        module = _text(item.get("module"))
        function = _text(item.get("function"))
        kind = _text(item.get("kind")) or "function"
        if kind not in COMPOSITION_CALL_KINDS:
            issues.append(_issue(LEVEL_ERROR, f"{label}的步骤 {step_id} 调用类型不合法：{kind}", node_id=node_id))
            kind = "function"
        for name, value in (("工程", project), ("模块", module), ("接口", function)):
            if not value:
                issues.append(_issue(LEVEL_ERROR, f"{label}的步骤 {step_id} 缺少{name}", node_id=node_id))
        target = _text(item.get("target"))
        if kind == "method":
            if not target:
                issues.append(_issue(LEVEL_ERROR, f"{label}的步骤 {step_id} 调用实例方法必须指定对象", node_id=node_id))
            elif target not in available:
                issues.append(_issue(
                    LEVEL_ERROR,
                    f"{label}的步骤 {step_id} 引用了尚不存在的对象 {target}："
                    f"必须先声明对象，或由更早的步骤用 saveAs 产出",
                    node_id=node_id,
                ))
        elif target:
            issues.append(_issue(LEVEL_ERROR, f"{label}的步骤 {step_id} 只有实例方法才能指定对象", node_id=node_id))

        output = _text(item.get("output")) or "auto"
        if output not in COMPOSITION_OUTPUTS:
            issues.append(_issue(LEVEL_ERROR, f"{label}的步骤 {step_id} 的输出类型不合法：{output}", node_id=node_id))
            output = "auto"
        save_as = _text(item.get("saveAs"))
        if save_as:
            if output not in ("auto", "object"):
                issues.append(_issue(LEVEL_ERROR,
                                     f"{label}的步骤 {step_id} 只有对象输出才能用 saveAs 命名", node_id=node_id))
                save_as = ""
            elif save_as in objects or save_as in available:
                issues.append(_issue(LEVEL_ERROR, f"{label}的对象名重复：{save_as}", node_id=node_id))
                save_as = ""
        # 返回类型没有注解时无法静态推断对象属于哪个类；允许作者显式声明，签名才查得到。
        owner = _text(item.get("owner"))

        args = item.get("args") or {}
        if not isinstance(args, dict):
            issues.append(_issue(LEVEL_ERROR, f"{label}的步骤 {step_id} 的 args 必须是对象", node_id=node_id))
            args = {}
        allowed = ARGUMENT_KEYS if allow_params else tuple(k for k in ARGUMENT_KEYS if k != "param")
        for name, expression in args.items():
            _check_argument(expression, f"{label}.{step_id}.{name}", issues,
                            step_ids=set(seen) - {step_id}, object_ids=available,
                            resource_ids=resource_ids, parameters=parameters, node_id=node_id,
                            allowed=allowed)
        result.append({
            "id": step_id, "project": project, "module": module, "kind": kind,
            "target": target, "function": function, "output": output, "args": args,
            "interface": _interface_name(kind, function, target, owners, owner),
            **({"saveAs": save_as} if save_as else {}),
            **({"owner": owner} if owner else {}),
        })
        if save_as:
            available.add(save_as)
            produced = owner or _produced_owner(kind, function, target, owners)
            if produced:
                owners[save_as] = produced
            else:
                issues.append(_issue(
                    LEVEL_WARNING,
                    f"{label}的步骤 {step_id} 产出的对象 {save_as} 无法静态确定类型，"
                    f"方法调用不做签名校验；可加 owner 声明类名",
                    node_id=node_id,
                ))
    return result


def _interface_name(kind: str, function: str, target: str, owners: dict[str, str],
                    explicit: str = "") -> str:
    """探测/指纹用的接口全名；实例方法要带类名，同名方法才不会被混在一起。

    owner 声明在方法步骤上是「被调用对象属于哪个类」，声明在产出对象的步骤上
    是「返回对象属于哪个类」；只有前者改变接口全名。
    """
    if kind != "method":
        return function
    owner = explicit or owners.get(target)
    return f"{owner}.{function}" if owner else function


def _produced_owner(kind: str, function: str, target: str, owners: dict[str, str]) -> str:
    if kind == "method":
        return owners.get(target, "")
    if kind == "constructor":
        return function
    return function.rsplit(".", 1)[0] if "." in function else ""


def _check_argument(expression, where: str, issues: list[dict], *, step_ids, object_ids,
                    resource_ids, parameters, node_id, allowed) -> None:
    if not isinstance(expression, dict):
        issues.append(_issue(LEVEL_ERROR, f"{where} 的参数来源必须是对象", node_id=node_id))
        return
    keys = [key for key in expression if key in ARGUMENT_KEYS]
    if len(keys) != 1:
        issues.append(_issue(LEVEL_ERROR, f"{where} 必须且只能指定一种来源（{'/'.join(ARGUMENT_KEYS)}）",
                             node_id=node_id))
        return
    key = keys[0]
    if key not in allowed:
        issues.append(_issue(LEVEL_ERROR, f"{where} 不允许使用 {key} 来源", node_id=node_id))
        return
    if key == "lit":
        return
    if key == "input":
        if not _text(expression.get("input")):
            issues.append(_issue(LEVEL_ERROR, f"{where} 的启动输入名为空", node_id=node_id))
        return
    if key in ("step", "resource", "param", "object"):
        name = _text(expression.get(key))
        if not name:
            issues.append(_issue(LEVEL_ERROR, f"{where} 的 {key} 名称为空", node_id=node_id))
            return
        if key == "step" and name not in step_ids:
            issues.append(_issue(LEVEL_ERROR, f"{where} 引用了尚未执行的步骤 {name}；步骤只能引用它前面的结果",
                                 node_id=node_id))
        if key == "resource":
            if name not in resource_ids:
                issues.append(_issue(LEVEL_ERROR, f"{where} 引用了未声明的资源 {name}", node_id=node_id))
            elif not _text(expression.get("step")):
                issues.append(_issue(LEVEL_ERROR, f"{where} 引用资源时必须指定步骤", node_id=node_id))
        if key == "param" and name not in parameters:
            issues.append(_issue(LEVEL_ERROR, f"{where} 引用了未声明的操作参数 {name}", node_id=node_id))
        if key == "object" and name not in object_ids:
            issues.append(_issue(LEVEL_ERROR, f"{where} 引用了未声明的对象 {name}", node_id=node_id))
        return
    if key == "system":
        if _text(expression.get("system")) not in ("directory", "file"):
            issues.append(_issue(LEVEL_ERROR, f"{where} 的平台路径只能是 directory 或 file", node_id=node_id))
        return
    if key == "items":
        raw = expression.get("items")
        if not isinstance(raw, list):
            issues.append(_issue(LEVEL_ERROR, f"{where} 的 items 必须是数组", node_id=node_id))
            return
        for index, item in enumerate(raw):
            _check_argument(item, f"{where}[{index}]", issues, step_ids=step_ids, object_ids=object_ids,
                            resource_ids=resource_ids, parameters=parameters, node_id=node_id,
                            allowed=allowed)
        return
    if key == "fields":
        raw = expression.get("fields")
        if not isinstance(raw, dict):
            issues.append(_issue(LEVEL_ERROR, f"{where} 的 fields 必须是对象", node_id=node_id))
            return
        for name, item in raw.items():
            _check_argument(item, f"{where}.{name}", issues, step_ids=step_ids, object_ids=object_ids,
                            resource_ids=resource_ids, parameters=parameters, node_id=node_id,
                            allowed=allowed)


def _compile_result(item, operation_id: str, steps: list[dict], issues: list[dict]):
    if item is None:
        return None
    if not isinstance(item, dict):
        issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的 result 必须是对象"))
        return None
    step_id = _text(item.get("step"))
    if not step_id:
        issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的 result 必须指定步骤"))
        return None
    if step_id not in {step["id"] for step in steps}:
        issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的 result 引用了未执行的步骤 {step_id}"))
        return None
    for key in ("outcome", "reason", "normalReason"):
        if key in item and not isinstance(item.get(key), str):
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的 result.{key} 必须是字符串"))
    return {
        "step": step_id,
        "outcome": _text(item.get("outcome")),
        "reason": _text(item.get("reason")),
        "normalReason": _text(item.get("normalReason")),
    }


def _compile_presentation(items, operation_id: str, steps: list[dict], issues: list[dict]) -> list[dict]:
    if items is None:
        return []
    if not isinstance(items, list):
        issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的 presentation 必须是数组"))
        return []
    known = {step["id"] for step in steps}
    result = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or _text(item.get("type")) not in ("text", "image", "table", "metrics", "line"):
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的展示块第 {index + 1} 项类型不合法"))
            continue
        step_id = _text(item.get("step"))
        if step_id and step_id not in known:
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的展示块引用了未执行的步骤 {step_id}"))
            continue
        if not step_id and "text" not in item:
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 的展示块缺少步骤或文本"))
            continue
        result.append(item)
    return result


def _compile_operation_nodes(nodes: dict, operations: dict, issues: list[dict]) -> None:
    for nid, node in nodes.items():
        operation_id = _text(node.get("operation"))
        if not operation_id:
            if node.get("operationArguments"):
                issues.append(_issue(LEVEL_ERROR, "配置了操作参数但没有选择操作", node_id=nid))
            continue
        if node.get("kind") != "investigation":
            issues.append(_issue(LEVEL_ERROR, "只有排查节点可以绑定操作", node_id=nid))
            continue
        if node.get("binding") or node.get("pipeline") or node.get("method"):
            issues.append(_issue(LEVEL_ERROR, "绑定操作的节点不能再配置函数、四阶段或实例方法", node_id=nid))
            continue
        operation = operations.get(operation_id)
        if operation is None:
            issues.append(_issue(LEVEL_ERROR, f"节点引用了未声明的操作 {operation_id}", node_id=nid))
            continue
        arguments = node.get("operationArguments") or {}
        if not isinstance(arguments, dict):
            issues.append(_issue(LEVEL_ERROR, "操作参数必须是对象", node_id=nid))
            continue
        required = set(operation["parameters"])
        missing = sorted(required - set(arguments))
        extra = sorted(set(arguments) - required)
        if missing:
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 缺少参数：{', '.join(missing)}", node_id=nid))
        if extra:
            issues.append(_issue(LEVEL_ERROR, f"操作 {operation_id} 未声明参数：{', '.join(extra)}", node_id=nid))
        for name, expression in arguments.items():
            _check_argument(expression, f"节点 {nid}.{name}", issues, step_ids=set(),
                            object_ids=set(), resource_ids=set(),
                            parameters=required, node_id=nid, allowed=NODE_ARGUMENT_KEYS)


def _check_composition_signatures(objects: dict, resources: dict, operations: dict,
                                  functions: dict | None, issues: list[dict]) -> None:
    if functions is None:
        return

    def check(project: str, module: str, function: str, args: dict, where: str) -> None:
        if not project or not module or not function:
            return
        key = _composition_key(project, module, function)
        params = functions.get(key)
        if not isinstance(params, list):
            detail = params.get("error", "接口尚未探测") if isinstance(params, dict) else "接口尚未探测"
            issues.append(_issue(LEVEL_ERROR, f"{where} 的接口不可用：{detail}"))
            return
        missing = [param["name"] for param in params
                   if param.get("required", True) and param["name"] not in args]
        if missing:
            issues.append(_issue(LEVEL_ERROR, f"{where} 缺少必填参数：{', '.join(missing)}"))
        unknown = sorted(set(args) - {param["name"] for param in params})
        if unknown:
            issues.append(_issue(LEVEL_WARNING, f"{where} 含接口未声明的参数：{', '.join(unknown)}"))

    for object_id, item in objects.items():
        check(item["project"], item["module"], item["entry"], item.get("args") or {},
              f"对象 {object_id}")
    for resource_id, item in resources.items():
        for step in item["steps"]:
            check(step["project"], step["module"], step.get("interface") or step["function"],
                  step["args"], f"资源 {resource_id} 的步骤 {step['id']}")
    for operation_id, item in operations.items():
        for step in item["steps"]:
            check(step["project"], step["module"], step.get("interface") or step["function"],
                  step["args"], f"操作 {operation_id} 的步骤 {step['id']}")


def _composition_key(project: str, module: str, function: str) -> str:
    return f"{project}:{module}:{function}"


def _used_parameters(steps) -> set[str]:
    found: set[str] = set()

    def walk(expression) -> None:
        if isinstance(expression, dict):
            if "param" in expression:
                name = _text(expression.get("param"))
                if name:
                    found.add(name)
                return
            for key in ("items", "fields"):
                value = expression.get(key)
                if key == "items" and isinstance(value, list):
                    for item in value:
                        walk(item)
                elif key == "fields" and isinstance(value, dict):
                    for item in value.values():
                        walk(item)

    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        for expression in (step.get("args") or {}).values():
            walk(expression)
    return found


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
        return [str(k) for k in raw]
    if isinstance(raw, list):
        return [str(v) for v in raw if isinstance(v, str)]
    return []


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

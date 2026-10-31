"""doc → plan 校验与编译（spec 第 4、5 节）。

发布时执行一次，产物不可变；执行期不再读 doc。
"""

from __future__ import annotations

import logging

from . import rules

logger = logging.getLogger(__name__)

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

SNAPSHOT_SOURCE = "$snapshot"


class ValidationError(Exception):
    """doc 校验未通过。issues 与前端 LintIssue 对齐。"""

    def __init__(self, issues: list[dict]):
        self.issues = list(issues)
        message = "；".join(str(i.get("message", "")) for i in self.issues)
        super().__init__(message or "文档校验未通过")


def validate_doc(
    doc: dict, functions: dict | None = None, strict_binding: bool = True
) -> list[dict]:
    """返回 issue 列表；level=error 才算阻断。

    functions 传 probe_doc 的结果时按真实参数名校验绑定，否则按候选字段全量校验。
    strict_binding=False 供 commit 使用：参数绑定问题一律降级为 warning，半成品能存。
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
        if kind not in rules.V1_KINDS:
            issues.append(_issue(LEVEL_ERROR, f"v1 不支持的节点类型：{kind}", node_id=nid))

    valid_edges = _check_edges(edges, nodes_by_id, issues)
    outgoing = _outgoing_map(nodes_by_id, valid_edges)
    incoming: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    for src, targets in outgoing.items():
        for dst in targets:
            incoming[dst].append(src)

    _check_node_fields(nodes_by_id, issues)
    _check_out_edges(nodes_by_id, valid_edges, outgoing, issues)
    entry = _check_entry(nodes_by_id, incoming, issues)
    if entry is not None:
        _check_reachability(entry, nodes_by_id, outgoing, issues)
        _check_cycles(entry, outgoing, issues)
    _check_bindings(nodes_by_id, valid_edges, doc, issues, functions, strict_binding)
    return issues


def compile_doc(
    doc: dict, flow_id: str, release_no: int, functions: dict | None = None
) -> dict:
    """把 doc 编译成 plan。校验不过抛 ValidationError。

    functions 为 probe_doc 的结果；给了签名就只为函数真实参数绑定。
    """
    issues = [i for i in validate_doc(doc, functions) if i.get("level") == LEVEL_ERROR]
    if issues:
        raise ValidationError(issues)

    nodes_by_id = {n["id"]: n for n in doc["nodes"]}
    edges = [e for e in doc.get("edges", []) if isinstance(e, dict)]
    valid_edges = [e for e in edges if e.get("from") in nodes_by_id and e.get("to") in nodes_by_id]
    snapshot_fields = _snapshot_fields(doc)
    entry = _find_entry(nodes_by_id, valid_edges)

    steps: dict[str, dict] = {}
    for nid in sorted(nodes_by_id):
        node = nodes_by_id[nid]
        kind = node.get("kind", "")
        step: dict = {"kind": kind}

        if rules.is_terminal_kind(kind):
            step["terminal"] = True
        else:
            if kind in rules.HUMAN_ONLY_KINDS:
                step["human"] = True
            elif kind == "investigation":
                if node.get("binding"):
                    args, _ = _resolve_args(
                        node, nodes_by_id, valid_edges, snapshot_fields, functions
                    )
                    step["call"] = {
                        "project": str(node["binding"].get("project") or ""),
                        "function": str(node["binding"].get("function") or ""),
                        "args": args,
                    }
                else:
                    step["human"] = True
            elif kind == "conclusion":
                step["text"] = _text(node.get("detail")) or _text(node.get("title"))

            step["next"] = _next_map(nid, kind, valid_edges)
        steps[nid] = step

    calls = [
        f"{step['call'].get('project')}.{step['call'].get('function')}"
        for step in steps.values()
        if step.get("call")
    ]
    logger.info(
        "编译 flow=%s release=%s：%d 个节点，%d 个调用，%d 个人工节点，函数 %s",
        flow_id,
        release_no,
        len(nodes_by_id),
        len(calls),
        sum(1 for step in steps.values() if step.get("human")),
        sorted(set(calls)) or "无",
    )

    return {
        "format": 1,
        "flowId": flow_id,
        "release": release_no,
        "mode": doc.get("mode") or "parallel",
        "entry": entry,
        "steps": steps,
        "signature": {},
    }


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
                issues.append(_issue(LEVEL_ERROR, "结论节点必须恰好一条出线", node_id=nid))
        elif len(_unique(targets)) > 1:
            issues.append(_issue(LEVEL_ERROR, "不支持隐式分叉，请为每个后继单独建节点", node_id=nid))


def _check_entry(
    nodes_by_id: dict[str, dict], incoming: dict[str, list[str]], issues: list[dict]
) -> str | None:
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
    doc: dict,
    issues: list[dict],
    functions: dict | None = None,
    strict_binding: bool = True,
) -> None:
    """spec 4.8：参数来源必须在当前版本存在，且位于该节点上游。"""
    snapshot_fields = _snapshot_fields(doc)
    for nid, node in nodes_by_id.items():
        if node.get("kind") != "investigation" or not node.get("binding"):
            continue
        _, arg_issues = _resolve_args(node, nodes_by_id, edges, snapshot_fields, functions)
        for issue in arg_issues:
            if not strict_binding and issue.get("level") == LEVEL_ERROR:
                issue = {**issue, "level": LEVEL_WARNING}
            issues.append(issue)


# ---------------------------------------------------------------- 参数绑定


def _resolve_args(
    node: dict,
    nodes_by_id: dict[str, dict],
    edges: list[dict],
    snapshot_fields: list[str],
    functions: dict | None = None,
) -> tuple[dict, list[dict]]:
    """解析调用参数：显式 params 优先，其次按函数签名或候选字段自动绑定。

    有签名时只为真实参数绑定，多余候选一律不传；无签名时（向前兼容）按候选全量绑定并给警告。
    """
    node_id = str(node.get("id"))
    issues: list[dict] = []
    args: dict = {}
    candidates = _candidate_map(node_id, nodes_by_id, edges, snapshot_fields)
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
            sources = _unique(candidates.get(name, []))
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
                            f"缺少必填参数 {name}，候选：{_available(candidates)}",
                            node_id=node_id,
                        )
                    )
                continue  # 可选参数交给 Python 默认值
            args[name] = _expr_for_source(name, sources[0])
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
        sources = _unique(candidates[name])
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


def _available(candidates: dict[str, list[str]]) -> str:
    return ", ".join(sorted(candidates)) or "无"


def _candidate_map(
    node_id: str,
    nodes_by_id: dict[str, dict],
    edges: list[dict],
    snapshot_fields: list[str],
) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {}
    for field in snapshot_fields:
        candidates.setdefault(field, []).append(SNAPSHOT_SOURCE)
    for src in _upstream_ids(node_id, edges):
        if src not in nodes_by_id:
            continue
        for field in _declared_outputs(nodes_by_id[src]):
            candidates.setdefault(field, []).append(src)
    return candidates


def _expr_from_spec(
    name: str,
    spec: object,
    node_id: str,
    candidates: dict[str, list[str]],
    upstream: set[str],
    issues: list[dict],
) -> dict | None:
    if isinstance(spec, str):
        sources = _unique(candidates.get(spec, []))
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


def _expr_for_source(name: str, source: str) -> dict:
    if source == SNAPSHOT_SOURCE:
        return {"snapshot": name}
    return {"run": source, "path": f"output.{name}"}


def _declared_outputs(node: dict) -> list[str]:
    """节点可选的 outputs 声明；v1 的 types.ts 未定义，缺省视为空。"""
    raw = node.get("outputs")
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    if isinstance(raw, list):
        return [str(v) for v in raw if isinstance(v, str)]
    return []


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
    incoming = {nid: 0 for nid in nodes_by_id}
    for edge in edges:
        if edge.get("to") in incoming:
            incoming[edge["to"]] += 1
    for nid in nodes_by_id:
        if incoming[nid] == 0:
            return nid
    raise ValidationError([_issue(LEVEL_ERROR, "没有入口节点")])


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
        "investigation": "排查",
        "investigationAction": "排查动作",
        "conclusion": "结论",
        "remediation": "处置措施",
        "problemSolved": "问题解决",
        "escalation": "上升",
    }.get(kind, str(kind))

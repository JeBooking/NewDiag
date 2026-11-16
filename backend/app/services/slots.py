"""节点槽位：校验、接口探测与表达式遍历。

槽位是唯一的接入方式：每个槽位填一个外接工程的公开方法，参数来源只有
常量、启动输入、现场参数和上一槽返回值四种，平台不描述业务逻辑。
"""

from __future__ import annotations

SLOT_NAMES = ("constructs", "fetch", "process", "present", "decide")
SLOT_ALLOWED = {
    "start": ("constructs", "fetch", "process"),
    "investigation": ("fetch", "process", "present", "decide"),
}
VIEW_TYPES = ("image", "line", "metrics", "table", "text")
# previous 只能出现在链上位于构造、拉取之后的槽
PREVIOUS_SLOTS = ("process", "present", "decide")


def slots_of(node):
    """节点的槽位表；非法或缺失一律当空对象。"""
    slots = node.get("slots")
    return slots if isinstance(slots, dict) else {}


def calls_in_node(node):
    """按固定顺序产出节点上的 (槽位名, 调用)；constructs 展开成多次。"""
    slots = slots_of(node)
    for name in SLOT_NAMES:
        value = slots.get(name)
        if name == "constructs":
            for call in value if isinstance(value, list) else []:
                if isinstance(call, dict):
                    yield name, call
        elif isinstance(value, dict):
            if name == "decide" and value.get("human") and not (value.get("interface") or value.get("function")):
                # 判定槽选了人工判断时没有接口可探测
                continue
            yield name, value


def calls_in(doc):
    for node in doc.get("nodes") or []:
        if isinstance(node, dict):
            yield from calls_in_node(node)


def walk_argument(value):
    """遍历一个参数表达式里的全部节点（含 lit）。"""
    if not isinstance(value, dict):
        return
    yield value
    if "lit" in value:
        return
    for item in value.get("items", []):
        yield from walk_argument(item)
    for item in value.get("fields", {}).values():
        yield from walk_argument(item)


def call_arguments(call):
    values = call.get("arguments")
    return values if isinstance(values, dict) else {}


def interface_key(call):
    return f"{call.get('project', '')}:{call.get('module', '')}:{call.get('interface') or call.get('function', '')}"


def _shape_errors(doc):
    errors = []

    def argument(value, owner, where, allow_previous=True):
        if not isinstance(value, dict):
            errors.append(f"{owner} 的{where}必须为对象")
            return
        tags = set(value) & {"lit", "source", "items", "fields"}
        if len(tags) != 1:
            errors.append(f"{owner} 的{where}必须且只能指定一种来源")
            return
        if "source" in value:
            source = value.get("source")
            if source == "previous":
                if not allow_previous:
                    errors.append("构造参数不能引用上一槽返回值")
                return
            if source not in ("input", "parameter"):
                errors.append(f"{owner} 的{where}来源无效：{source}")
                return
            if not isinstance(value.get("name"), str) or not value["name"].strip():
                errors.append(f"{owner} 的{where}缺少名称")
            if "file" in value and not isinstance(value["file"], bool):
                errors.append(f"{owner} 的{where}文件选项必须为布尔值")
        elif "items" in value:
            items = value["items"]
            if not isinstance(items, list):
                errors.append(f"{owner} 的组合列表无效")
            else:
                for item in items:
                    argument(item, owner, where, allow_previous)
        elif "fields" in value:
            fields = value["fields"]
            if not isinstance(fields, dict):
                errors.append(f"{owner} 的组合字段无效")
            else:
                for item in fields.values():
                    argument(item, owner, where, allow_previous)

    nodes = [node for node in doc.get("nodes") or [] if isinstance(node, dict)]
    for node in nodes:
        kind = node.get("kind")
        label = f"节点 {node.get('id')}"
        slots = node.get("slots")
        if slots is None:
            continue
        if not isinstance(slots, dict):
            errors.append(f"{label} 的槽位必须为对象")
            continue
        allowed = SLOT_ALLOWED.get(kind)
        for name in slots:
            if name not in SLOT_NAMES:
                errors.append(f"{label} 的槽位名无效：{name}")
            elif allowed is not None and name not in allowed:
                errors.append(f"{label} 不能配置{name}槽")
        constructs = slots.get("constructs")
        if constructs is not None:
            if not isinstance(constructs, list) or not constructs:
                errors.append(f"{label} 的构造槽必须为非空列表")
            else:
                for call in constructs:
                    if not isinstance(call, dict):
                        errors.append(f"{label} 的构造槽必须为对象")
        for name in SLOT_NAMES:
            value = slots.get(name)
            if name == "constructs" or value is None:
                continue
            if not isinstance(value, dict):
                errors.append(f"{label} 的{name}槽必须为对象")
                continue
            if name == "present" and value.get("view") not in VIEW_TYPES:
                errors.append(f"{label} 的展示槽必须选择展示类型")
            if name != "present" and value.get("view"):
                errors.append(f"{label} 只有展示槽能指定展示类型")
            if name == "decide" and value.get("human") and (value.get("interface") or value.get("function")):
                errors.append(f"{label} 的判定槽要么选人工判断，要么选方法")
        for name, call in calls_in_node(node):
            owner = f"{label} 的{name}槽"
            allow_previous = name != "constructs"
            if not call.get("project"):
                errors.append(f"{owner} 缺少工程")
            if not (call.get("interface") or call.get("function")):
                errors.append(f"{owner} 缺少接口")
            if "useCache" in call:
                if name != "fetch":
                    errors.append(f"{owner} 不支持缓存开关：只有拉取槽会缓存")
                elif not isinstance(call["useCache"], bool):
                    errors.append(f"{owner} 的使用缓存开关必须为布尔值")
            arguments = call.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                errors.append(f"{owner} 的参数必须为参数名到来源的对象")
            else:
                for key, value in (arguments or {}).items():
                    if not isinstance(key, str) or not key.strip():
                        errors.append(f"{owner} 存在空参数名")
                    argument(value, owner, "参数", allow_previous)
        _previous_scope(slots, label, errors)
    return errors


def _uses_previous(value) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("source") == "previous":
        return True
    return (any(_uses_previous(item) for item in value.get("items") or [])
            or any(_uses_previous(item) for item in (value.get("fields") or {}).values()))


def _previous_scope(slots, label, errors):
    """「上一槽结果」只认本节点内的上游槽，跨节点不再传值。"""
    def uses(name):
        call = slots.get(name)
        return isinstance(call, dict) and any(
            _uses_previous(item) for item in (call.get("arguments") or {}).values())

    if uses("process") and not isinstance(slots.get("fetch"), dict):
        errors.append(f"{label} 的处理槽引用了上一槽结果，但本节点没有拉取槽")
    for name in ("present", "decide"):
        if uses(name) and not any(isinstance(slots.get(key), dict) for key in ("fetch", "process")):
            errors.append(f"{label} 的{name}槽引用了上一槽结果，但本节点没有拉取或处理槽")


def validate(doc, functions=None):
    """返回错误列表；functions 为探测结果时进一步核对接口、必填参数与现场参数。"""
    errors = _shape_errors(doc)
    nodes = [node for node in doc.get("nodes") or [] if isinstance(node, dict)]
    start = next((node for node in nodes if node.get("kind") == "start"), None)

    objects = {}
    for node in nodes:
        if node.get("kind") != "start":
            continue
        for call in slots_of(node).get("constructs") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("object") or "").strip()
            if not name.isidentifier():
                errors.append(f"构造槽的对象名必须是合法标识符：{name or '（空）'}")
            elif name in objects:
                errors.append(f"构造槽的对象名重复：{name}")
            else:
                objects[name] = call

    for node in nodes:
        kind = node.get("kind")
        slots = slots_of(node)
        label = f"节点 {node.get('id')}"
        if kind == "investigation" and slots:
            # 完全没有槽位的历史文档按人工节点执行，不强制展示与判定
            if not isinstance(slots.get("present"), dict):
                errors.append(f"{label} 缺少展示槽")
            decide = slots.get("decide")
            if not isinstance(decide, dict):
                errors.append(f"{label} 缺少判定槽")
            elif not decide.get("human") and not (decide.get("interface") or decide.get("function")):
                errors.append(f"{label} 的判定槽必须选择方法或人工判断")
        for name, call in calls_in_node(node):
            if name != "constructs":
                target = str(call.get("object") or "").strip()
                if target and target not in objects:
                    errors.append(f"{label} 的{name}槽引用了未声明的对象：{target}")

    for node in nodes:
        declared = {str(item.get("name")) for item in node.get("runtimeInputs") or []
                    if isinstance(item, dict) and item.get("name")}
        for name, call in calls_in_node(node):
            for value in call_arguments(call).values():
                for item in walk_argument(value):
                    if item.get("source") == "parameter" and item.get("name") not in declared:
                        errors.append(f"节点 {node.get('id')} 的{name}槽引用了未声明的现场参数 {item.get('name')}")

    if functions is not None:
        for node in nodes:
            for name, call in calls_in_node(node):
                if name == "decide" and call.get("human"):
                    continue
                key = interface_key(call)
                params = functions.get(key)
                if not isinstance(params, list):
                    errors.append(f"接口 {key} 尚未成功探测")
                    continue
                supplied = set(call_arguments(call))
                for param in params:
                    if param.get("required") and param["name"] not in supplied:
                        errors.append(f"节点 {node.get('id')} 的{name}槽缺少必填参数 {param['name']}")
                known = {param["name"] for param in params if not param["name"].startswith("$")}
                keywords = any(param["name"] == "$keywords" and param["type"] == "yes" for param in params)
                if not keywords and supplied - known:
                    errors.append(f"节点 {node.get('id')} 的{name}槽包含接口未声明的参数")
    return errors


def probe_slots(doc, projects=None):
    """按槽位探测接口签名；返回 {工程:模块:接口: [参数...] 或 {'error': ...}}。"""
    from .projects import probe
    from .signatures import _load_project

    result, catalogues = {}, {}
    for _, call in calls_in(doc):
        if call.get("human") and not (call.get("interface") or call.get("function")):
            continue
        project_name = call.get("project", "")
        module = call.get("module", "")
        interface = call.get("interface") or call.get("function", "")
        key = f"{project_name}:{module}:{interface}"
        cache_key = (project_name, module)
        if cache_key not in catalogues:
            project = projects.get(project_name) if projects is not None else _load_project(project_name)
            catalogues[cache_key] = probe({
                "name": project_name, "path": project.path, "python": project.python,
                "module": module or project.module, "catalog": True,
            }) if project else {"error": "工程未登记"}
        catalog = catalogues[cache_key]
        entry = next((item for item in catalog.get("functions", []) if item["name"] == interface), None)
        if key in result:
            continue
        if entry and entry.get("kind") == "instance_method" and not call.get("object"):
            result[key] = {"error": f"实例方法 {interface} 必须指定目标对象"}
        elif entry and entry.get("interfaceError"):
            result[key] = {"error": entry["interfaceError"]}
        elif not entry:
            result[key] = {"error": catalog.get("error") or f"公开接口 {interface} 不存在"}
        else:
            result[key] = [*entry["params"],
                           {"name": "$implementation", "type": entry.get("fingerprint", ""),
                            "required": False, "default": None},
                           {"name": "$keywords", "type": "yes" if entry.get("variadicKeywords") else "no",
                            "required": False, "default": None}]
    return result


def freeze_slots(plan, functions):
    """把接口实现指纹写进 plan 的槽位，执行前比对代码版本。"""
    for step in plan.get("steps", {}).values():
        for _, call in _step_calls(step):
            key = interface_key(call)
            call["fingerprint"] = next((item["type"] for item in (functions or {}).get(key, [])
                                        if item["name"] == "$implementation"), "")
    return plan


def _step_calls(step):
    slots = step.get("slots")
    if not isinstance(slots, dict):
        return
    for name in SLOT_NAMES:
        value = slots.get(name)
        if name == "constructs":
            for call in value if isinstance(value, list) else []:
                if isinstance(call, dict):
                    yield name, call
        elif isinstance(value, dict):
            if not (value.get("project") and (value.get("interface") or value.get("function"))):
                continue
            yield name, value


def plan_interfaces(plan):
    keys = set()
    for step in plan.get("steps", {}).values():
        for _, call in _step_calls(step):
            if call.get("project") and (call.get("interface") or call.get("function")):
                keys.add(interface_key(call))
    return keys


def probe_plan(plan):
    """把 plan 的槽位还原成探测输入，供没有 doc 的场景（启动前比对签名）使用。"""
    nodes = []
    for node_id, step in plan.get("steps", {}).items():
        slots = step.get("slots")
        if isinstance(slots, dict):
            nodes.append({"id": node_id, "kind": step.get("kind", "investigation"), "slots": slots})
    return probe_slots({"nodes": nodes})


def merge_slot_state(root, selected, latest):
    """回退：对象与构造引用回到目标到访，已取得的拉取结果继续保留。"""
    from app.worker import artifacts

    if not latest:
        return selected
    current = artifacts.load(root, latest) or {}
    previous = artifacts.load(root, selected) if selected else {}
    merged = {
        "format": "slot-runtime/v1",
        "objects": dict(previous.get("objects") or {}),
        "workers": dict(previous.get("workers") or {}),
        "cache": {**(previous.get("cache") or {}), **(current.get("cache") or {})},
    }
    return artifacts.save(root, merged)

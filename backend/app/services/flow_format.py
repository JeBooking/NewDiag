"""持久流程分层格式；旧格式在边界读取时展开，运行策略由实例提供。"""

from copy import deepcopy

NODE_BINDINGS = ("binding", "pipeline", "method", "runtimeInputs", "params", "outputs")


def expand(value):
    if not isinstance(value, dict):
        raise ValueError("流程文档必须是对象")
    doc = deepcopy(value)
    version = doc.get("format", 1)
    if version == 1:
        doc["format"] = 1
        doc.pop("mode", None)
        return doc
    if version != 2:
        raise ValueError(f"不支持的流程格式 {version}")
    core = doc.get("flow")
    if not isinstance(core, dict) or core.get("format") != 1:
        raise ValueError("不支持的业务流程定义格式")
    bindings = doc.get("bindings", {"format": 1})
    presentation = doc.get("presentation", {"format": 1})
    if not isinstance(bindings, dict) or bindings.get("format") != 1:
        raise ValueError("不支持的模块绑定格式")
    if not isinstance(core.get("nodes"), list) or not isinstance(core.get("edges"), list):
        raise ValueError("流程定义缺少 nodes 或 edges 数组")
    if not isinstance(bindings.get("nodes", {}), dict):
        raise ValueError("节点绑定必须是对象")
    result = {**core, "nodes": []}
    result.pop("mode", None)
    if bindings.get("diagnosis"):
        result["diagnosis"] = bindings["diagnosis"]
    positions = presentation.get("nodes", {}) if isinstance(presentation, dict) and presentation.get("format") == 1 else {}
    if not isinstance(positions, dict):
        positions = {}
    for index, node in enumerate(core.get("nodes", [])):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise ValueError("流程节点缺少 id")
        binding = bindings.get("nodes", {}).get(node["id"], {})
        if not isinstance(binding, dict):
            raise ValueError("节点绑定必须是对象")
        position = positions.get(node["id"], {})
        if not isinstance(position, dict):
            position = {}
        result["nodes"].append({
            **node, **{key: value for key, value in binding.items() if key in NODE_BINDINGS},
            "position": position.get("position", {"x": index * 200, "y": 100}),
        })
    return result


def compact(value):
    doc = expand(value)
    bindings = {"format": 1, "nodes": {}}
    if doc.get("diagnosis"):
        bindings["diagnosis"] = doc.pop("diagnosis")
    presentation = {"format": 1, "nodes": {}}
    for node in doc.get("nodes", []):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise ValueError("流程节点缺少 id")
        binding = {key: node.pop(key) for key in NODE_BINDINGS if key in node}
        if binding:
            bindings["nodes"][node["id"]] = binding
        if "position" in node:
            presentation["nodes"][node["id"]] = {"position": node.pop("position")}
    return {"format": 2, "flow": doc, "bindings": bindings, "presentation": presentation}

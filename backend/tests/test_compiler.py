"""validate_doc / compile_doc 的行为测试（spec 第 4、5 节）。

v1.16 只有组合配置一种接入方式：节点选择 operation，参数来源在组合配置里声明。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.compiler import ValidationError, compile_doc, validate_doc
from app.services.rules import can_connect, is_single_successor

START_ID = "start"


def composition() -> dict:
    return {
        "objects": [
            {"id": "motor", "project": "冷却系统", "module": "motor", "function": "Motor",
             "arguments": {}},
        ],
        "resources": [],
        "operations": [
            {
                "id": "checkVoltage",
                "parameters": ["threshold"],
                "calls": [
                    {"id": "voltage", "project": "冷却系统", "module": "motor",
                     "interface": "Motor.voltage", "function": "voltage",
                     "target": {"source": "object", "name": "motor"},
                     "arguments": {"device": {"source": "input", "name": "device"}}},
                ],
                "presentation": [],
                "decision": {"result": {"source": "value", "name": "voltage"},
                             "reason": {"lit": "读取母线电压"}},
            },
        ],
    }


def base_doc() -> dict:
    """开始 → 排查（组合操作）→ 结论 → 处置 → 问题解决 / 上升。"""
    return {
        "format": 1,
        "name": "冷却系统诊断",
        "composition": composition(),
        "nodes": [
            _start_node([{"name": "device", "type": "text"}]),
            _investigation("n1", "检查母线电压", "checkVoltage"),
            _node("c1", "conclusion", "电压不足", "母线电压低于 210V"),
            _node("n3", "remediation", "调整稳压器", "把稳压器输出调到 220V"),
            _node("n5", "problemSolved", "问题解决", "电压恢复正常"),
            _node("n9", "escalation", "上升", "转交厂家工程师"),
        ],
        "edges": [
            {"id": "s1", "from": START_ID, "to": "n1", "order": 1},
            {"id": "e1", "from": "n1", "to": "c1", "branch": "yes", "order": 1},
            {"id": "e2", "from": "n1", "to": "n9", "branch": "no", "order": 2},
            {"id": "e3", "from": "c1", "to": "n3", "order": 1},
            {"id": "e4", "from": "n3", "to": "n5", "order": 1},
        ],
    }


def _start_node(inputs: list[dict] | None = None, nid: str = START_ID) -> dict:
    node = {
        "id": nid,
        "kind": "start",
        "title": "开始",
        "detail": "",
        "position": {"x": 0, "y": 0},
    }
    if inputs is not None:
        node["inputs"] = inputs
    return node


def _node(nid, kind, title, detail) -> dict:
    return {"id": nid, "kind": kind, "title": title, "detail": detail,
            "position": {"x": 0, "y": 0}}


def _investigation(nid: str, title: str, operation: str | None) -> dict:
    node = _node(nid, "investigation", title, "读取母线电压是否低于 210V")
    node["fields"] = {
        "observation": "母线电压",
        "acquisition": "调用数据服务器读取 voltage",
        "assessment": "低于 210V 判为异常",
    }
    if operation:
        node["operation"] = operation
    return node


def errors(doc: dict, **kwargs) -> list[dict]:
    return [issue for issue in validate_doc(doc, **kwargs) if issue["level"] == "error"]


def messages(doc: dict, **kwargs) -> str:
    return " | ".join(issue["message"] for issue in errors(doc, **kwargs))


def edge(doc: dict, eid: str) -> dict:
    return next(item for item in doc["edges"] if item["id"] == eid)


def node(doc: dict, nid: str) -> dict:
    return next(item for item in doc["nodes"] if item["id"] == nid)


SIGNATURES = {
    "冷却系统:motor:Motor": [],
    "冷却系统:motor:Motor.voltage": [
        {"name": "device", "type": "str", "required": True, "default": None},
        {"name": "$keywords", "type": "no", "required": False, "default": None},
    ],
}


# ---------------------------------------------------------------- 正常路径


def test_valid_doc_compiles_to_plan():
    doc = base_doc()
    assert errors(doc) == []

    plan = compile_doc(doc, "f1", 3, functions=SIGNATURES)

    assert plan["format"] == 1
    assert plan["flowId"] == "f1"
    assert plan["release"] == 3
    assert plan["entry"] == START_ID
    assert plan["composition"]["operations"][0]["id"] == "checkVoltage"
    assert "mode" not in plan


def test_start_step_has_only_kind_and_next():
    plan = compile_doc(base_doc(), "f1", 1, functions=SIGNATURES)
    assert plan["steps"][START_ID] == {"kind": "start", "next": {"default": ["n1"]}}


def test_investigation_step_carries_operation_and_inputs():
    doc = base_doc()
    node(doc, "n1")["runtimeInputs"] = [{"name": "threshold", "type": "number"}]
    plan = compile_doc(doc, "f1", 1, functions=SIGNATURES)
    step = plan["steps"]["n1"]
    assert step["compositionOperation"] == "checkVoltage"
    assert step["operationInputs"] == [{"name": "threshold", "type": "number"}]
    assert step["next"] == {"yes": ["c1"], "no": ["n9"]}


def test_human_investigation_has_no_operation():
    doc = base_doc()
    node(doc, "n1").pop("operation")
    plan = compile_doc(doc, "f1", 1, functions=SIGNATURES)
    assert plan["steps"]["n1"]["human"] is True


def test_start_supports_multiple_successors():
    doc = base_doc()
    doc["nodes"].append(_investigation("n2", "另一条思路", None))
    doc["edges"].append({"id": "s2", "from": START_ID, "to": "n2", "order": 2})
    doc["edges"].append({"id": "e5", "from": "n2", "to": "n9", "branch": "no", "order": 1})
    assert errors(doc) == []
    assert compile_doc(doc, "f1", 1, functions=SIGNATURES)["steps"][START_ID]["next"] == {
        "default": ["n1", "n2"]
    }


# ---------------------------------------------------------------- 结构校验


def test_missing_start_rejected_on_publish():
    doc = base_doc()
    doc["nodes"] = [item for item in doc["nodes"] if item["id"] != START_ID]
    doc["edges"] = [item for item in doc["edges"] if item["from"] != START_ID]
    assert "缺少开始节点" in messages(doc)


def test_two_start_nodes_rejected():
    doc = base_doc()
    doc["nodes"].append(_start_node([{"name": "second"}], nid="start2"))
    assert "开始节点必须全局唯一" in messages(doc)


def test_start_with_incoming_edge_rejected():
    doc = base_doc()
    doc["edges"].append({"id": "back", "from": "n3", "to": START_ID})
    assert "开始节点不能有入线" in messages(doc)


def test_start_requires_outgoing_edge():
    doc = base_doc()
    doc["edges"] = [item for item in doc["edges"] if item["from"] != START_ID]
    assert "开始节点至少需要一条出线" in messages(doc)


def test_start_duplicate_target_rejected():
    doc = base_doc()
    doc["edges"].append({"id": "dup", "from": START_ID, "to": "n1"})
    assert "开始节点重复连接到" in messages(doc)


def test_start_input_type_and_duplicate_rejected():
    doc = base_doc()
    doc["nodes"][0]["inputs"] = [{"name": "device", "type": "text"},
                                 {"name": "device", "type": "text"}]
    assert "启动参数 device 重复" in messages(doc)
    doc["nodes"][0]["inputs"] = [{"name": "device", "type": "unsupported"}]
    assert "启动参数 device 的类型不支持" in messages(doc)


def test_runtime_inputs_require_operation():
    doc = base_doc()
    node(doc, "n1").pop("operation")
    node(doc, "n1")["runtimeInputs"] = [{"name": "threshold", "type": "number"}]
    assert "节点现场参数需要绑定排查操作" in messages(doc)


def test_removed_binding_fields_rejected():
    doc = base_doc()
    node(doc, "n1").pop("operation")
    node(doc, "n1")["binding"] = {"project": "旧工程", "function": "judge"}
    text = messages(doc)
    assert "已移除的旧接入字段" in text and "binding" in text


def test_data_preparation_node_rejected():
    doc = base_doc()
    doc["nodes"].append({**_node("prep", "dataPreparation", "数据准备", "旧节点"), "pipeline": {}})
    text = messages(doc)
    assert "数据准备节点已移除" in text


def test_removed_binding_fields_rejected():
    doc = base_doc()
    node(doc, "n1").pop("operation")
    node(doc, "n1")["binding"] = {"project": "旧工程", "function": "judge"}
    text = messages(doc)
    assert "已移除的旧接入字段" in text and "binding" in text


def test_data_preparation_node_rejected():
    doc = base_doc()
    doc["nodes"].append({**_node("prep", "dataPreparation", "数据准备", "旧节点"), "pipeline": {}})
    text = messages(doc)
    assert "数据准备节点已移除" in text


def test_missing_branch_rejected():
    doc = base_doc()
    edge(doc, "e1").pop("branch")
    assert "必须指定是/否分支" in messages(doc)


def test_conclusion_with_two_outgoing_rejected():
    doc = base_doc()
    doc["edges"].append({"id": "e6", "from": "c1", "to": "n9", "order": 2})
    assert "必须恰好一条出线" in messages(doc)


def test_jump_compiles_to_human_step():
    doc = base_doc()
    doc["nodes"].append(_node("j1", "jump", "转交专家", "转交给二线"))
    doc["edges"] = [item for item in doc["edges"] if item["id"] != "e4"]
    doc["edges"].append({"id": "e7", "from": "n3", "to": "j1", "order": 2})
    doc["edges"].append({"id": "e8", "from": "j1", "to": "n5", "order": 1})
    plan = compile_doc(doc, "f1", 1, functions=SIGNATURES)
    assert plan["steps"]["j1"]["human"] is True


def test_jump_connects_any_kind_except_start():
    assert can_connect("jump", "investigation")
    assert can_connect("jump", "remediation")
    assert not can_connect("jump", "start")
    assert is_single_successor("jump")


def test_jump_edge_to_start_rejected():
    doc = base_doc()
    doc["nodes"].append(_node("j1", "jump", "转交", "转交"))
    doc["edges"].append({"id": "e7", "from": "n3", "to": "j1", "order": 2})
    doc["edges"].append({"id": "e8", "from": "j1", "to": START_ID, "order": 1})
    assert "不允许的连接" in messages(doc)


def test_unreachable_terminal_rejected():
    doc = base_doc()
    doc["nodes"].append(_node("lonely", "remediation", "孤立", "孤立"))
    assert "节点不可达" in messages(doc)


def test_cycle_without_exit_rejected():
    doc = base_doc()
    doc["nodes"].append(_node("r1", "remediation", "反复", "反复"))
    doc["nodes"].append(_node("r2", "remediation", "反复2", "反复2"))
    doc["edges"].append({"id": "e7", "from": "n3", "to": "r1", "order": 2})
    doc["edges"].append({"id": "e8", "from": "r1", "to": "r2", "order": 1})
    doc["edges"].append({"id": "e9", "from": "r2", "to": "r1", "order": 1})
    assert "没有出口的环" in messages(doc)


def test_duplicate_node_id_rejected():
    doc = base_doc()
    doc["nodes"].append(_node("n1", "remediation", "重复", "重复"))
    assert "节点 id 重复" in messages(doc)


def test_investigation_requires_fields():
    doc = base_doc()
    node(doc, "n1")["fields"] = {"observation": "母线电压"}
    text = messages(doc)
    assert "取数方式" in text and "判定依据" in text


# ---------------------------------------------------------------- 组合配置校验


def test_operation_without_composition_rejected():
    doc = base_doc()
    doc.pop("composition")
    assert "选择排查操作前必须配置组合实例" in messages(doc)


def test_unknown_operation_rejected():
    doc = base_doc()
    node(doc, "n1")["operation"] = "missing"
    assert "引用的排查操作不存在" in messages(doc, functions=SIGNATURES)


def test_runtime_input_must_be_operation_parameter():
    doc = base_doc()
    node(doc, "n1")["runtimeInputs"] = [{"name": "unknown", "type": "number"}]
    assert "现场参数名称或类型无效" in messages(doc, functions=SIGNATURES)


def test_argument_and_runtime_input_conflict_rejected():
    doc = base_doc()
    node(doc, "n1")["operationArguments"] = {"threshold": {"lit": 1}}
    node(doc, "n1")["runtimeInputs"] = [{"name": "threshold", "type": "number"}]
    assert "不能同时配置来源和现场输入" in messages(doc, functions=SIGNATURES)


def test_input_argument_must_be_declared_on_start():
    doc = base_doc()
    doc["nodes"][0]["inputs"] = [{"name": "other", "type": "text"}]
    assert "启动输入 device 尚未在开始节点声明" in messages(doc, functions=SIGNATURES)


def test_node_source_must_be_upstream():
    doc = base_doc()
    doc["composition"]["operations"][0]["calls"][0]["arguments"]["device"] = {
        "source": "node", "name": "c1"
    }
    assert "只能引用上游节点结果" in messages(doc, functions=SIGNATURES)


def test_missing_probed_interface_rejected():
    doc = base_doc()
    assert "尚未成功探测" in messages(doc, functions={})


def test_missing_required_interface_argument_rejected():
    doc = base_doc()
    doc["composition"]["operations"][0]["calls"][0]["arguments"] = {}
    assert "缺少必填参数 device" in messages(doc, functions=SIGNATURES)


def test_lax_commit_downgrades_composition_issues():
    doc = base_doc()
    doc["composition"]["operations"][0]["calls"][0]["arguments"] = {}
    assert errors(doc, functions=SIGNATURES, strict_binding=True)
    lax = [issue for issue in validate_doc(doc, SIGNATURES, strict_binding=False)
           if issue["level"] == "error"]
    assert lax == []


def test_lax_commit_keeps_structural_errors():
    doc = base_doc()
    doc["edges"] = [item for item in doc["edges"] if item["from"] != START_ID]
    assert errors(doc, strict_binding=False)


def test_issues_are_json_safe():
    doc = base_doc()
    node(doc, "n1")["operation"] = "missing"
    json.dumps(validate_doc(doc, functions=SIGNATURES), ensure_ascii=False)


def test_validation_error_carries_issues():
    doc = base_doc()
    doc["edges"] = []
    with pytest.raises(ValidationError) as caught:
        compile_doc(doc, "f1", 1)
    assert caught.value.issues

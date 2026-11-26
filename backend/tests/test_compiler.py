"""validate_doc / compile_doc 的行为测试（spec 第 4、5、6 节）。

v1.17 只有一种接入方式：节点槽位；每个槽位填一个外接工程的公开方法，
参数来源只有固定值、启动输入、现场参数和上一槽结果四种。
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
PROJECT = "冷却系统"
MODULE = "motor"


def construct(name: str = "motor") -> dict:
    return {
        "object": name, "project": PROJECT, "module": MODULE,
        "interface": "Motor", "function": "Motor", "arguments": {},
    }


def slot(function: str, arguments: dict | None = None, **extra) -> dict:
    return {
        "object": "motor", "project": PROJECT, "module": MODULE,
        "interface": f"Motor.{function}", "function": function,
        "arguments": arguments or {}, **extra,
    }


def base_doc() -> dict:
    """开始（构造）→ 排查（拉取／处理／展示／判定）→ 结论 → 处置 → 问题解决 / 上升。"""
    return {
        "format": 1,
        "name": "冷却系统诊断",
        "nodes": [
            _start_node([{"name": "device", "type": "text"}], [construct()]),
            _investigation("n1"),
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


def _start_node(inputs: list[dict] | None = None, constructs: list[dict] | None = None,
                nid: str = START_ID) -> dict:
    node = {
        "id": nid,
        "kind": "start",
        "title": "开始",
        "detail": "",
        "position": {"x": 0, "y": 0},
    }
    if inputs is not None:
        node["inputs"] = inputs
    if constructs is not None:
        node["slots"] = {"constructs": constructs}
    return node


def _node(nid, kind, title, detail) -> dict:
    return {"id": nid, "kind": kind, "title": title, "detail": detail,
            "position": {"x": 0, "y": 0}}


def _investigation(nid: str, slots: dict | None = None) -> dict:
    node = _node(nid, "investigation", "检查母线电压", "读取母线电压是否低于 210V")
    node["fields"] = {
        "observation": "母线电压",
        "acquisition": "调用数据服务器读取 voltage",
        "assessment": "低于 210V 判为异常",
    }
    node["slots"] = slots if slots is not None else {
        "fetch": slot("voltage", {"device": {"source": "input", "name": "device"}}),
        "present": slot("plot", view="image"),
        "decide": slot("judge", {"value": {"source": "previous"}}),
    }
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
    f"{PROJECT}:{MODULE}:Motor": [],
    f"{PROJECT}:{MODULE}:Motor.voltage": [
        {"name": "device", "type": "str", "required": True, "default": None},
        {"name": "$keywords", "type": "no", "required": False, "default": None},
    ],
    f"{PROJECT}:{MODULE}:Motor.plot": [
        {"name": "$keywords", "type": "no", "required": False, "default": None},
    ],
    f"{PROJECT}:{MODULE}:Motor.judge": [
        {"name": "value", "type": "any", "required": False, "default": None},
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
    assert "composition" not in plan
    assert "mode" not in plan


def test_start_step_carries_constructs():
    plan = compile_doc(base_doc(), "f1", 1, functions=SIGNATURES)
    step = plan["steps"][START_ID]
    assert step["kind"] == "start"
    assert step["next"] == {"default": ["n1"]}
    assert step["slots"]["constructs"][0]["object"] == "motor"


def test_investigation_step_carries_slot_chain():
    plan = compile_doc(base_doc(), "f1", 1, functions=SIGNATURES)
    step = plan["steps"]["n1"]
    assert step["next"] == {"yes": ["c1"], "no": ["n9"]}
    assert step["slots"]["fetch"]["function"] == "voltage"
    assert step["slots"]["present"]["view"] == "image"
    assert step["slots"]["decide"]["function"] == "judge"
    assert step["slots"]["decide"]["fingerprint"] == ""


def test_cache_switch_compiled_into_step():
    doc = base_doc()
    node(doc, "n1")["slots"]["fetch"]["useCache"] = False
    step = compile_doc(doc, "f1", 1, functions=SIGNATURES)["steps"]["n1"]
    assert step["slots"]["fetch"]["useCache"] is False


def test_cache_switch_rejected_outside_fetch():
    doc = base_doc()
    node(doc, "n1")["slots"]["present"]["useCache"] = False
    assert "只有拉取槽会缓存" in messages(doc)


def test_runtime_inputs_compiled_into_step():
    doc = base_doc()
    node(doc, "n1")["runtimeInputs"] = [{"name": "scale", "type": "number"}]
    step = compile_doc(doc, "f1", 1, functions=SIGNATURES)["steps"]["n1"]
    assert step["runtimeInputs"] == [{"name": "scale", "type": "number"}]


def test_human_decide_keeps_slots():
    doc = base_doc()
    node(doc, "n1")["slots"]["decide"] = {"human": True}
    plan = compile_doc(doc, "f1", 1, functions=SIGNATURES)
    assert plan["steps"]["n1"]["slots"]["decide"] == {"human": True}
    assert not plan["steps"]["n1"].get("human")


def test_investigation_without_slots_is_human():
    doc = base_doc()
    node(doc, "n1").pop("slots")
    doc["nodes"][0]["slots"] = {"constructs": [construct()]}
    plan = compile_doc(doc, "f1", 1, functions=SIGNATURES)
    assert plan["steps"]["n1"]["human"] is True


def test_start_supports_multiple_successors():
    doc = base_doc()
    doc["nodes"].append(_investigation("n2"))
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
    doc["nodes"].append(_start_node([{"name": "second"}], [construct("motor2")], nid="start2"))
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


def test_runtime_inputs_require_slots():
    doc = base_doc()
    node(doc, "n1").pop("slots")
    node(doc, "n1")["runtimeInputs"] = [{"name": "threshold", "type": "number"}]
    assert "节点现场参数需要先配置槽位" in messages(doc)


def test_removed_binding_fields_rejected():
    doc = base_doc()
    node(doc, "n1")["operation"] = "old"
    text = messages(doc)
    assert "已移除的旧接入字段" in text and "operation" in text


def test_flow_level_composition_rejected():
    doc = base_doc()
    doc["composition"] = {"objects": [], "resources": [], "operations": []}
    assert "对象／资源／操作配置已移除" in messages(doc)


def test_data_preparation_node_rejected():
    doc = base_doc()
    doc["nodes"].append({**_node("prep", "dataPreparation", "数据准备", "旧节点"), "pipeline": {}})
    assert "数据准备节点已移除" in messages(doc)


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


# ---------------------------------------------------------------- 槽位校验


def test_function_only_flow_needs_no_construct():
    """纯函数工程不需要构造槽，只要没有槽位引用对象。"""
    doc = base_doc()
    doc["nodes"][0].pop("slots")
    for name in ("fetch", "present", "decide"):
        node(doc, "n1")["slots"][name].pop("object")
    assert errors(doc) == []


def test_construct_object_name_must_be_identifier():
    doc = base_doc()
    doc["nodes"][0]["slots"]["constructs"] = [construct("电机-1")]
    assert "合法标识符" in messages(doc)


def test_duplicate_construct_name_rejected():
    doc = base_doc()
    doc["nodes"][0]["slots"]["constructs"].append(construct())
    assert "构造槽的对象名重复" in messages(doc)


def test_slot_object_must_be_declared():
    doc = base_doc()
    node(doc, "n1")["slots"]["present"]["object"] = "unknown"
    assert "引用了未声明的对象" in messages(doc)


def test_missing_slots_are_allowed():
    """槽位有没有填不拦发布：只要文字写全就能过，缺口留给运行时暴露"""
    doc = base_doc()
    node(doc, "n1")["slots"] = {}
    assert errors(doc) == []


def test_present_view_is_optional():
    doc = base_doc()
    node(doc, "n1")["slots"]["present"].pop("view")
    assert errors(doc) == []


def test_only_present_carries_view():
    doc = base_doc()
    node(doc, "n1")["slots"]["fetch"]["view"] = "image"
    assert "只有展示槽能指定展示类型" in messages(doc)


def test_decide_can_be_omitted():
    doc = base_doc()
    node(doc, "n1")["slots"].pop("decide")
    assert errors(doc) == []


def test_empty_decide_is_allowed():
    doc = base_doc()
    node(doc, "n1")["slots"]["decide"] = {}
    assert errors(doc) == []


def test_human_decide_cannot_carry_interface():
    doc = base_doc()
    node(doc, "n1")["slots"]["decide"] = {**slot("judge"), "human": True}
    assert "要么选人工判断" in messages(doc)


def test_previous_rejected_in_construct():
    doc = base_doc()
    doc["nodes"][0]["slots"]["constructs"][0]["arguments"] = {"values": {"source": "previous"}}
    assert "构造参数不能引用上一槽返回值" in messages(doc)


def test_previous_requires_upstream_slot_in_node():
    """上一槽结果只认本节点内的上游槽，跨节点不传值。"""
    doc = base_doc()
    node(doc, "n1")["slots"].pop("fetch")
    assert "没有拉取或处理槽" in messages(doc)


def test_process_previous_requires_fetch():
    doc = base_doc()
    node(doc, "n1")["slots"].pop("fetch")
    node(doc, "n1")["slots"]["process"] = slot("plot", {"data": {"source": "previous"}})
    assert "没有拉取槽" in messages(doc)


def test_decide_can_use_presentation_result():
    doc = base_doc()
    node(doc, "n1")["slots"]["decide"]["arguments"] = {"items": {"source": "presentation"}}
    assert "展示结果" not in messages(doc)


def test_presentation_requires_present_slot():
    doc = base_doc()
    node(doc, "n1")["slots"]["decide"]["arguments"] = {"items": {"source": "presentation"}}
    node(doc, "n1")["slots"].pop("present")
    assert "只有判定槽能这样做" in messages(doc)


def test_presentation_rejected_outside_decide():
    doc = base_doc()
    node(doc, "n1")["slots"]["present"]["arguments"] = {"data": {"source": "presentation"}}
    assert "只有判定槽能这样做" in messages(doc)


def test_removed_sources_rejected():
    doc = base_doc()
    node(doc, "n1")["slots"]["fetch"]["arguments"] = {"device": {"source": "resource", "name": "x"}}
    assert "来源无效" in messages(doc)


def test_parameter_must_be_declared():
    doc = base_doc()
    node(doc, "n1")["slots"]["fetch"]["arguments"] = {"device": {"source": "parameter", "name": "device"}}
    assert "未声明的现场参数 device" in messages(doc)


def test_input_must_be_declared_on_start():
    doc = base_doc()
    doc["nodes"][0]["inputs"] = [{"name": "other", "type": "text"}]
    assert "未在开始节点声明的启动输入 device" in messages(doc)


def test_missing_probed_interface_rejected():
    doc = base_doc()
    assert "尚未成功探测" in messages(doc, functions={})


def test_missing_required_interface_argument_rejected():
    doc = base_doc()
    node(doc, "n1")["slots"]["fetch"]["arguments"] = {}
    assert "缺少必填参数 device" in messages(doc, functions=SIGNATURES)


def test_unknown_interface_argument_rejected():
    doc = base_doc()
    node(doc, "n1")["slots"]["fetch"]["arguments"] = {
        "device": {"source": "input", "name": "device"}, "other": {"lit": 1}}
    assert "接口未声明的参数" in messages(doc, functions=SIGNATURES)


def test_lax_commit_downgrades_slot_issues():
    doc = base_doc()
    node(doc, "n1")["slots"]["fetch"]["arguments"] = {}
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
    node(doc, "n1")["slots"]["present"].pop("view")
    json.dumps(validate_doc(doc, functions=SIGNATURES), ensure_ascii=False)


def test_validation_error_carries_issues():
    doc = base_doc()
    doc["edges"] = []
    with pytest.raises(ValidationError) as caught:
        compile_doc(doc, "f1", 1)
    assert caught.value.issues


def test_title_only_nodes_do_not_need_detail():
    """结论、上升、问题解决只写标题，正文留空不该被拦"""
    doc = base_doc()
    for item in doc["nodes"]:
        if item["kind"] in {"conclusion", "escalation", "problemSolved"}:
            item["detail"] = ""
    assert errors(doc) == []


def test_detail_errors_use_the_label_the_panel_shows():
    """同一个 detail 字段，面板上叫操作步骤／链接，报错也要跟着叫，不能笼统说缺少说明"""
    doc = base_doc()
    node(doc, "n3")["detail"] = ""
    assert "缺少操作步骤" in messages(doc)

    doc = base_doc()
    node(doc, "n1")["detail"] = ""
    assert "缺少说明" in messages(doc)

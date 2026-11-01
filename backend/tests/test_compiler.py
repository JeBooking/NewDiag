"""validate_doc / compile_doc 的行为测试（spec 第 4、5 节）。"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.compiler import ValidationError, compile_doc, validate_doc

START_ID = "start"


def base_doc() -> dict:
    """新格式：开始节点 → 排查 → 结论 → 处置 → 问题解决 / 上升。"""
    return {
        "format": 1,
        "name": "冷却系统诊断",
        "mode": "parallel",
        "nodes": [
            _start_node([{"name": "voltage", "scope": "global", "type": "number"}]),
            _node("n1", "investigation", "检查母线电压", "读取母线电压是否低于 210V", code=True),
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


def legacy_doc() -> dict:
    """旧格式：没有 start 节点，用 snapshotFields 表示全局参数。"""
    doc = base_doc()
    doc["nodes"] = [node for node in doc["nodes"] if node["id"] != START_ID]
    doc["edges"] = [edge for edge in doc["edges"] if edge["from"] != START_ID]
    doc["snapshotFields"] = ["voltage"]
    return doc


def _start_node(inputs: list[dict] | None = None) -> dict:
    node = {
        "id": START_ID,
        "kind": "start",
        "title": "开始",
        "detail": "",
        "position": {"x": 0, "y": 0},
    }
    if inputs is not None:
        node["inputs"] = inputs
    return node


def _node(nid, kind, title, detail, code=False) -> dict:
    node = {"id": nid, "kind": kind, "title": title, "detail": detail, "position": {"x": 0, "y": 0}}
    if kind == "investigation":
        node["fields"] = {
            "observation": "母线电压",
            "acquisition": "调用数据服务器读取 voltage",
            "assessment": "低于 210V 判为异常",
        }
        if code:
            node["binding"] = {"project": "cooling", "function": "judge_voltage"}
            node["outputs"] = ["voltage"]
    return node


def errors(doc: dict, **kwargs) -> list[dict]:
    return [issue for issue in validate_doc(doc, **kwargs) if issue["level"] == "error"]


def messages(doc: dict, **kwargs) -> str:
    return " | ".join(issue["message"] for issue in errors(doc, **kwargs))


def edge(doc: dict, eid: str) -> dict:
    return next(item for item in doc["edges"] if item["id"] == eid)


def node(doc: dict, nid: str) -> dict:
    return next(item for item in doc["nodes"] if item["id"] == nid)


VOLTAGE_SIGNATURE = {
    "cooling:judge_voltage": [
        {"name": "voltage", "type": "float", "required": True, "default": None}
    ]
}


# ---------------------------------------------------------------- 正常路径


def test_valid_doc_compiles_to_plan():
    doc = base_doc()
    # 无签名时只给 warning，不阻断
    assert errors(doc) == []

    plan = compile_doc(doc, "f1", 3)

    assert plan["format"] == 1
    assert plan["flowId"] == "f1"
    assert plan["release"] == 3
    assert plan["mode"] == "parallel"
    assert plan["entry"] == START_ID
    assert plan["signature"] == {}
    # steps 按节点 id 排序写入，保证同一 doc 编译结果可复现
    assert list(plan["steps"]) == sorted(plan["steps"])

    n1 = plan["steps"]["n1"]
    assert n1["call"]["project"] == "cooling"
    assert n1["call"]["function"] == "judge_voltage"
    assert n1["call"]["args"] == {"voltage": {"snapshot": "voltage"}}
    assert n1["next"] == {"yes": ["c1"], "no": ["n9"]}

    assert plan["steps"]["c1"]["text"] == "母线电压低于 210V"
    assert plan["steps"]["c1"]["next"] == {"default": ["n3"]}
    assert plan["steps"]["n3"]["human"] is True
    assert plan["steps"]["n5"]["terminal"] is True
    assert plan["steps"]["n9"]["terminal"] is True


def test_start_step_has_only_kind_and_next():
    plan = compile_doc(base_doc(), "f1", 1)

    assert plan["steps"][START_ID] == {"kind": "start", "next": {"default": ["n1"]}}


def test_start_supports_multiple_successors():
    doc = base_doc()
    doc["nodes"].append(_node("n0", "investigation", "旁路排查", "另开一条排查线"))
    doc["edges"].append({"id": "s2", "from": START_ID, "to": "n0", "order": 2})
    doc["edges"].append({"id": "e10", "from": "n0", "to": "n9", "branch": "yes", "order": 1})

    assert errors(doc) == []
    plan = compile_doc(doc, "f1", 1)
    # 多出边按 order 排序，运行时一次全部投递
    assert plan["steps"][START_ID]["next"] == {"default": ["n1", "n0"]}


def test_human_investigation_has_no_call():
    doc = base_doc()
    del node(doc, "n1")["binding"]
    del node(doc, "n1")["outputs"]

    plan = compile_doc(doc, "f1", 1)

    assert plan["steps"]["n1"]["human"] is True
    assert "call" not in plan["steps"]["n1"]


def test_explicit_param_binding_is_used():
    doc = base_doc()
    node(doc, "n1")["params"] = {"v": {"snapshot": "voltage"}, "limit": {"lit": 210}}

    assert validate_doc(doc) == []
    plan = compile_doc(doc, "f1", 1)
    assert plan["steps"]["n1"]["call"]["args"] == {
        "v": {"snapshot": "voltage"},
        "limit": {"lit": 210},
    }


def test_snapshot_field_binding():
    doc = base_doc()
    doc["snapshotFields"] = ["threshold"]
    node(doc, "n1")["params"] = {"limit": "threshold"}

    plan = compile_doc(doc, "f1", 1)
    assert plan["steps"]["n1"]["call"]["args"] == {"limit": {"snapshot": "threshold"}}


# ---------------------------------------------------------------- 开始节点校验


def test_missing_start_rejected_on_publish():
    doc = legacy_doc()

    assert "缺少开始节点" in messages(doc)
    with pytest.raises(ValidationError):
        compile_doc(doc, "f1", 1)


def test_two_start_nodes_rejected():
    doc = base_doc()
    second = _start_node()
    second["id"] = "start2"
    doc["nodes"].append(second)
    doc["edges"].append({"id": "s9", "from": "start2", "to": "n1", "order": 2})

    assert "全局唯一" in messages(doc)


def test_start_with_incoming_edge_rejected():
    doc = base_doc()
    doc["edges"].append({"id": "bad", "from": "n1", "to": START_ID, "order": 3})

    start_issues = [i for i in errors(doc) if "入线" in i["message"]]
    assert start_issues, messages(doc)
    assert start_issues[0]["edgeId"] == "bad"
    assert start_issues[0]["nodeId"] == START_ID


def test_start_requires_outgoing_edge():
    doc = base_doc()
    doc["edges"] = [edge for edge in doc["edges"] if edge["from"] != START_ID]

    start_issues = [i for i in errors(doc) if "开始节点至少需要一条出线" in i["message"]]
    assert start_issues, messages(doc)
    assert start_issues[0]["nodeId"] == START_ID


def test_start_duplicate_target_rejected():
    """start 出边会一次性全部投递，重复连接等于同一节点跑两遍。"""
    doc = base_doc()
    doc["edges"].append({"id": "s2", "from": START_ID, "to": "n1", "order": 2})

    assert "重复连接" in messages(doc)


def test_start_rejects_binding_and_params():
    doc = base_doc()
    start = node(doc, START_ID)
    start["binding"] = {"project": "cooling", "function": "judge_voltage"}
    start["params"] = {"voltage": {"snapshot": "voltage"}}

    text = messages(doc)
    assert "不允许绑定函数" in text
    assert "不允许配置参数绑定" in text


def test_start_input_scope_requires_function():
    doc = base_doc()
    node(doc, START_ID)["inputs"] = [{"name": "voltage", "scope": "function"}]

    assert "没有指定函数" in messages(doc)


def test_start_input_duplicate_rejected():
    doc = base_doc()
    node(doc, START_ID)["inputs"] = [
        {"name": "voltage", "scope": "global"},
        {"name": "voltage", "scope": "global"},
    ]

    assert "启动参数 voltage 重复" in messages(doc)


# ---------------------------------------------------------------- 旧文档兼容


def test_legacy_doc_without_start_still_compiles():
    doc = legacy_doc()

    plan = compile_doc(doc, "f1", 1, require_start=False)

    assert plan["entry"] == "n1"
    assert "start" not in plan["steps"]
    # snapshotFields 视为全局参数，继续可用
    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_legacy_multiple_entries_rejected():
    doc = legacy_doc()
    doc["nodes"].append(_node("x1", "investigation", "另一个排查", "孤立入口"))
    doc["nodes"].append(_node("x2", "escalation", "上升", "孤立出口"))
    doc["edges"].append({"id": "ex1", "from": "x1", "to": "x2", "branch": "yes"})

    text = messages(doc, require_start=False)
    assert "入口" in text
    assert "2" in text


def test_snapshot_fields_merge_into_global_params():
    """迁移期文档同时有 start.inputs 和 snapshotFields，同名字段只算一次。"""
    doc = base_doc()
    doc["snapshotFields"] = ["voltage", "current"]
    node(doc, "n1")["outputs"] = []
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None},
            {"name": "current", "type": "float", "required": True, "default": None},
        ]
    }

    plan = compile_doc(doc, "f1", 1, functions=functions)

    assert plan["steps"]["n1"]["call"]["args"] == {
        "voltage": {"snapshot": "voltage"},
        "current": {"snapshot": "current"},
    }


# ---------------------------------------------------------------- 绑定可见性


def test_global_param_satisfies_required_param():
    doc = base_doc()
    node(doc, "n1")["outputs"] = []

    plan = compile_doc(doc, "f1", 1, functions=VOLTAGE_SIGNATURE)

    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_function_scoped_param_visible_to_its_function():
    doc = base_doc()
    node(doc, START_ID)["inputs"] = [
        {"name": "voltage", "scope": "function", "function": "judge_voltage"}
    ]
    node(doc, "n1")["outputs"] = []

    assert errors(doc, functions=VOLTAGE_SIGNATURE) == []
    plan = compile_doc(doc, "f1", 1, functions=VOLTAGE_SIGNATURE)
    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_function_scoped_param_not_leaked_to_other_function():
    doc = base_doc()
    node(doc, START_ID)["inputs"] = [
        {"name": "voltage", "scope": "function", "function": "judge_voltage"}
    ]
    node(doc, "n1")["binding"] = {"project": "cooling", "function": "judge_current"}
    node(doc, "n1")["outputs"] = []
    functions = {
        "cooling:judge_current": [
            {"name": "voltage", "type": "float", "required": True, "default": None}
        ]
    }

    issues = errors(doc, functions=functions)
    assert any("函数 judge_current 的必填参数 voltage 没有来源" in i["message"] for i in issues)
    assert issues[0]["nodeId"] == START_ID


def test_global_param_beats_upstream_output():
    """同名时全局启动参数优先，不再因为上游也有同名字段报歧义。"""
    doc = base_doc()
    doc["nodes"].append(_node("n2", "investigation", "复核", "复核电压"))
    node(doc, "n2")["outputs"] = ["voltage"]
    node(doc, "n2")["binding"] = {"project": "cooling", "function": "judge_voltage"}
    # 拆成 n1 → n2 → 结论 / 上升，让 n2 同时具备上游输出和全局参数两个同名来源
    doc["edges"] = [e for e in doc["edges"] if e["id"] not in ("e1", "e2")]
    doc["edges"].append({"id": "e20", "from": "n1", "to": "n2", "branch": "yes", "order": 1})
    doc["edges"].append({"id": "e21", "from": "n2", "to": "c1", "branch": "yes", "order": 1})
    doc["edges"].append({"id": "e22", "from": "n2", "to": "n9", "branch": "no", "order": 2})

    assert errors(doc, functions=VOLTAGE_SIGNATURE) == []
    plan = compile_doc(doc, "f1", 1, functions=VOLTAGE_SIGNATURE)
    assert plan["steps"]["n2"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_missing_required_param_points_at_start():
    doc = base_doc()
    node(doc, START_ID)["inputs"] = [{"name": "current", "scope": "global"}]
    node(doc, "n1")["outputs"] = []

    with pytest.raises(ValidationError) as info:
        compile_doc(doc, "f1", 1, functions=VOLTAGE_SIGNATURE)

    issue = next(i for i in info.value.issues if "voltage" in i["message"])
    assert "函数 judge_voltage 的必填参数 voltage 没有来源" in issue["message"]
    assert issue["nodeId"] == START_ID


# ---------------------------------------------------------------- 校验失败


def test_missing_branch_rejected():
    doc = base_doc()
    edge(doc, "e1")["branch"] = None

    assert "分支" in messages(doc)


def test_conclusion_with_two_outgoing_rejected():
    doc = base_doc()
    doc["edges"].append({"id": "e9", "from": "c1", "to": "n9", "order": 2})

    assert "恰好一条出线" in messages(doc)


def test_unreachable_terminal_rejected():
    doc = base_doc()
    doc["nodes"].append(_node("x1", "investigation", "孤立排查", "不在主路径上"))
    doc["nodes"].append(_node("x2", "investigationAction", "孤立动作", "不在主路径上"))
    # 环有出口（x1 → c1），但整块从入口不可达
    doc["edges"].append({"id": "ex1", "from": "x1", "to": "x2"})
    doc["edges"].append({"id": "ex2", "from": "x2", "to": "x1"})
    doc["edges"].append({"id": "ex3", "from": "x1", "to": "c1", "branch": "yes"})

    assert "不可达" in messages(doc)


def test_cycle_without_exit_rejected():
    doc = base_doc()
    doc["nodes"].append(_node("x1", "investigation", "环路排查", "只在本环节"))
    doc["nodes"].append(_node("x2", "investigationAction", "环路动作", "只在本环节"))
    doc["edges"].append({"id": "ex1", "from": "x1", "to": "x2"})
    doc["edges"].append({"id": "ex2", "from": "x2", "to": "x1"})
    doc["edges"].append({"id": "ex3", "from": "n1", "to": "x1", "branch": "yes"})

    text = messages(doc)
    assert "环" in text


def test_duplicate_node_id_rejected():
    doc = base_doc()
    dup = copy.deepcopy(node(doc, "c1"))
    dup["title"] = "重复结论"
    doc["nodes"].append(dup)

    assert "节点 id 重复" in messages(doc)


def test_binding_requires_project_and_function():
    doc = base_doc()
    node(doc, "n1")["binding"] = {"project": "", "function": ""}

    text = messages(doc)
    assert "工程" in text and "函数" in text


def test_investigation_requires_fields():
    doc = base_doc()
    node(doc, "n1")["fields"]["assessment"] = ""

    assert "判定依据" in messages(doc)


# ---------------------------------------------------------------- 参数绑定


def ambiguous_doc() -> dict:
    """两个上游节点都声明 voltage，下游无法自动绑定。"""
    doc = {
        "format": 1,
        "name": "参数歧义",
        "mode": "parallel",
        "nodes": [
            _start_node(),
            _node("n1", "investigation", "读电压", "读母线电压", code=True),
            _node("n2", "investigation", "复核电压", "再读一次母线电压"),
            _node("n3", "investigation", "判定", "判断是否需要处置"),
            _node("c1", "conclusion", "判定结论", "需要处置"),
            _node("r1", "remediation", "处置", "按结论处置"),
            _node("n5", "problemSolved", "问题解决", "已处置"),
            _node("n9", "escalation", "上升", "转交厂家"),
        ],
        "edges": [
            {"id": "s0", "from": START_ID, "to": "n1", "order": 1},
            {"id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1},
            {"id": "e2", "from": "n1", "to": "n9", "branch": "no", "order": 2},
            {"id": "e3", "from": "n2", "to": "n3", "branch": "yes", "order": 1},
            {"id": "e4", "from": "n2", "to": "n9", "branch": "no", "order": 2},
            {"id": "e5", "from": "n3", "to": "c1", "branch": "yes", "order": 1},
            {"id": "e6", "from": "n3", "to": "n9", "branch": "no", "order": 2},
            {"id": "e7", "from": "c1", "to": "r1", "order": 1},
            {"id": "e8", "from": "r1", "to": "n5", "order": 1},
        ],
    }
    doc["nodes"][2]["outputs"] = ["voltage"]  # n2 也声明了 outputs
    doc["nodes"][3]["binding"] = {"project": "cooling", "function": "judge_voltage"}
    return doc


def test_ambiguous_param_rejected():
    doc = ambiguous_doc()

    text = messages(doc)
    assert "voltage" in text
    assert "多个来源" in text
    assert "n1" in text and "n2" in text
    with pytest.raises(ValidationError) as info:
        compile_doc(doc, "f1", 1)
    assert any("多个来源" in issue["message"] for issue in info.value.issues)


def test_param_source_must_be_upstream():
    doc = ambiguous_doc()
    node(doc, "n3")["params"] = {"v": {"run": "n5", "path": "output.voltage"}}

    assert "不在上游" in messages(doc)


def test_missing_param_source_rejected():
    doc = ambiguous_doc()
    node(doc, "n3")["params"] = {"v": "current"}

    text = messages(doc)
    assert "不存在或不在上游" in text
    assert "voltage" in text  # 提示可用字段


def test_explicit_param_disambiguates():
    doc = ambiguous_doc()
    node(doc, "n3")["params"] = {"v": {"run": "n2", "path": "output.voltage"}}

    assert errors(doc) == []
    plan = compile_doc(doc, "f1", 1)
    assert plan["steps"]["n3"]["call"]["args"] == {"v": {"run": "n2", "path": "output.voltage"}}


def test_issues_are_json_safe():
    doc = base_doc()
    edge(doc, "e1")["branch"] = None
    for issue in validate_doc(doc):
        json.dumps(issue)  # 必须能直接进 API 响应
        assert set(issue) <= {"level", "message", "nodeId", "edgeId"}


def test_signature_binds_only_real_params():
    """函数只有一个参数，候选有三个字段：plan 里只能出现那一个。"""
    doc = base_doc()
    doc["snapshotFields"] = ["current", "temperature"]
    node(doc, "n1")["outputs"] = []

    plan = compile_doc(doc, "f1", 1, functions=VOLTAGE_SIGNATURE)

    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_signature_omits_optional_param_without_source():
    doc = base_doc()
    node(doc, "n1")["outputs"] = []
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None},
            {"name": "threshold", "type": "float", "required": False, "default": 210.0},
        ]
    }

    plan = compile_doc(doc, "f1", 1, functions=functions)

    # threshold 缺来源又有默认值，直接省略，交给 Python
    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_no_signature_emits_warning():
    doc = base_doc()
    node(doc, "n1")["outputs"] = []

    issues = validate_doc(doc)
    assert [issue for issue in issues if issue["level"] == "error"] == []
    warnings = [issue for issue in issues if issue["level"] == "warning"]
    assert any("未提供函数签名" in issue["message"] for issue in warnings)
    # 无签名时仍然全量绑定
    plan = compile_doc(doc, "f1", 1)
    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_signature_ignores_ambiguous_unused_field():
    """候选里同名多来源，但函数用不到该字段时不该拦。"""
    doc = ambiguous_doc()
    node(doc, "n3")["params"] = {}
    functions = {
        "cooling:judge_voltage": [
            {"name": "current", "type": "float", "required": False, "default": 0.0}
        ]
    }
    doc["snapshotFields"] = ["current"]

    assert errors(doc, functions=functions) == []
    plan = compile_doc(doc, "f1", 1, functions=functions)
    assert plan["steps"]["n3"]["call"]["args"] == {
        "current": {"snapshot": "current", "optional": True}
    }


def test_params_outside_signature_rejected():
    doc = base_doc()
    node(doc, "n1")["params"] = {"nope": {"lit": 1}}

    with pytest.raises(ValidationError) as info:
        compile_doc(doc, "f1", 1, functions=VOLTAGE_SIGNATURE)
    text = " ".join(issue["message"] for issue in info.value.issues)
    assert "不在函数签名里" in text
    assert "voltage" in text


def test_unused_candidate_warns():
    doc = base_doc()
    doc["snapshotFields"] = ["current"]
    node(doc, "n1")["outputs"] = []

    issues = validate_doc(doc, VOLTAGE_SIGNATURE)
    assert [i for i in issues if i["level"] == "error"] == []
    warnings = " ".join(i["message"] for i in issues if i["level"] == "warning")
    assert "current" in warnings and "不在函数签名里" in warnings

    plan = compile_doc(doc, "f1", 1, functions=VOLTAGE_SIGNATURE)
    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


# ---------------------------------------------------------------- 轻校验开关


def test_lax_binding_downgrades_ambiguity():
    doc = ambiguous_doc()

    strict = errors(doc)
    assert any("多个来源" in i["message"] for i in strict)

    lax = validate_doc(doc, strict_binding=False)
    assert [i for i in lax if i["level"] == "error"] == []
    assert any(i["level"] == "warning" and "多个来源" in i["message"] for i in lax)


def test_lax_binding_keeps_structural_errors():
    doc = ambiguous_doc()
    node(doc, "n1")["fields"]["assessment"] = ""

    lax = validate_doc(doc, strict_binding=False)
    assert any(i["level"] == "error" and "判定依据" in i["message"] for i in lax)


def test_lax_binding_downgrades_missing_required_param():
    doc = base_doc()
    node(doc, START_ID)["inputs"] = [{"name": "current", "scope": "global"}]
    node(doc, "n1")["outputs"] = []

    lax = validate_doc(doc, VOLTAGE_SIGNATURE, strict_binding=False)
    assert [i for i in lax if i["level"] == "error"] == []
    assert any(
        i["level"] == "warning" and "必填参数 voltage 没有来源" in i["message"] for i in lax
    )


def test_optional_param_carries_optional_flag():
    """可选参数有来源时仍然绑定，但带上 optional：运行期缺值就省略。"""
    doc = base_doc()
    node(doc, "n1")["outputs"] = []
    node(doc, START_ID)["inputs"].append({"name": "threshold", "scope": "global"})
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None},
            {"name": "threshold", "type": "float", "required": False, "default": 210.0},
        ]
    }

    plan = compile_doc(doc, "f1", 1, functions=functions)

    assert plan["steps"]["n1"]["call"]["args"] == {
        "voltage": {"snapshot": "voltage"},
        "threshold": {"snapshot": "threshold", "optional": True},
    }


def test_optional_param_from_upstream_output_carries_optional_flag():
    doc = base_doc()
    node(doc, "n1")["outputs"] = ["rating"]
    doc["nodes"].append(_node("n2", "investigation", "复核", "复核电压"))
    node(doc, "n2")["binding"] = {"project": "cooling", "function": "judge_rating"}
    doc["edges"] = [e for e in doc["edges"] if e["id"] not in ("e1", "e2")]
    doc["edges"].append({"id": "e20", "from": "n1", "to": "n2", "branch": "yes", "order": 1})
    doc["edges"].append({"id": "e21", "from": "n2", "to": "c1", "branch": "yes", "order": 1})
    doc["edges"].append({"id": "e22", "from": "n2", "to": "n9", "branch": "no", "order": 2})
    functions = {
        "cooling:judge_rating": [
            {"name": "rating", "type": "float", "required": False, "default": 1.0}
        ]
    }

    plan = compile_doc(doc, "f1", 1, functions=functions)

    assert plan["steps"]["n2"]["call"]["args"] == {
        "rating": {"run": "n1", "path": "output.rating", "optional": True}
    }


def test_explicit_param_is_never_optional():
    """作者显式绑定的来源就是要传，缺值必须报错。"""
    doc = base_doc()
    node(doc, "n1")["outputs"] = []
    node(doc, "n1")["params"] = {"threshold": {"snapshot": "threshold"}}
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None},
            {"name": "threshold", "type": "float", "required": False, "default": 210.0},
        ]
    }

    plan = compile_doc(doc, "f1", 1, functions=functions)

    assert plan["steps"]["n1"]["call"]["args"]["threshold"] == {"snapshot": "threshold"}


# ---------------------------------------------------------------- 探针默认值


def test_probe_marks_repr_default_untrusted():
    """repr 兜底的默认值不能当真实默认值用，必须带不可信标记。"""
    from app.worker.probe import _describe

    moment = datetime(2020, 1, 1)

    def sample(count=3, when=moment, tags=("a", "b")):
        pass

    probed = {p["name"]: p for p in _describe("sample", sample)["params"]}
    assert probed["count"]["defaultTrusted"] is True
    assert probed["tags"]["defaultTrusted"] is True
    assert probed["when"]["defaultTrusted"] is False
    assert probed["when"]["default"] == repr(moment)


def test_param_list_keeps_trust_flag_without_changing_hash():
    """信任标记只是附带信息，不能改变签名指纹，否则已发布版本会全部报签名变更。"""
    from app.services import signatures

    params = [
        {
            "name": "when",
            "type": "datetime",
            "required": False,
            "default": "x",
            "defaultTrusted": False,
        }
    ]
    listed = signatures._param_list({"name": "f", "params": params})
    assert listed[0]["defaultTrusted"] is False

    trusted = [{**params[0], "defaultTrusted": True}]
    assert signatures._hash_params("f", listed) == signatures._hash_params("f", trusted)

"""validate_doc / compile_doc 的行为测试（spec 第 4、5 节）。"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.compiler import ValidationError, compile_doc, validate_doc


def base_doc() -> dict:
    return {
        "format": 1,
        "name": "冷却系统诊断",
        "mode": "parallel",
        "nodes": [
            _node("n1", "investigation", "检查母线电压", "读取母线电压是否低于 210V", code=True),
            _node("c1", "conclusion", "电压不足", "母线电压低于 210V"),
            _node("n3", "remediation", "调整稳压器", "把稳压器输出调到 220V"),
            _node("n5", "problemSolved", "问题解决", "电压恢复正常"),
            _node("n9", "escalation", "上升", "转交厂家工程师"),
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "c1", "branch": "yes", "order": 1},
            {"id": "e2", "from": "n1", "to": "n9", "branch": "no", "order": 2},
            {"id": "e3", "from": "c1", "to": "n3", "order": 1},
            {"id": "e4", "from": "n3", "to": "n5", "order": 1},
        ],
    }


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


def errors(doc: dict) -> list[dict]:
    return [issue for issue in validate_doc(doc) if issue["level"] == "error"]


def messages(doc: dict) -> str:
    return " | ".join(issue["message"] for issue in errors(doc))


def edge(doc: dict, eid: str) -> dict:
    return next(item for item in doc["edges"] if item["id"] == eid)


# ---------------------------------------------------------------- 正常路径


def test_valid_doc_compiles_to_plan():
    doc = base_doc()
    # 无签名时只给 warning，不阻断
    assert [issue for issue in validate_doc(doc) if issue["level"] == "error"] == []

    plan = compile_doc(doc, "f1", 3)

    assert plan["format"] == 1
    assert plan["flowId"] == "f1"
    assert plan["release"] == 3
    assert plan["mode"] == "parallel"
    assert plan["entry"] == "n1"
    assert plan["signature"] == {}
    # steps 按节点 id 排序写入，保证同一 doc 编译结果可复现
    assert list(plan["steps"]) == sorted(plan["steps"])

    n1 = plan["steps"]["n1"]
    assert n1["call"]["project"] == "cooling"
    assert n1["call"]["function"] == "judge_voltage"
    assert n1["call"]["args"] == {}  # 入口节点没有上游，只能靠快照或显式绑定
    assert n1["next"] == {"yes": ["c1"], "no": ["n9"]}

    assert plan["steps"]["c1"]["text"] == "母线电压低于 210V"
    assert plan["steps"]["c1"]["next"] == {"default": ["n3"]}
    assert plan["steps"]["n3"]["human"] is True
    assert plan["steps"]["n5"]["terminal"] is True
    assert plan["steps"]["n9"]["terminal"] is True


def test_human_investigation_has_no_call():
    doc = base_doc()
    del doc["nodes"][0]["binding"]
    del doc["nodes"][0]["outputs"]

    plan = compile_doc(doc, "f1", 1)

    assert plan["steps"]["n1"]["human"] is True
    assert "call" not in plan["steps"]["n1"]


def test_explicit_param_binding_is_used():
    doc = base_doc()
    doc["nodes"][0]["params"] = {"v": {"snapshot": "voltage"}, "limit": {"lit": 210}}

    assert validate_doc(doc) == []
    plan = compile_doc(doc, "f1", 1)
    assert plan["steps"]["n1"]["call"]["args"] == {
        "v": {"snapshot": "voltage"},
        "limit": {"lit": 210},
    }


def test_snapshot_field_binding():
    doc = base_doc()
    doc["snapshotFields"] = ["threshold"]
    doc["nodes"][0]["params"] = {"limit": "threshold"}

    plan = compile_doc(doc, "f1", 1)
    assert plan["steps"]["n1"]["call"]["args"] == {"limit": {"snapshot": "threshold"}}


# ---------------------------------------------------------------- 校验失败


def test_multiple_entries_rejected():
    doc = base_doc()
    doc["nodes"].append(_node("x1", "investigation", "另一个排查", "孤立入口"))
    doc["nodes"].append(_node("x2", "escalation", "上升", "孤立出口"))
    doc["edges"].append({"id": "ex1", "from": "x1", "to": "x2", "branch": "yes"})

    text = messages(doc)
    assert "入口" in text
    assert "2" in text
    with pytest.raises(ValidationError):
        compile_doc(doc, "f1", 1)


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
    dup = copy.deepcopy(doc["nodes"][1])
    dup["title"] = "重复结论"
    doc["nodes"].append(dup)

    assert "节点 id 重复" in messages(doc)


def test_binding_requires_project_and_function():
    doc = base_doc()
    doc["nodes"][0]["binding"] = {"project": "", "function": ""}

    text = messages(doc)
    assert "工程" in text and "函数" in text


def test_investigation_requires_fields():
    doc = base_doc()
    doc["nodes"][0]["fields"]["assessment"] = ""

    assert "判定依据" in messages(doc)


# ---------------------------------------------------------------- 参数绑定


def ambiguous_doc() -> dict:
    """两个上游节点都声明 voltage，下游无法自动绑定。"""
    doc = {
        "format": 1,
        "name": "参数歧义",
        "mode": "parallel",
        "nodes": [
            _node("n1", "investigation", "读电压", "读母线电压", code=True),
            _node("n2", "investigation", "复核电压", "再读一次母线电压"),
            _node("n3", "investigation", "判定", "判断是否需要处置"),
            _node("c1", "conclusion", "判定结论", "需要处置"),
            _node("r1", "remediation", "处置", "按结论处置"),
            _node("n5", "problemSolved", "问题解决", "已处置"),
            _node("n9", "escalation", "上升", "转交厂家"),
        ],
        "edges": [
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
    doc["nodes"][1]["outputs"] = ["voltage"]  # n1 也声明了 outputs
    doc["nodes"][2]["binding"] = {"project": "cooling", "function": "judge_voltage"}
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
    doc["nodes"][2]["params"] = {"v": {"run": "n5", "path": "output.voltage"}}

    assert "不在上游" in messages(doc)


def test_missing_param_source_rejected():
    doc = ambiguous_doc()
    doc["nodes"][2]["params"] = {"v": "current"}

    text = messages(doc)
    assert "不存在或不在上游" in text
    assert "voltage" in text  # 提示可用字段


def test_explicit_param_disambiguates():
    doc = ambiguous_doc()
    doc["nodes"][2]["params"] = {"v": {"run": "n2", "path": "output.voltage"}}

    assert [i for i in validate_doc(doc) if i["level"] == "error"] == []
    plan = compile_doc(doc, "f1", 1)
    assert plan["steps"]["n3"]["call"]["args"] == {"v": {"run": "n2", "path": "output.voltage"}}


def test_issues_are_json_safe():
    doc = base_doc()
    edge(doc, "e1")["branch"] = None
    for issue in validate_doc(doc):
        json.dumps(issue)  # 必须能直接进 API 响应
        assert set(issue) <= {"level", "message", "nodeId", "edgeId"}

def test_signature_binds_only_real_params():
    """函数只有一个参数，快照有三个字段：plan 里只能出现那一个。"""
    doc = base_doc()
    doc["snapshotFields"] = ["voltage", "current", "temperature"]
    doc["nodes"][0]["outputs"] = []
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None}
        ]
    }

    plan = compile_doc(doc, "f1", 1, functions=functions)

    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_signature_omits_optional_param_without_source():
    doc = base_doc()
    doc["snapshotFields"] = ["voltage"]
    doc["nodes"][0]["outputs"] = []
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None},
            {"name": "threshold", "type": "float", "required": False, "default": 210.0},
        ]
    }

    plan = compile_doc(doc, "f1", 1, functions=functions)

    # threshold 缺来源又有默认值，直接省略，交给 Python
    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}


def test_required_param_without_source_rejected():
    doc = base_doc()
    doc["snapshotFields"] = ["current"]
    doc["nodes"][0]["outputs"] = []
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None}
        ]
    }

    with pytest.raises(ValidationError) as info:
        compile_doc(doc, "f1", 1, functions=functions)

    assert "voltage" in str(info.value)
    assert any("缺少必填参数 voltage" in issue["message"] for issue in info.value.issues)


def test_no_signature_emits_warning():
    doc = base_doc()
    doc["snapshotFields"] = ["voltage"]
    doc["nodes"][0]["outputs"] = []

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
    doc["nodes"][2]["params"] = {}
    functions = {
        "cooling:judge_voltage": [
            {"name": "current", "type": "float", "required": False, "default": 0.0}
        ]
    }
    doc["snapshotFields"] = ["current"]

    assert [i for i in validate_doc(doc, functions) if i["level"] == "error"] == []
    plan = compile_doc(doc, "f1", 1, functions=functions)
    assert plan["steps"]["n3"]["call"]["args"] == {"current": {"snapshot": "current"}}

# ---------------------------------------------------------------- 轻校验开关


def test_lax_binding_downgrades_ambiguity():
    doc = ambiguous_doc()

    strict = [i for i in validate_doc(doc) if i["level"] == "error"]
    assert any("多个来源" in i["message"] for i in strict)

    lax = validate_doc(doc, strict_binding=False)
    assert [i for i in lax if i["level"] == "error"] == []
    assert any(i["level"] == "warning" and "多个来源" in i["message"] for i in lax)


def test_lax_binding_keeps_structural_errors():
    doc = ambiguous_doc()
    doc["nodes"][0]["fields"]["assessment"] = ""

    lax = validate_doc(doc, strict_binding=False)
    assert any(i["level"] == "error" and "判定依据" in i["message"] for i in lax)


def test_lax_binding_downgrades_missing_required_param():
    doc = base_doc()
    doc["snapshotFields"] = ["current"]
    doc["nodes"][0]["outputs"] = []
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None}
        ]
    }

    lax = validate_doc(doc, functions=functions, strict_binding=False)
    assert [i for i in lax if i["level"] == "error"] == []
    assert any(i["level"] == "warning" and "缺少必填参数 voltage" in i["message"] for i in lax)


def test_params_outside_signature_rejected():
    doc = base_doc()
    doc["nodes"][0]["params"] = {"nope": {"lit": 1}}
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None}
        ]
    }

    with pytest.raises(ValidationError) as info:
        compile_doc(doc, "f1", 1, functions=functions)
    messages = " ".join(issue["message"] for issue in info.value.issues)
    assert "不在函数签名里" in messages
    assert "voltage" in messages


def test_unused_candidate_warns():
    doc = base_doc()
    doc["snapshotFields"] = ["voltage", "current"]
    doc["nodes"][0]["outputs"] = []
    functions = {
        "cooling:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None}
        ]
    }

    issues = validate_doc(doc, functions)
    assert [i for i in issues if i["level"] == "error"] == []
    warnings = " ".join(i["message"] for i in issues if i["level"] == "warning")
    assert "current" in warnings and "不在函数签名里" in warnings

    plan = compile_doc(doc, "f1", 1, functions=functions)
    assert plan["steps"]["n1"]["call"]["args"] == {"voltage": {"snapshot": "voltage"}}

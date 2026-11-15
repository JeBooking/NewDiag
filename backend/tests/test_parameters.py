"""启动表单与现场参数的类型校验。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.worker.node_sdk import validate_fields


def start_api():
    """延迟导入：避免在测试夹具配置数据库之前绑定旧引擎。"""
    from app.api import instances

    return instances


def test_start_context_accepts_global_and_flat_shapes():
    api = start_api()
    assert api.normalize_start_input({"global": {"voltage": 1}}) == {"global": {"voltage": 1}}
    assert api.normalize_start_input({"voltage": 1}) == {"global": {"voltage": 1}}
    # 旧前端提交的 scope 层不再进入上下文
    assert api.normalize_start_input({"global": {"a": 1}, "scope": {"f": {"b": 2}}}) == {
        "global": {"a": 1}
    }
    assert api.normalize_start_input(None) == {"global": {}}


def test_defaults_do_not_override_user_values():
    api = start_api()
    context = {"global": {"voltage": 205.0}}
    fields = [{"name": "voltage", "type": "number", "required": False, "default": 210.0},
              {"name": "chamber", "type": "text", "required": False, "default": "A1"}]
    api.apply_defaults(fields, context)
    assert context["global"] == {"voltage": 205.0, "chamber": "A1"}


def test_json_null_default_is_a_value():
    api = start_api()
    context = {"global": {}}
    api.apply_defaults([{"name": "options", "type": "json", "required": False, "default": None}], context)
    assert context["global"]["options"] is None
    api.apply_defaults([{"name": "flag", "type": "json", "required": False, "default": False}], context)
    assert context["global"]["flag"] is False


def test_missing_inputs_skips_optional_and_empty_text():
    api = start_api()
    fields = [
        {"name": "device", "type": "text", "required": True},
        {"name": "note", "type": "text", "required": False},
        {"name": "options", "type": "json", "required": True},
    ]
    assert api.missing_inputs(fields, {"global": {"options": None}}) == ["device"]


def test_validate_fields_keeps_native_structures():
    fields = [
        {"name": "samples", "type": "array"},
        {"name": "thresholds", "type": "object"},
        {"name": "options", "type": "json"},
        {"name": "evidence", "type": "file"},
    ]
    data = {
        "samples": [1, 2, 6],
        "thresholds": {"max": 3},
        "options": None,
        "evidence": {"hash": "0" * 64},
    }
    values = validate_fields(fields, data)
    assert values["samples"] == [1, 2, 6]
    assert values["thresholds"] == {"max": 3}
    assert values["options"] is None
    assert values["evidence"] == {"hash": "0" * 64}


@pytest.mark.parametrize("data", [
    {"samples": "[1,2,6]"},
    {"thresholds": []},
    {"evidence": {"hash": 1}},
])
def test_validate_fields_rejects_wrong_shapes(data):
    fields = [
        {"name": "samples", "type": "array"},
        {"name": "thresholds", "type": "object"},
        {"name": "evidence", "type": "file"},
    ]
    with pytest.raises(ValueError):
        validate_fields(fields, data)


def test_validate_fields_rejects_unknown_and_empty_required():
    with pytest.raises(ValueError):
        validate_fields([{"name": "a", "type": "text"}], {"b": 1})
    with pytest.raises(ValueError):
        validate_fields([{"name": "a", "type": "text", "required": True}], {"a": ""})

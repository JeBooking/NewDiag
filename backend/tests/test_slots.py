"""拉取缓存与「每次重新拉取」开关（worker/slot_runtime.py、services/slots.py）。"""

from __future__ import annotations

import sys

import pytest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.slots import validate
from app.worker.slot_runtime import SlotRuntime

CALL = {"project": "数据", "module": "feeder", "interface": "DataCenter.fetch",
        "function": "fetch", "arguments": {}}


def runtime(tmp_path):
    calls = []

    def invoke(project, request):
        calls.append(request)
        return {"value": f"v{len(calls)}"}

    return SlotRuntime(str(tmp_path), invoke), calls


def test_fetch_uses_cache_by_default(tmp_path):
    slot, calls = runtime(tmp_path)
    assert slot.fetch(dict(CALL)) == "v1"
    assert slot.fetch(dict(CALL)) == "v1"
    assert len(calls) == 1


def test_disabling_cache_refetches_and_updates_it(tmp_path):
    slot, calls = runtime(tmp_path)
    assert slot.fetch(dict(CALL)) == "v1"

    assert slot.fetch({**CALL, "useCache": False}) == "v2"
    assert len(calls) == 2

    # 重新拉取的值写回缓存，后续默认读取拿到新值
    assert slot.fetch(dict(CALL)) == "v2"
    assert len(calls) == 2


def test_decide_reads_presentation_result(tmp_path):
    """判定槽的「展示结果」拿到的是 present 这次调用的返回值。"""
    values = {
        'View.metrics': [{'label': '峰值', 'value': 82.8}],
        'Rules.check': {'result': True, 'reason': '峰值越线'},
    }

    def invoke(project, request):
        return {'value': values[request['interface']]}

    runtime = SlotRuntime(str(tmp_path), invoke)
    step = {'slots': {
        'present': {'project': 'p', 'interface': 'View.metrics', 'function': 'metrics',
                    'arguments': {}, 'view': 'metrics'},
        'decide': {'project': 'p', 'interface': 'Rules.check', 'function': 'check',
                   'arguments': {'items': {'source': 'presentation'}}},
    }}
    result = runtime.run(step)
    assert result['result'] == 'yes'
    assert result['reason'] == '峰值越线'
    assert result['blocks'][0]['type'] == 'metrics'


def test_upload_input_becomes_local_path(tmp_path):
    """上传参数默认换成本地路径；显式 file=false 才拿原始引用。"""
    digest = 'a' * 64
    (tmp_path / 'aa').mkdir()
    (tmp_path / 'aa' / digest).write_text('x', encoding='utf-8')

    runtime = SlotRuntime(str(tmp_path), lambda *args: None, str(tmp_path))
    runtime.inputs = {'doc': {'hash': digest, 'name': 'a.txt'}}
    resolved = runtime.resolve({'source': 'input', 'name': 'doc'})
    assert resolved.endswith(digest)
    raw = runtime.resolve({'source': 'input', 'name': 'doc', 'file': False})
    assert raw == {'hash': digest, 'name': 'a.txt'}


def test_upload_input_becomes_local_path(tmp_path):
    """上传参数默认换成本地路径；显式 file=false 才拿原始引用。"""
    digest = 'a' * 64
    (tmp_path / 'aa').mkdir()
    (tmp_path / 'aa' / digest).write_text('x', encoding='utf-8')

    runtime = SlotRuntime(str(tmp_path), lambda *args: None, str(tmp_path))
    runtime.inputs = {'doc': {'hash': digest, 'name': 'a.txt'}}
    resolved = runtime.resolve({'source': 'input', 'name': 'doc'})
    assert resolved.endswith(digest)
    raw = runtime.resolve({'source': 'input', 'name': 'doc', 'file': False})
    assert raw == {'hash': digest, 'name': 'a.txt'}


def test_slot_error_names_slot(tmp_path):
    """报错带上槽位名：展示和判定同节点时，光看函数名分不清。"""

    def invoke(project, request):
        raise RuntimeError('参数解析失败')

    runtime = SlotRuntime(str(tmp_path), invoke)
    step = {'slots': {'present': {'project': 'p', 'interface': 'View.metrics',
                                  'function': 'metrics', 'arguments': {}, 'view': 'metrics'}}}
    with pytest.raises(ValueError) as info:
        runtime.run(step)
    assert '展示槽 View.metrics 调用失败' in str(info.value)


def start_doc(slots):
    return {"nodes": [{"id": "s1", "kind": "start", "slots": slots}]}


def messages(doc):
    return " | ".join(validate(doc))


def test_only_fetch_accepts_cache_switch():
    doc = start_doc({"fetch": {**CALL, "useCache": False}})
    assert messages(doc) == ""

    doc = start_doc({"fetch": dict(CALL), "process": {**CALL, "useCache": False}})
    assert "只有拉取槽会缓存" in messages(doc)


def test_cache_switch_must_be_boolean():
    doc = start_doc({"fetch": {**CALL, "useCache": "yes"}})
    assert "使用缓存开关必须为布尔值" in messages(doc)

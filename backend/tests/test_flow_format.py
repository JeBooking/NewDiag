"""分层格式的读写边界：编辑器不认识的键不能在往返里丢掉。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.flow_format import compact, expand


def test_compact_and_expand_keep_unknown_fields():
    """顶层与节点上的未知键都要透传，否则给 doc 加新块后，编辑保存一次就丢了。"""
    doc = {
        'format': 1,
        'name': '流程',
        'nodes': [
            {'id': 's0', 'kind': 'start', 'title': '开始', 'detail': '',
             'position': {'x': 0, 'y': 0}, 'futureNodeField': {'a': 1}},
            {'id': 'n1', 'kind': 'conclusion', 'title': '结论', 'detail': '说明',
             'position': {'x': 200, 'y': 100}},
        ],
        'edges': [{'id': 'e0', 'from': 's0', 'to': 'n1'}],
        'futureTopLevel': {'enabled': True},
    }

    stored = compact(doc)
    assert stored['format'] == 2
    assert stored['flow']['futureTopLevel'] == {'enabled': True}

    restored = expand(stored)
    assert restored['futureTopLevel'] == {'enabled': True}
    assert restored['nodes'][0]['futureNodeField'] == {'a': 1}
    assert restored['nodes'][0]['position'] == {'x': 0, 'y': 0}


def test_expand_rejects_old_flat_format():
    with pytest.raises(ValueError, match='不支持的流程格式'):
        expand({'format': 1, 'name': '旧', 'nodes': [], 'edges': []})

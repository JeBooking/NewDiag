"""给存量流程补开始节点：检出 → 插入 start → 迁移 snapshotFields → 提交。

不改历史版本，每个流程产生一个新 revision。可以重复执行，已有 start 的流程跳过。

用法：
    backend/.venv/Scripts/python scripts/migrate_start_node.py
    NEWDIAG_BASE=http://127.0.0.1:8000/api ...（可选）
"""

from __future__ import annotations

import os

import httpx

BASE = os.environ.get('NEWDIAG_BASE', 'http://127.0.0.1:8000/api')
START_ID = 'start'


def build_inputs(doc: dict) -> list[dict]:
    """旧 snapshotFields → 全局参数；没有就留空。"""
    fields = doc.get('snapshotFields')
    if not isinstance(fields, list):
        return []
    seen: set[str] = set()
    inputs: list[dict] = []
    for item in fields:
        name = str(item or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        inputs.append({'name': name, 'scope': 'global', 'type': 'text'})
    return inputs


def migrate_doc(doc: dict) -> dict | None:
    nodes = doc.get('nodes') or []
    if any(node.get('kind') == 'start' for node in nodes if isinstance(node, dict)):
        return None

    edges = list(doc.get('edges') or [])
    ids = {str(node.get('id')) for node in nodes if isinstance(node, dict)}
    starts_at = {str(edge.get('to')) for edge in edges if isinstance(edge, dict)}
    roots = [nid for nid in ids if nid and nid not in starts_at]

    xs = [node.get('position', {}).get('x', 0) for node in nodes if isinstance(node, dict)]
    start_x = (min(xs) - 280) if xs else 40
    start_node = {
        'id': START_ID,
        'kind': 'start',
        'title': '开始',
        'detail': '',
        'inputs': build_inputs(doc),
        'position': {'x': start_x, 'y': 40},
    }

    new_edges = [
        {'id': f'e-{START_ID}-{index}', 'from': START_ID, 'to': nid, 'order': index}
        for index, nid in enumerate(sorted(roots), start=1)
    ]

    next_doc = {**doc, 'nodes': [start_node, *nodes], 'edges': [*edges, *new_edges]}
    next_doc.pop('snapshotFields', None)
    return next_doc


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=30) as client:
        flows = client.get('/flows').json()
        changed = 0
        for summary in flows:
            flow_id = summary['id']
            detail = client.get(f'/flows/{flow_id}').json()
            doc = detail.get('doc') or {}
            next_doc = migrate_doc(doc)
            if next_doc is None:
                print(f'跳过 {detail.get("name")}（已有开始节点）')
                continue

            checkout = client.post(f'/flows/{flow_id}/checkout')
            if checkout.status_code == 409:
                print(f'跳过 {detail.get("name")}：{checkout.json().get("detail")}')
                continue
            checkout.raise_for_status()

            response = client.post(f'/flows/{flow_id}/commit', json={'doc': next_doc})
            if response.status_code != 200:
                print(f'失败 {detail.get("name")}：{response.text[:200]}')
                continue
            issues = response.json().get('issues') or []
            errors = [item for item in issues if item.get('level') == 'error']
            changed += 1
            suffix = f'，{len(errors)} 个待处理问题' if errors else ''
            print(f'已升级 {detail.get("name")}：补 {len(next_doc["nodes"]) - len(doc.get("nodes") or [])} 个节点、'
                  f'{len(next_doc["edges"]) - len(doc.get("edges") or [])} 条边{suffix}')

        print(f'完成，升级 {changed} 个流程')


if __name__ == '__main__':
    main()

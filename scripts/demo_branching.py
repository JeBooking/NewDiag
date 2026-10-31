"""分支与回退演示：超温走处置，未超温上升；处置待办上回退改选「否」。

前提：后端跑在 http://127.0.0.1:8000，且已跑过 demo_seed.py（工程已登记）。
"""

from __future__ import annotations

import time

import httpx

BASE = 'http://127.0.0.1:8000/api'

DOC = {
    'format': 1,
    'name': '冷却系统诊断（分支）',
    'mode': 'parallel',
    'snapshotFields': ['temperature'],
    'nodes': [
        {
            'id': 'n1',
            'kind': 'investigation',
            'title': '检查是否超温',
            'detail': '判断设备温度是否超过 70°C',
            'fields': {
                'observation': '设备温度',
                'acquisition': '启动快照 temperature',
                'assessment': '超过 70°C 为超温',
            },
            'binding': {'project': '冷却系统', 'function': 'is_overheated'},
            'params': {'temperature': {'snapshot': 'temperature'}},
            'position': {'x': 120, 'y': 160},
        },
        {
            'id': 'n2',
            'kind': 'conclusion',
            'title': '冷却能力不足',
            'detail': '设备超温，冷却能力不足',
            'position': {'x': 420, 'y': 80},
        },
        {
            'id': 'n3',
            'kind': 'remediation',
            'title': '检查风路与风机',
            'detail': '检查滤网与风机，确认冷却回路',
            'position': {'x': 700, 'y': 80},
        },
        {
            'id': 'n4',
            'kind': 'problemSolved',
            'title': '问题解决',
            'detail': '温度恢复',
            'position': {'x': 980, 'y': 80},
        },
        {
            'id': 'n5',
            'kind': 'escalation',
            'title': '转交热管理工程师',
            'detail': '温度未超温但仍有异常，交人工排查',
            'position': {'x': 420, 'y': 260},
        },
    ],
    'edges': [
        {'id': 'e1', 'from': 'n1', 'to': 'n2', 'branch': 'yes'},
        {'id': 'e2', 'from': 'n2', 'to': 'n3'},
        {'id': 'e3', 'from': 'n3', 'to': 'n4'},
        {'id': 'e4', 'from': 'n1', 'to': 'n5', 'branch': 'no'},
    ],
}


def call(method: str, path: str, **kwargs):
    response = httpx.request(method, BASE + path, timeout=30, **kwargs)
    if response.status_code >= 400:
        raise SystemExit(f'{method} {path} -> {response.status_code}: {response.text}')
    return response.json()


def wait(instance_id: str, statuses: tuple[str, ...], timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = call('GET', f'/instances/{instance_id}')
        if detail['status'] in statuses:
            return detail
        time.sleep(0.3)
    raise SystemExit(f'等待 {statuses} 超时：{detail["status"]}')


def main():
    flow = None
    for item in call('GET', '/flows'):
        if item['name'] == DOC['name']:
            flow = item
    if flow is None:
        flow = call('POST', '/flows', json={'name': DOC['name']})

    call('POST', f'/flows/{flow["id"]}/checkout')
    call('PUT', f'/flows/{flow["id"]}/working', json={'doc': DOC})
    call('POST', f'/flows/{flow["id"]}/commit', json={'doc': DOC})
    release = call('POST', f'/flows/{flow["id"]}/releases', json={'note': '分支演示'})

    # 场景一：超温 → 处置待办 → 回退改选「否」→ 上升
    instance = call(
        'POST', f'/flows/{flow["id"]}/instances',
        json={'releaseId': release['id'], 'input': {'temperature': 85.0}},
    )
    detail = wait(instance['id'], ('waiting',))
    review = next(r for r in detail['reviews'] if r['status'] == 'pending')
    print(f"停在 {review['request']['title']}（{review['kind']}）")

    call('POST', f'/instances/{instance["id"]}/rollback',
         json={'targetNodeId': 'n1', 'branch': 'no', 'reason': '复测温度正常，改走上升'})
    detail = wait(instance['id'], ('escalated', 'success', 'failed'))
    abandoned = [r for r in detail['runs'] if r['status'] == 'abandoned']
    print(f"回退后状态：{detail['status']}，废弃记录 {len(abandoned)} 条，新代 token "
          f"{max((t['generation'] for t in detail['tokens']), default=0)}")

    # 场景二：未超温 → 直接上升
    instance2 = call(
        'POST', f'/flows/{flow["id"]}/instances',
        json={'releaseId': release['id'], 'input': {'temperature': 42.0}},
    )
    detail2 = wait(instance2['id'], ('escalated', 'success', 'failed'))
    print(f"正常温度场景状态：{detail2['status']}")

    assert detail['status'] == 'escalated', detail['status']
    assert detail2['status'] == 'escalated', detail2['status']
    print('分支、回退、上升全部符合预期')


if __name__ == '__main__':
    main()

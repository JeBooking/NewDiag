"""分支与回退演示：是分支走处置，否分支上升；处置待办上回退改选「否」。

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
    'snapshotFields': ['voltage'],
    'nodes': [
        {
            'id': 'n1',
            'kind': 'investigation',
            'title': '检查母线电压',
            'detail': '判断母线电压是否低于 210V',
            'fields': {
                'observation': '母线电压',
                'acquisition': '数据服务器 voltage',
                'assessment': '低于 210V 为异常',
            },
            'binding': {'project': '冷却系统', 'function': 'judge_voltage'},
            'position': {'x': 120, 'y': 160},
        },
        {
            'id': 'n2',
            'kind': 'conclusion',
            'title': '电压不足',
            'detail': '电源模块输出不足',
            'position': {'x': 420, 'y': 80},
        },
        {
            'id': 'n3',
            'kind': 'remediation',
            'title': '更换电源模块',
            'detail': '断电更换 24V 电源模块后上电复查',
            'position': {'x': 700, 'y': 80},
        },
        {
            'id': 'n4',
            'kind': 'problemSolved',
            'title': '问题解决',
            'detail': '电压恢复',
            'position': {'x': 980, 'y': 80},
        },
        {
            'id': 'n5',
            'kind': 'escalation',
            'title': '转交电气工程师',
            'detail': '电压正常但仍有异常，交人工排查',
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

    # 场景一：电压异常 → 处置待办 → 回退改选「否」→ 上升
    instance = call(
        'POST', f'/flows/{flow["id"]}/instances',
        json={'releaseId': release['id'], 'input': {'voltage': 198.4}},
    )
    detail = wait(instance['id'], ('waiting',))
    review = next(r for r in detail['reviews'] if r['status'] == 'pending')
    print(f"停在 {review['request']['title']}（{review['kind']}）")

    call('POST', f'/instances/{instance["id"]}/rollback',
         json={'targetNodeId': 'n1', 'branch': 'no', 'reason': '复测电压正常，改走上升'})
    detail = wait(instance['id'], ('escalated', 'success', 'failed'))
    abandoned = [r for r in detail['runs'] if r['status'] == 'abandoned']
    print(f"回退后状态：{detail['status']}，废弃记录 {len(abandoned)} 条，新代 token "
          f"{max((t['generation'] for t in detail['tokens']), default=0)}")

    # 场景二：电压正常 → 直接上升
    instance2 = call(
        'POST', f'/flows/{flow["id"]}/instances',
        json={'releaseId': release['id'], 'input': {'voltage': 230.0}},
    )
    detail2 = wait(instance2['id'], ('escalated', 'success', 'failed'))
    print(f"正常电压场景状态：{detail2['status']}")

    assert detail['status'] == 'escalated', detail['status']
    assert detail2['status'] == 'escalated', detail2['status']
    print('分支、回退、上升全部符合预期')


if __name__ == '__main__':
    main()

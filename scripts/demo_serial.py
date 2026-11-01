"""串行排查演示：超温后同组两条思路按 order 依次尝试，成功后暂停并询问是否继续。

前提：后端跑在 http://127.0.0.1:8000，且已跑过 demo_seed.py（工程已登记）。
"""

from __future__ import annotations

import time

import httpx

BASE = 'http://127.0.0.1:8000/api'


def conclusion(node_id: str, title: str, x: int, y: int):
    return {
        'id': node_id,
        'kind': 'conclusion',
        'title': title,
        'detail': f'{title}的处理方案',
        'position': {'x': x, 'y': y},
    }


def remediation(node_id: str, title: str, x: int, y: int):
    return {
        'id': node_id,
        'kind': 'remediation',
        'title': title,
        'detail': f'执行：{title}',
        'position': {'x': x, 'y': y},
    }


DOC = {
    'format': 1,
    'name': '冷却系统诊断（串行）',
    'mode': 'serial',
    'nodes': [
        {
            'id': 'start',
            'kind': 'start',
            'title': '开始',
            'detail': '',
            'inputs': [{'name': 'temperature', 'scope': 'global', 'type': 'number'}],
            'position': {'x': 60, 'y': 220},
        },
        {
            'id': 'n1',
            'kind': 'investigation',
            'title': '检查是否超温',
            'detail': '超过 70°C 为超温',
            'fields': {
                'observation': '设备温度',
                'acquisition': '启动快照 temperature',
                'assessment': '超过 70°C 为超温',
            },
            'binding': {'project': '冷却系统', 'function': 'is_overheated'},
            'params': {'temperature': {'snapshot': 'temperature'}},
            'position': {'x': 120, 'y': 200},
        },
        conclusion('n2', '滤网可能堵塞', 400, 100),
        remediation('n3', '清理滤网', 660, 100),
        conclusion('n6', '风机可能故障', 400, 320),
        remediation('n7', '重启风机', 660, 320),
        {
            'id': 'n4',
            'kind': 'problemSolved',
            'title': '问题解决',
            'detail': '温度恢复',
            'position': {'x': 940, 'y': 200},
        },
    ],
    'edges': [
        {'id': 'e0', 'from': 'start', 'to': 'n1', 'order': 1},
        {'id': 'e1', 'from': 'n1', 'to': 'n2', 'branch': 'yes', 'order': 1},
        {'id': 'e2', 'from': 'n2', 'to': 'n3'},
        {'id': 'e3', 'from': 'n3', 'to': 'n4'},
        {'id': 'e4', 'from': 'n1', 'to': 'n6', 'branch': 'yes', 'order': 2},
        {'id': 'e5', 'from': 'n6', 'to': 'n7'},
        {'id': 'e6', 'from': 'n7', 'to': 'n4'},
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


def pending(detail: dict) -> dict:
    return next(r for r in detail['reviews'] if r['status'] == 'pending')


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
    release = call('POST', f'/flows/{flow["id"]}/releases', json={'note': '串行演示'})

    instance = call(
        'POST', f'/flows/{flow["id"]}/instances',
        json={'releaseId': release['id'], 'input': {'temperature': 85.0}},
    )

    detail = wait(instance['id'], ('waiting',))
    first = pending(detail)
    print(f"第一条思路停在 {first['request']['title']}")
    assert first['nodeId'] == 'n3', first['nodeId']
    call('POST', f"/instances/{instance['id']}/reviews/{first['id']}", json={'result': 'done'})

    detail = wait(instance['id'], ('paused', 'success', 'failed'))
    assert detail['status'] == 'paused', detail['status']
    gate = pending(detail)
    print(f"成功后暂停：{gate['request'].get('type')} / {gate['request']['title']}")
    call('POST', f"/instances/{instance['id']}/reviews/{gate['id']}", json={'result': 'continue'})

    detail = wait(instance['id'], ('waiting',))
    second = pending(detail)
    print(f"继续后停在 {second['request']['title']}")
    assert second['nodeId'] == 'n7', second['nodeId']
    call('POST', f"/instances/{instance['id']}/reviews/{second['id']}", json={'result': 'done'})

    detail = wait(instance['id'], ('success', 'failed', 'escalated'))
    print(f"最终状态：{detail['status']}，待办 {len(detail['reviews'])} 个")
    assert detail['status'] == 'success'
    print('串行队列、成功后暂停、继续、收敛全部符合预期')


if __name__ == '__main__':
    main()

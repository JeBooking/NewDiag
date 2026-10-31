"""串行排查演示：同组两条思路按 order 依次尝试，成功后暂停并询问是否继续。

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
    'snapshotFields': ['voltage'],
    'nodes': [
        {
            'id': 'n1',
            'kind': 'investigation',
            'title': '检查母线电压',
            'detail': '低于 210V 为异常',
            'fields': {
                'observation': '母线电压',
                'acquisition': '数据服务器 voltage',
                'assessment': '低于 210V 为异常',
            },
            'binding': {'project': '冷却系统', 'function': 'judge_voltage'},
            'position': {'x': 120, 'y': 200},
        },
        conclusion('n2', '更换电源模块', 400, 100),
        remediation('n3', '断开负载后更换电源模块', 660, 100),
        conclusion('n6', '检查供电线路', 400, 320),
        remediation('n7', '紧固配电端子并复测', 660, 320),
        {
            'id': 'n4',
            'kind': 'problemSolved',
            'title': '问题解决',
            'detail': '电压恢复',
            'position': {'x': 940, 'y': 200},
        },
    ],
    'edges': [
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
        json={'releaseId': release['id'], 'input': {'voltage': 198.4}},
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

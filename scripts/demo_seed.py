"""端到端演示脚本：登记工程 → 建流程 → 保存 → 发布 → 执行 → 处置 → 收敛。

前提：后端跑在 http://127.0.0.1:8000。
用法：backend/.venv/Scripts/python scripts/demo_seed.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

BASE = 'http://127.0.0.1:8000/api'
ROOT = Path(__file__).resolve().parents[1]

DOC = {
    'format': 1,
    'name': '冷却系统诊断',
    'mode': 'parallel',
    'snapshotFields': ['voltage'],
    'nodes': [
        {
            'id': 'n1',
            'kind': 'investigation',
            'title': '检查母线电压',
            'detail': '读取母线电压，判断是否低于 210V',
            'fields': {
                'observation': '母线电压',
                'acquisition': '调用数据服务器读取 voltage',
                'assessment': '低于 210V 判为异常',
            },
            'binding': {'project': '冷却系统', 'function': 'judge_voltage'},
            'position': {'x': 120, 'y': 160},
        },
        {
            'id': 'n2',
            'kind': 'conclusion',
            'title': '电压不足',
            'detail': '母线电压低于 210V，电源模块输出能力不足',
            'position': {'x': 420, 'y': 160},
        },
        {
            'id': 'n3',
            'kind': 'remediation',
            'title': '更换电源模块',
            'detail': '断电后更换 24V 电源模块，上电复查电压',
            'position': {'x': 700, 'y': 160},
        },
        {
            'id': 'n4',
            'kind': 'problemSolved',
            'title': '问题解决',
            'detail': '电压恢复正常',
            'position': {'x': 980, 'y': 160},
        },
    ],
    'edges': [
        {'id': 'e1', 'from': 'n1', 'to': 'n2', 'branch': 'yes'},
        {'id': 'e2', 'from': 'n2', 'to': 'n3'},
        {'id': 'e3', 'from': 'n3', 'to': 'n4'},
    ],
}


def call(method: str, path: str, **kwargs):
    response = httpx.request(method, BASE + path, timeout=30, **kwargs)
    if response.status_code >= 400:
        raise SystemExit(f'{method} {path} -> {response.status_code}: {response.text}')
    return response.json()


def wait_health(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(BASE + '/health', timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit('后端没有就绪，先运行 uvicorn app.main:app --port 8000')


def ensure_project():
    projects = call('GET', '/projects')
    for item in projects:
        if item['name'] == '冷却系统':
            call('POST', f"/projects/{item['id']}/refresh")
            return item
    return call(
        'POST',
        '/projects',
        json={
            'name': '冷却系统',
            'path': str((ROOT / 'examples' / 'cooling').resolve()),
            'module': 'cooling.diagnosis',
        },
    )


def ensure_flow():
    for item in call('GET', '/flows'):
        if item['name'] == DOC['name']:
            return item
    return call('POST', '/flows', json={'name': DOC['name']})


def publish(flow_id: str):
    call('POST', f'/flows/{flow_id}/checkout')
    call('PUT', f'/flows/{flow_id}/working', json={'doc': DOC})
    call('POST', f'/flows/{flow_id}/commit', json={'doc': DOC})
    return call('POST', f'/flows/{flow_id}/releases', json={'note': '演示版本'})


def run_instance(flow_id: str, release_id: str):
    return call(
        'POST',
        f'/flows/{flow_id}/instances',
        json={'releaseId': release_id, 'input': {'voltage': 198.4}},
    )


def wait_review(instance_id: str, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = call('GET', f'/instances/{instance_id}')
        pending = [r for r in detail['reviews'] if r['status'] == 'pending']
        if pending:
            return detail, pending[0]
        if detail['status'] in ('success', 'failed', 'escalated'):
            raise SystemExit(f"实例提前结束：{detail['status']}")
        time.sleep(0.3)
    raise SystemExit('等待人工待办超时')


def main():
    wait_health()
    project = ensure_project()
    flow = ensure_flow()
    release = publish(flow['id'])
    instance = run_instance(flow['id'], release['id'])

    detail, review = wait_review(instance['id'])
    print(f"实例 {instance['id']} 停在 {review['request']['title']}（{review['kind']}）")

    call(
        'POST',
        f"/instances/{instance['id']}/reviews/{review['id']}",
        json={'result': 'done'},
    )

    deadline = time.time() + 20
    while time.time() < deadline:
        detail = call('GET', f'/instances/{instance["id"]}')
        if detail['status'] in ('success', 'failed', 'escalated'):
            break
        time.sleep(0.3)

    print(f"最终状态：{detail['status']}")
    print(f"工程：{project['name']}  版本：v{release['versionNo']}  记录数：{len(detail['runs'])}")
    if detail['status'] != 'success':
        sys.exit(1)


if __name__ == '__main__':
    main()

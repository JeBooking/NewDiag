"""端到端演示：登记工程 → 导入流程文档 → 发布 → 执行 → 处置 → 收敛。

验证「通用导入」链路：examples/cooling/workflow.json 只是一份普通流程文档，
经 /flows/import 导入成流程；工程登记是独立步骤，两者都是平台通用能力。

前提：后端跑在 http://127.0.0.1:8000。
用法：backend/.venv/Scripts/python scripts/demo_seed.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get('NEWDIAG_BASE', 'http://127.0.0.1:8000/api')
ROOT = Path(__file__).resolve().parents[1]

# 风机故障场景：温度 85°C 超温，风量 1.0 正常、转速 300 偏低
SCENARIO_FAN = {'temperature': 85.0, 'airflow': 1.0, 'fan_rpm': 300}


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
    print(f"登记工程：{project['name']}")

    doc = json.loads((ROOT / 'examples' / 'cooling' / 'workflow.json').read_text(encoding='utf-8'))
    flow = call('POST', '/flows/import', json={'doc': doc})
    print(f"导入流程：{flow['name']}（{flow['id']}）")

    release = call('POST', f"/flows/{flow['id']}/releases", json={'note': '演示版本'})
    print(f"发布版本：v{release['versionNo']}")

    instance = call(
        'POST',
        f"/flows/{flow['id']}/instances",
        json={'releaseId': release['id'], 'input': SCENARIO_FAN},
    )
    print('风机故障场景：温度 85°C 超温，风量 1.0 正常、转速 300 偏低')

    detail, review = wait_review(instance['id'])
    print(f"实例 {instance['id']} 停在 {review['request']['title']}（{review['kind']}）")

    call('POST', f"/instances/{instance['id']}/reviews/{review['id']}", json={'result': 'done'})

    deadline = time.time() + 20
    while time.time() < deadline:
        detail = call('GET', f'/instances/{instance["id"]}')
        if detail['status'] in ('success', 'failed', 'escalated'):
            break
        time.sleep(0.3)

    print(f"最终状态：{detail['status']}，记录数 {len(detail['runs'])}")
    if detail['status'] != 'success':
        sys.exit(1)


if __name__ == '__main__':
    main()

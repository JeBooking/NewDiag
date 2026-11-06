"""端到端演示：三个现成库零改动接入，装配成一条诊断流程。

三个工程各司其职，平台只按配方调用它们，不需要为它们写适配类或粘合代码：
    取数服务   MockDataService    dataclient.DataClient.fetch            取一段数据落成文件
    绘图引擎   PlotEngine         dataset.Dataset / plot_window.save_plot  装配并出图
    判断引擎   JudgeEngine        judgekit.JudgeEngine / judge_file       规则判定

前提：后端跑在 http://127.0.0.1:8000。
用法：
    backend/.venv/Scripts/python scripts/demo_library.py
    backend/.venv/Scripts/python scripts/demo_library.py --device D002 --activity 启停

三个工程的路径默认指向开发机上的位置，可用环境变量覆盖：
    NEWDIAG_MOCK / NEWDIAG_PLOT / NEWDIAG_JUDGE / NEWDIAG_PLOT_PYTHON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get('NEWDIAG_BASE', 'http://127.0.0.1:8000/api')
ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / 'examples' / 'library-flow' / 'workflow.json'

DEFAULT_PROJECTS = [
    {
        'name': '取数服务',
        'path': os.environ.get('NEWDIAG_MOCK', r'D:\FILE\Projects\MockDataService'),
        'module': 'dataclient',
        'factory': 'DataClient',
        'python': '',
    },
    {
        'name': '绘图引擎',
        'path': os.environ.get('NEWDIAG_PLOT', r'D:\FILE\Projects\PlotEngine'),
        'module': 'dataset',
        'factory': '',
        # 绘图工程自带 matplotlib，用它自己的解释器；平台解释器通常没装
        'python': os.environ.get(
            'NEWDIAG_PLOT_PYTHON', r'D:\FILE\Projects\PlotEngine\.venv\Scripts\python.exe'
        ),
    },
    {
        'name': '判断引擎',
        'path': os.environ.get('NEWDIAG_JUDGE', r'D:\FILE\Projects\JudgeEngine'),
        'module': 'judgekit',
        'factory': '',
        'python': '',
    },
]


def call(method: str, path: str, **kwargs):
    response = httpx.request(method, BASE + path, timeout=60, **kwargs)
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


def ensure_project(spec: dict) -> dict:
    """登记工程；已存在则把路径与解释器对齐到本次演示，接口在运行时发现。"""
    if not Path(spec['path']).is_dir():
        raise SystemExit(f"工程路径不存在：{spec['path']}（可用环境变量覆盖）")
    for item in call('GET', '/projects'):
        if item['name'] != spec['name']:
            continue
        call('PATCH', f"/projects/{item['id']}", json={
            'path': spec['path'], 'module': spec['module'],
            'python': spec['python'], 'factory': spec['factory'],
        })
        call('POST', f"/projects/{item['id']}/refresh")
        return {**item, **spec}
    return call('POST', '/projects', json=spec)


def print_wiring(doc: dict):
    print('装配关系（画布节点 → 被调用的现成库）：')
    for node in doc['nodes']:
        recipe = node.get('recipe')
        if not recipe:
            continue
        calls = []
        for step in recipe['steps']:
            calls.append(('  on %s.' % step['on'] if step.get('on') else '  ') + step['call'])
        print(f"  {node['id']:>4} {node['title']}  <-  {recipe['project']}")
        for line in calls:
            print(f"        {line.strip()}")


def blocks_of(run: dict) -> list[str]:
    evidence = run.get('evidence') or {}
    blocks = ((evidence.get('presentation') or {}).get('blocks') or [])
    return [block['type'] for block in blocks if isinstance(block, dict)]


def wait_review(instance_id: str, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = call('GET', f'/instances/{instance_id}')
        pending = [r for r in detail['reviews'] if r['status'] == 'pending']
        if pending:
            return detail, pending[0]
        if detail['status'] in ('success', 'failed', 'escalated'):
            return detail, None
        time.sleep(0.4)
    raise SystemExit('等待人工待办超时')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', default='D001')
    parser.add_argument('--activity', default='满载')
    parser.add_argument('--metric', default='vibration')
    parser.add_argument('--peak', default='12')
    args = parser.parse_args()

    wait_health()
    for spec in DEFAULT_PROJECTS:
        project = ensure_project(spec)
        print(f"登记工程：{project['name']}  {project['path']}")

    doc = json.loads(FLOW.read_text(encoding='utf-8'))
    print_wiring(doc)

    flow = call('POST', '/flows/import', json={'doc': doc})
    print(f"导入流程：{flow['name']}（{flow['id']}）")

    release = call('POST', f"/flows/{flow['id']}/releases", json={'note': '三库装配演示'})
    print(f"发布版本：v{release['versionNo']}")

    instance = call('POST', f"/flows/{flow['id']}/instances", json={
        'releaseId': release['id'],
        'input': {
            'device': args.device,
            'activity': args.activity,
            'start_time': '2026-11-05 08:00',
            'end_time': '2026-11-05 10:00',
            'metric': args.metric,
            'peak_limit': args.peak,
        },
    })
    print(f"启动实例：{instance['id']}")

    detail, review = wait_review(instance['id'])
    for run in detail['runs']:
        if run['status'] != 'ok':
            continue
        output = run.get('output') or {}
        summary = {key: output[key] for key in ('overall', 'result', 'reason') if key in output}
        print(f"  节点 {run['nodeId']}: {', '.join(blocks_of(run)) or '无展示'}  {summary or ''}")

    if review is None:
        print(f"实例提前结束：{detail['status']}")
        return 1 if detail['status'] != 'success' else 0

    print(f"停在待办：{review['request']['title']}（{review['kind']}）")
    blocks = ((review['request'].get('presentation') or {}).get('blocks') or [])
    print(f"  待办展示：{[block['type'] for block in blocks]}")
    call('POST', f"/instances/{instance['id']}/reviews/{review['id']}", json={'result': 'done'})

    deadline = time.time() + 30
    while time.time() < deadline:
        detail = call('GET', f"/instances/{instance['id']}")
        if detail['status'] in ('success', 'failed', 'escalated'):
            break
        time.sleep(0.4)

    print(f"最终状态：{detail['status']}，记录数 {len(detail['runs'])}")
    return 0 if detail['status'] == 'success' else 1


if __name__ == '__main__':
    sys.exit(main())

"""用真实平台 API 生成示例导出包；只需在维护平台时运行一次。"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    # 隔离数据库与运行目录，生成演示包不读写使用者的平台实例。
    workspace = ROOT / 'backend' / '.pytest_cache' / f'export-demo-{uuid.uuid4().hex}'
    workspace.mkdir(parents=True)
    os.environ['NEWDIAG_DATA_DIR'] = str(workspace / 'data')
    os.environ['NEWDIAG_DB'] = str(workspace / 'data' / 'newdiag.db')
    from fastapi.testclient import TestClient
    from app.main import app

    output = HERE / 'generated'
    output.mkdir(exist_ok=True)
    with TestClient(app) as client:
        registered = client.post('/api/projects', json={
            'name': '代码导出演示', 'path': str(HERE / 'project'), 'module': 'business', 'python': sys.executable,
        })
        registered.raise_for_status()
        imported = client.post('/api/flows/import', json={
            'doc': json.loads((HERE / 'workflow.json').read_text(encoding='utf-8')),
        })
        imported.raise_for_status()
        flow = imported.json()['id']
        released = client.post(f'/api/flows/{flow}/releases', json={})
        released.raise_for_status()
        started = client.post(f'/api/flows/{flow}/instances', json={
            'releaseId': released.json()['id'], 'input': {},
        })
        started.raise_for_status()
        instance = started.json()['id']
        try:
            deadline = time.monotonic() + 15
            while True:
                detail = client.get(f'/api/instances/{instance}')
                detail.raise_for_status()
                status = detail.json()['status']
                if status == 'escalated':
                    break
                if status in ('failed', 'waiting', 'terminated') or time.monotonic() > deadline:
                    raise RuntimeError(f'自动示例没有正常结束：{detail.text}')
                time.sleep(0.1)
            response = client.get(f'/api/instances/{instance}/code-export')
            response.raise_for_status()
            destination = output / 'standalone-code.zip'
            destination.write_bytes(response.content)
        finally:
            detail = client.get(f'/api/instances/{instance}')
            if detail.json()['status'] in ('running', 'waiting', 'paused'):
                client.post(f'/api/instances/{instance}/terminate').raise_for_status()
    print(destination)


if __name__ == '__main__':
    main()

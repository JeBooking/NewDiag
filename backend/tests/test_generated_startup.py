"""用真实热重载服务覆盖生成文件与后端生命周期之间的边界。"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.worker.processes import process_options, stop_tree


def wait_until(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("等待服务状态超时")


@contextmanager
def live_backend(tmp_path):
    backend = tmp_path / "backend"
    shutil.copytree(ROOT / "backend" / "app", backend / "app", ignore=shutil.ignore_patterns("__pycache__"))
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    log = tmp_path / "server.log"
    module = (ROOT / "scripts" / "backend-command.mjs").as_uri()
    command = subprocess.run([
        "node", "--input-type=module", "-e",
        f"import {{ backendArgs }} from {json.dumps(module)}; "
        "process.stdout.write(JSON.stringify(backendArgs(process.argv[1], Number(process.argv[2]))))",
        str(backend), str(port),
    ], check=True, capture_output=True, text=True, encoding="utf-8")
    args = json.loads(command.stdout)
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen([sys.executable, *args], cwd=backend,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   env={**os.environ, "NEWDIAG_DATA_DIR": str(backend / "data"),
                                        "NEWDIAG_DB": str(backend / "data" / "newdiag.db"),
                                        "PYTHONIOENCODING": "utf-8"}, **process_options())
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
                wait_until(lambda: client.get("/api/health").status_code == 200)
                yield client, log
        finally:
            stop_tree(process)
            process.wait(timeout=15)


def test_generated_files_do_not_restart_development_backend(tmp_path):
    example = ROOT / "examples" / "diagnosis-instance"
    with live_backend(tmp_path) as (client, log):
        response = client.post("/api/projects", json={
            "name": "诊断实例示例", "path": str(example), "module": "diagnosis", "python": sys.executable,
        })
        response.raise_for_status()
        document = json.loads((example / "workflow.json").read_text(encoding="utf-8"))
        flow = client.post("/api/flows/import", json={"doc": document})
        flow.raise_for_status()
        flow_id = flow.json()["id"]
        release = client.post(f"/api/flows/{flow_id}/releases", json={})
        release.raise_for_status()
        started = client.post(f"/api/flows/{flow_id}/instances", json={
            "releaseId": release.json()["id"], "input": {"global": {
                "device_id": "motor-01", "time_point": "2026-11-05T08:00:00+08:00",
            }},
        })
        started.raise_for_status()
        instance = started.json()["id"]
        wait_until(lambda: client.get(f"/api/instances/{instance}").json().get("status") == "waiting")
        # 热重载器有 debounce，须越过该窗口再检查，不能只看启动请求返回成功。
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            assert client.get("/api/health").status_code == 200
            time.sleep(0.1)
        text = log.read_text(encoding="utf-8")
        assert text.count("Started server process") == 1, text
        assert "WatchFiles detected changes" not in text, text
        detail = client.get(f"/api/instances/{instance}").json()
        review = next(item for item in detail["reviews"] if item["status"] == "pending")
        accepted = client.post(f"/api/instances/{instance}/reviews/{review['id']}", json={"result": "done"})
        assert accepted.status_code == 200, accepted.text


def test_snapshot_probe_failure_does_not_stop_backend(tmp_path):
    example = tmp_path / "example"
    shutil.copytree(ROOT / "examples" / "diagnosis-instance", example,
                    ignore=shutil.ignore_patterns("__pycache__"))
    source = example / "diagnosis.py"
    with source.open("a", encoding="utf-8") as output:
        output.write('\nif "executions" in Path(__file__).parts:\n    raise RuntimeError("snapshot-probe-failure")\n')
    with live_backend(tmp_path) as (client, log):
        client.post("/api/projects", json={
            "name": "诊断实例示例", "path": str(example), "module": "diagnosis", "python": sys.executable,
        }).raise_for_status()
        flow = client.post("/api/flows/import", json={
            "doc": json.loads((example / "workflow.json").read_text(encoding="utf-8")),
        })
        flow.raise_for_status()
        flow_id = flow.json()["id"]
        release = client.post(f"/api/flows/{flow_id}/releases", json={})
        release.raise_for_status()
        failed = client.post(f"/api/flows/{flow_id}/instances", json={
            "input": {"device_id": "motor-01", "time_point": "2026-11-05T08:00:00+08:00"},
        })
        assert failed.status_code == 500
        assert "snapshot-probe-failure" in failed.json()["detail"]
        assert "副本目录=" in failed.json()["detail"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            assert client.get("/api/health").status_code == 200
            time.sleep(0.1)
        assert log.read_text(encoding="utf-8").count("Started server process") == 1
        instances = client.get("/api/instances", params={"flowId": flow_id}).json()
        assert len(instances) == 1 and instances[0]["status"] == "failed"

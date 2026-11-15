"""用真实开发服务覆盖生成文件、源码重启与后端生命周期之间的边界。"""

import json
import os
import re
import shutil
import signal
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
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False) as client:
                wait_until(lambda: client.get("/api/health").status_code == 200)
                yield client, log
        finally:
            stop_tree(process)
            process.wait(timeout=15)


def register_composed_projects(client, example: Path) -> None:
    for name, folder, module in (
        ("组合数据示例", "data_tools", "records"),
        ("组合判断示例", "analysis_tools", "checks"),
    ):
        response = client.post("/api/projects", json={
            "name": name, "path": str(example / folder), "module": module, "python": sys.executable,
        })
        response.raise_for_status()


def composed_input() -> dict:
    return {"global": {"samples": [1, 2, 8, 3], "limit": 5}}


def test_generated_files_do_not_restart_development_backend(tmp_path):
    example = ROOT / "examples" / "composed-basic"
    with live_backend(tmp_path) as (client, log):
        register_composed_projects(client, example)
        document = json.loads((example / "workflow.json").read_text(encoding="utf-8"))
        flow = client.post("/api/flows/import", json={"doc": document})
        flow.raise_for_status()
        flow_id = flow.json()["id"]
        release = client.post(f"/api/flows/{flow_id}/releases", json={})
        release.raise_for_status()
        started = client.post(f"/api/flows/{flow_id}/instances", json={
            "releaseId": release.json()["id"], "input": composed_input(),
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
    shutil.copytree(ROOT / "examples" / "composed-basic", example,
                    ignore=shutil.ignore_patterns("__pycache__"))
    source = example / "data_tools" / "records.py"
    with source.open("a", encoding="utf-8") as output:
        output.write(
            '\nfrom pathlib import Path as _Path\n'
            'if "executions" in _Path(__file__).parts:\n'
            '    raise RuntimeError("snapshot-probe-failure")\n'
        )
    with live_backend(tmp_path) as (client, log):
        register_composed_projects(client, example)
        flow = client.post("/api/flows/import", json={
            "doc": json.loads((example / "workflow.json").read_text(encoding="utf-8")),
        })
        flow.raise_for_status()
        flow_id = flow.json()["id"]
        release = client.post(f"/api/flows/{flow_id}/releases", json={})
        release.raise_for_status()
        failed = client.post(f"/api/flows/{flow_id}/instances", json={"input": composed_input()})
        assert failed.status_code == 500, failed.text
        assert "snapshot-probe-failure" in failed.json()["detail"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            assert client.get("/api/health").status_code == 200
            time.sleep(0.1)
        assert log.read_text(encoding="utf-8").count("Started server process") == 1
        instances = client.get("/api/instances", params={"flowId": flow_id}).json()
        assert len(instances) == 1 and instances[0]["status"] == "failed"


@contextmanager
def development_stack(tmp_path):
    backend = tmp_path / "backend"
    shutil.copytree(ROOT / "backend" / "app", backend / "app", ignore=shutil.ignore_patterns("__pycache__"))
    scripts = tmp_path / "scripts"
    shutil.copytree(ROOT / "scripts", scripts)
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    # 仅替换测试资源位置和端口，完整执行实际开发启动器及其健康监控。
    entry = scripts / "dev-all.mjs"
    source = entry.read_text(encoding="utf-8")
    first = source.index("const python =")
    last = source.index("const viteBin =", first)
    source = source[:first] + f"const python = {json.dumps(sys.executable)}\n" + source[last:]
    source = source.replace("const BACKEND_PORT = 8000", f"const BACKEND_PORT = {port}")
    source = source.replace("const WEB_PORT = 5173", "const WEB_PORT = 0")
    entry.write_text(source, encoding="utf-8")
    vite = tmp_path / "node_modules" / "vite" / "bin" / "vite.js"
    vite.parent.mkdir(parents=True)
    vite.write_text("setInterval(() => {}, 1000)\n", encoding="utf-8")
    log = tmp_path / "dev.log"
    options = {"start_new_session": True}
    if os.name == "nt":
        # 模拟终端里的 pnpm dev，但另建隐藏控制台，避免重载信号影响测试进程。
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options = {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startup}
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(["node", str(entry)], cwd=tmp_path,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   env={**os.environ, "NEWDIAG_DATA_DIR": str(backend / "data"),
                                        "NEWDIAG_DB": str(backend / "data" / "newdiag.db"),
                                        "PYTHONIOENCODING": "utf-8"}, **options)
        try:
            # 本机常驻 HTTP_PROXY，NO_PROXY 对回环端口不生效，会被代理打回 502
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False) as client:
                wait_until(lambda: client.get("/api/health").status_code == 200)
                wait_until(lambda: "后端已就绪" in log.read_text(encoding="utf-8"))
                yield client, log, process, backend
        finally:
            stop_tree(process)
            process.wait(timeout=15)


def test_development_reload_recovers_with_open_event_stream(tmp_path):
    with development_stack(tmp_path) as (client, log, process, backend):
        example = ROOT / "examples" / "composed-basic"
        register_composed_projects(client, example)
        flow = client.post("/api/flows/import", json={
            "doc": json.loads((example / "workflow.json").read_text(encoding="utf-8")),
        }).json()
        client.post(f"/api/flows/{flow['id']}/releases", json={}).raise_for_status()
        started = client.post(f"/api/flows/{flow['id']}/instances", json={"input": composed_input()})
        started.raise_for_status()
        instance = started.json()["id"]
        wait_until(lambda: client.get(f"/api/instances/{instance}").json().get("status") == "waiting")
        with client.stream("GET", f"/api/instances/{instance}/events") as events:
            events.raise_for_status()
            assert next(events.iter_lines()).startswith("data:")
            time.sleep(1)
            assert log.read_text(encoding="utf-8").count("Started server process") == 1
            (backend / "app" / "reload_test_marker.py").write_text("# reload test\n", encoding="utf-8")
            def recovered():
                text = log.read_text(encoding="utf-8")
                assert process.poll() is None, text
                return (text.count("Started server process") >= 2
                        and client.get("/api/health").status_code == 200)
            try:
                wait_until(recovered, timeout=25)
            except AssertionError:
                raise AssertionError(log.read_text(encoding="utf-8")) from None


def test_development_backend_exit_is_restarted(tmp_path):
    with development_stack(tmp_path) as (client, log, process, _backend):
        text = log.read_text(encoding="utf-8")
        backend_pid = int(re.findall(r"Started server process \[(\d+)\]", text)[-1])
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(backend_pid), "/T", "/F"],
                           capture_output=True, check=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.kill(backend_pid, signal.SIGKILL)
        def recovered():
            text = log.read_text(encoding="utf-8")
            assert process.poll() is None, text
            return (text.count("Started server process") >= 2
                    and client.get("/api/health").status_code == 200)
        wait_until(recovered)

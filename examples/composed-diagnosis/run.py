"""组合诊断示例：注册三个既有工程 → 导入流程 → 发布 → 跑一次实例。

用法（在任意目录执行都行）：

    python examples/composed-diagnosis/run.py
    python examples/composed-diagnosis/run.py --device D002 --activity 空载

脚本只使用平台公开的 HTTP 接口，业务侧三个工程不需要改一行代码。默认把数据库和
产物写到临时目录，跑完不污染 backend/data；加 --keep 可以看到产物路径。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DEFAULT_PROJECTS = {
    "MockDataService": Path(r"D:\FILE\Projects\MockDataService"),
    "PlotEngine": Path(r"D:\FILE\Projects\PlotEngine"),
    "JudgeEngine": Path(r"D:\FILE\Projects\JudgeEngine"),
}
MODULES = {"MockDataService": "dataclient", "PlotEngine": "dataset", "JudgeEngine": "judgekit"}


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="newdiag-"))
    os.environ["NEWDIAG_DATA_DIR"] = str(data_dir)
    os.environ.pop("NEWDIAG_DB", None)

    from fastapi.testclient import TestClient

    from app import db

    db.configure()
    db.init_db()

    from app.main import app

    with TestClient(app) as client:
        projects = register(client, args)
        flow = import_flow(client, manual=args.manual)
        release = publish(client, flow["id"])
        instance = start(client, flow["id"], release["id"], args)
        detail = wait(client, instance["id"], args.timeout, answer=args.answer)
        report(detail, data_dir)
        ok = detail["status"] in ("success", "escalated")
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)
        else:
            print(f"\n产物目录：{data_dir}")
        return 0 if ok else 1


def parse_args():
    parser = argparse.ArgumentParser(description="三工程组合诊断示例")
    parser.add_argument("--mock-data", default=str(DEFAULT_PROJECTS["MockDataService"]))
    parser.add_argument("--plot", default=str(DEFAULT_PROJECTS["PlotEngine"]))
    parser.add_argument("--judge", default=str(DEFAULT_PROJECTS["JudgeEngine"]))
    parser.add_argument("--plot-python", default="", help="PlotEngine 的解释器，缺省用其 .venv")
    parser.add_argument("--device", default="D001")
    parser.add_argument("--activity", default="满载")
    parser.add_argument("--start-time", default="2026-11-05 08:00")
    parser.add_argument("--end-time", default="2026-11-05 10:00")
    parser.add_argument("--peak", type=float, default=12.0)
    parser.add_argument("--mean", type=float, default=40.0)
    parser.add_argument("--trend", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--data-dir", default="", help="数据库与产物目录，缺省用临时目录")
    parser.add_argument("--keep", action="store_true", help="保留临时目录并打印路径")
    parser.add_argument("--manual", action="store_true",
                        help="删掉操作的自动判断，让节点只取数绘图后等人看图判断")
    parser.add_argument("--answer", choices=["yes", "no"], default="",
                        help="配合 --manual：脚本代人选是/否；不填则停在人工待办")
    return parser.parse_args()


def register(client, args) -> dict:
    """登记三个工程；同名工程已存在时改成当前路径，脚本可重复运行。"""
    paths = {
        "MockDataService": args.mock_data,
        "PlotEngine": args.plot,
        "JudgeEngine": args.judge,
    }
    plot_python = args.plot_python or str(Path(args.plot) / ".venv" / "Scripts" / "python.exe")
    existing = {item["name"]: item for item in client.get("/api/projects").json()}
    result = {}
    for name, path in paths.items():
        payload = {"name": name, "path": path, "module": MODULES[name], "python": "", "factory": ""}
        if name == "PlotEngine" and Path(plot_python).is_file():
            payload["python"] = plot_python
        if name in existing:
            response = client.patch(f"/api/projects/{existing[name]['id']}", json={
                "path": payload["path"], "module": payload["module"], "python": payload["python"],
            })
            response.raise_for_status()
            result[name] = response.json()
        else:
            response = client.post("/api/projects", json=payload)
            response.raise_for_status()
            result[name] = response.json()
        print(f"工程 {name}: {payload['path']}（模块 {payload['module']}，解释器 {payload['python'] or '平台自身'}）")
    return result


def import_flow(client, manual: bool = False) -> dict:
    doc = json.loads((HERE / "workflow.json").read_text(encoding="utf-8"))
    if manual:
        for operation in (doc.get("composition") or {}).get("operations", {}).values():
            operation.pop("result", None)
    response = client.post("/api/flows/import", json={"doc": doc})
    response.raise_for_status()
    flow = response.json()
    print(f"流程已导入：{flow['name']}（{flow['id']}，{len(doc['nodes'])} 个节点）")
    return flow


def publish(client, flow_id: str) -> dict:
    response = client.post(f"/api/flows/{flow_id}/releases", json={"note": "组合诊断示例"})
    if response.status_code != 200:
        raise SystemExit(f"发布失败：{response.status_code}\n{_pretty(response)}")
    release = response.json()
    detail = client.get(f"/api/releases/{release['id']}").json()
    calls = []
    composition = detail["plan"].get("composition") or {}
    for obj in (composition.get("objects") or {}).values():
        calls.append(f"{obj['project']}:{obj['module']}:{obj['entry']}")
    for group in list((composition.get("resources") or {}).values()) + \
            list((composition.get("operations") or {}).values()):
        for step in group.get("steps") or []:
            calls.append(f"{step['project']}:{step['module']}:{step.get('interface') or step['function']}")
    print(f"已发布 v{release['versionNo']}：{len(calls)} 个接口调用，启动参数 "
          f"{[item['name'] for item in detail['inputs']]}")
    for call in sorted(set(calls)):
        print(f"  - {call}")
    return release


def start(client, flow_id: str, release_id: str, args) -> dict:
    payload = {
        "releaseId": release_id,
        "input": {
            "global": {
                "device": args.device,
                "activity": args.activity,
                "start_time": args.start_time,
                "end_time": args.end_time,
                "peak": args.peak,
                "mean": args.mean,
                "trend": args.trend,
            }
        },
    }
    response = client.post(f"/api/flows/{flow_id}/instances", json=payload)
    if response.status_code != 200:
        raise SystemExit(f"启动失败：{response.status_code}\n{_pretty(response)}")
    instance = response.json()
    print(f"\n实例已启动：{instance['id']}（{args.device}/{args.activity}）")
    return instance


def wait(client, instance_id: str, timeout: float, answer: str = "") -> dict:
    """推进实例；人工动作由脚本代填，判断待办看 --answer。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/instances/{instance_id}").json()
        if detail["status"] in ("success", "failed", "escalated", "terminated"):
            return detail
        pending = [item for item in detail.get("reviews") or [] if item["status"] == "pending"]
        if not pending:
            time.sleep(0.5)
            continue
        decision = [item for item in pending if item["kind"] == "decision"]
        if decision and not answer:
            return detail
        for review in pending:
            payload = {"result": "done", "reason": "示例脚本代为完成"}
            if review["kind"] == "decision":
                payload = {"result": answer, "reason": f"示例脚本代为判断：{answer}"}
            response = client.post(
                f"/api/instances/{instance_id}/reviews/{review['id']}", json=payload,
            )
            response.raise_for_status()
            print(f"  （示例脚本处理人工待办：{review['request']['title']} → "
                  f"{payload['result']}）")
    raise SystemExit("等待实例结束超时")


def report(detail: dict, data_dir: Path) -> None:
    print(f"\n实例状态：{detail['status']}")
    for run in detail.get("runs") or []:
        if run["status"] != "ok":
            print(f"  [{run['status']}] {run['nodeId']}: {run.get('error')}")
            continue
        output = run.get("output") or {}
        evidence = run.get("evidence") or {}
        if isinstance(output, dict) and "result" in output:
            print(f"  {run['nodeId']}: {output['result']} — {output.get('reason')}")
        elif isinstance(output, dict) and "resources" in output:
            steps = ", ".join(f"{key}({len(value)} 步)" for key, value in output["resources"].items())
            print(f"  {run['nodeId']}: 启动资源 {steps}")
        else:
            print(f"  {run['nodeId']}: {run['status']}")
        for step in (evidence.get("composition") or {}).get("steps") or []:
            print(f"      - {step['id']}: {step['output']}"
                  + (f" {step.get('file') or step.get('object') or step.get('image') or ''}".rstrip()))
    files = _generated_files(data_dir)
    images = [item for item in files if item[0] == "png"]
    if images:
        print(f"\n生成的图片 {len(images)} 个：")
        for _, path, size in images:
            print(f"  {path}（{size} 字节）")
    if files:
        print(f"共落盘 {len(files)} 个内容寻址文件（含取数 JSON 与图片）")
    pending = [review for review in detail.get("reviews") or [] if review["status"] == "pending"]
    if pending:
        print(f"\n等待人工处理 {len(pending)} 项")


def _generated_files(data_dir: Path):
    root = data_dir / "files"
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.glob("*/*")):
        if not path.is_file():
            continue
        try:
            head = path.open("rb").read(8)
        except OSError:
            continue
        kind = "png" if head.startswith(b"\x89PNG") else "data"
        result.append((kind, path, path.stat().st_size))
    return result


def _pretty(response) -> str:
    try:
        return json.dumps(response.json(), ensure_ascii=False, indent=2)
    except ValueError:
        return response.text


if __name__ == "__main__":
    raise SystemExit(main())

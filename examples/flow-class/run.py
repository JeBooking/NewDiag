#!/usr/bin/env python
"""三工程流程类示例：一条命令走完 登记 → 发布 → 执行 → 结论。

用法（在仓库根目录）：
    backend/.venv/Scripts/python.exe examples/flow-class/run.py
    backend/.venv/Scripts/python.exe examples/flow-class/run.py --device D002 --activity 空载

前提：三个工程位于 D:\\FILE\\Projects；路径不同就用 --projects 指定父目录。
默认写平台自己的库（backend/data），跑完切到界面就能看到这条流程；
想隔离就用 --data-dir 指定一个目录。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


from app import db, models  # noqa: E402
from app.api.releases import freeze_inputs  # noqa: E402
from app.services import signatures  # noqa: E402
from app.services.compiler import compile_doc  # noqa: E402
from app.services.scheduler import Scheduler  # noqa: E402
from app.services.sessions import SessionManager  # noqa: E402

FINAL = {"success", "failed", "escalated", "terminated"}


def project_specs(base: Path) -> list[dict]:
    plot_python = base / "PlotEngine" / ".venv" / "Scripts" / "python.exe"
    return [
        {"name": "MockDataService", "path": base / "MockDataService",
         "module": "dataclient", "python": None},
        {"name": "PlotEngine", "path": base / "PlotEngine",
         "module": "dataset", "python": str(plot_python) if plot_python.exists() else None},
        {"name": "JudgeEngine", "path": base / "JudgeEngine",
         "module": "judgekit", "python": None},
    ]


def register(session, specs: list[dict]) -> None:
    for spec in specs:
        if not spec["path"].is_dir():
            raise SystemExit(f"工程目录不存在：{spec['path']}，用 --projects 指定父目录")
        row = session.query(models.Project).filter_by(name=spec["name"]).first()
        if row is None:
            row = models.Project(id=models.new_id(), name=spec["name"])
            session.add(row)
        row.path = str(spec["path"])
        row.module = spec["module"]
        row.python = spec["python"]
    session.commit()


def seed(session, doc: dict, plan: dict, probed: dict, inputs: dict, name: str) -> str:
    flow = models.Flow(id=models.new_id(), name=name, description="流程类示例",
                       mode=doc.get("mode") or "serial", owner_id="example")
    revision = models.Revision(id=models.new_id(), flow_id=flow.id,
                               json=json.dumps(doc, ensure_ascii=False),
                               status="committed", author="example")
    release = models.Release(id=models.new_id(), flow_id=flow.id, revision_id=revision.id,
                             version_no=1, plan_json=json.dumps(plan, ensure_ascii=False),
                             signature_json="{}",
                             inputs_json=json.dumps(freeze_inputs(doc, probed), ensure_ascii=False),
                             published_by="example")
    instance = models.Instance(id=models.new_id(), flow_id=flow.id, release_id=release.id,
                               status="running", snapshot_id=None, started_by="example")
    snapshot = models.Snapshot(id=models.new_id(), instance_id=instance.id, seq=1,
                               payload_json=json.dumps({"global": inputs}, ensure_ascii=False),
                               params_json="{}")
    instance.snapshot_id = snapshot.id
    flow.current_revision_id = revision.id
    session.add_all([flow, revision, release, instance, snapshot])
    session.commit()
    return instance.id


def pending(session, instance_id: str) -> list[models.Review]:
    return list(session.query(models.Review).filter_by(instance_id=instance_id, status="pending"))


def describe(session, instance_id: str) -> list[str]:
    lines = []
    for run in session.query(models.Run).filter_by(instance_id=instance_id).order_by(models.Run.started_at):
        output = json.loads(run.output_json or "{}")
        result = output.get("result") if isinstance(output, dict) else None
        reason = output.get("reason") if isinstance(output, dict) else None
        detail = f"结论={result} 依据={reason}" if result is not None else ""
        lines.append(f"  {run.node_id:<12} {run.status:<7} {detail}".rstrip())
    return lines


def evidence(session, instance_id: str) -> dict:
    found = {"image": 0, "line": 0}
    for run in session.query(models.Run).filter_by(instance_id=instance_id):
        payload = json.loads(run.evidence_json or "{}")
        for block in ((payload.get("presentation") or {}).get("blocks") or []):
            if block.get("type") in found:
                found[block["type"]] += 1
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="三个工程拼成一个流程类")
    parser.add_argument("--projects", default=r"D:\FILE\Projects", help="三个工程所在父目录")
    parser.add_argument("--device", default="D001")
    parser.add_argument("--activity", default="满载")
    parser.add_argument("--start", default="2026-11-05 08:00")
    parser.add_argument("--end", default="2026-11-05 10:00")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--data-dir", default="", help="单独放演示数据；默认与平台共用一个库")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.data_dir:
        os.environ["NEWDIAG_DATA_DIR"] = args.data_dir

    db.configure()  # 与平台同一个库：命令行跑完，界面里就能看到这条流程
    db.init_db()
    doc = json.loads((Path(__file__).parent / "workflow.json").read_text(encoding="utf-8"))
    specs = project_specs(Path(args.projects))

    with db.SessionLocal() as session:
        register(session, specs)

    probed = signatures.probe_doc(doc)
    failures = {key: value for key, value in probed.items() if isinstance(value, dict)}
    if failures:
        for key, value in failures.items():
            print(f"发布前探测失败 {key}: {value.get('error')}")
        return 2
    plan = compile_doc(doc, "example-" + str(int(time.time())), 1, functions=probed)

    inputs = {"device": args.device, "activity": args.activity,
              "start_time": args.start, "end_time": args.end}
    with db.SessionLocal() as session:
        instance_id = seed(session, doc, plan, probed, inputs,
                           f"三工程流程类 {time.strftime('%H:%M:%S')}")

    sessions = SessionManager()
    scheduler = Scheduler(db.SessionLocal, sessions=sessions, timeout=60)
    scheduler.start(instance_id)
    print(f"实例 {instance_id} 已启动：{args.device}/{args.activity}")

    deadline = time.monotonic() + args.timeout
    handled: set[str] = set()
    status = "running"
    while time.monotonic() < deadline:
        with db.SessionLocal() as session:
            instance = session.get(models.Instance, instance_id)
            status = instance.status if instance else "missing"
            for review in pending(session, instance_id):
                if review.id in handled:
                    continue
                handled.add(review.id)
                request = json.loads(review.request_json or "{}")
                print(f"  人工处置：{request.get('title') or review.node_id}（脚本自动完成）")
                scheduler.submit_review(instance_id, review.id, {"result": "done"})
        if status in FINAL:
            break
        time.sleep(0.2)

    with db.SessionLocal() as session:
        print(f"\n实例状态：{status}")
        print("运行记录：")
        for line in describe(session, instance_id):
            print(line)
        blocks = evidence(session, instance_id)
        print(f"\n展示物：图 {blocks['image']} 张，曲线 {blocks['line']} 条")
        files = db.data_dir() / "files"
        sizes = sorted((item.stat().st_size for item in files.rglob("*") if item.is_file()),
                       reverse=True)[:3]
        print(f"附件目录：{files}（最大 {sizes[:3]} 字节）")

    source = plan["diagnosis"]["source"]
    generated = db.data_dir() / "generated_flow.py"
    generated.write_text(source, encoding="utf-8")
    print(f"\n平台生成的类（{generated}）：")
    print("\n".join(source.splitlines()[:24]))
    print("  ...")

    sessions.shutdown()
    db.engine.dispose()
    return 0 if status in ("success", "escalated") else 1


if __name__ == "__main__":
    sys.exit(main())

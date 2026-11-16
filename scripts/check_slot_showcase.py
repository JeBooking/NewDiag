"""用 slot-showcase 示例跑一遍生成代码：覆盖表格、曲线、图片、指标、现场参数与并行分支。

用法：backend/.venv/Scripts/python scripts/check_slot_showcase.py [--keep]
不依赖数据库，直接探测、编译、生成并在临时目录里以串行模式执行。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.services.codegen import generate  # noqa: E402
from app.services.compiler import compile_doc  # noqa: E402
from app.services.slots import probe_slots  # noqa: E402

EXAMPLE = ROOT / "examples" / "slot-showcase"
PROJECTS = {
    "示例数据源": ("data_source", "feeder"),
    "示例处理展示": ("analytics", "render"),
    "示例判断": ("judge", "rules"),
}
ANSWERS = ["no", "no", json.dumps({"channel": "vibration", "points": 10}), "yes", "done"]


def main() -> int:
    doc = json.loads((EXAMPLE / "workflow.json").read_text(encoding="utf-8"))
    specs = {
        name: SimpleNamespace(name=name, path=str(EXAMPLE / folder), module=module,
                              python=sys.executable, factory="")
        for name, (folder, module) in PROJECTS.items()
    }
    probed = probe_slots(doc, specs)
    failed = {key: value for key, value in probed.items() if isinstance(value, dict)}
    if failed:
        print("探测失败", failed)
        return 1
    plan = compile_doc(doc, "showcase", 1, functions=probed)

    root = Path(tempfile.mkdtemp(prefix="slot-showcase-"))
    projects = {}
    for name, (folder, module) in PROJECTS.items():
        key = f"p{len(projects) + 1}"
        shutil.copytree(EXAMPLE / folder, root / "modules" / key)
        projects[name] = {"name": name, "path": str(root / "modules" / key), "module": module,
                          "factory": "", "python": sys.executable}
    shutil.copytree(BACKEND / "app" / "worker", root / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (root / "flow.py").write_text(generate(plan), encoding="utf-8")
    configuration = {
        "projects": projects,
        "runtime": {"mode": "serial", "maxParallel": 4},
        "input": {"global": {"dataset": "vib-2026-05", "device": "M-1",
                             "points": 24, "threshold": 20}},
        "artifactRoot": str(root / "artifacts"),
        "fileRoot": str(root / "files"),
        "meta": {node["id"]: node for node in doc["nodes"]},
    }
    (root / "configuration.json").write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run([sys.executable, "flow.py", "--mode", "serial"], cwd=root,
                               input="\n".join(ANSWERS) + "\n", capture_output=True,
                               text=True, encoding="utf-8", timeout=180)
    print("exit", completed.returncode, "目录", root)
    if completed.returncode != 0:
        print((completed.stderr or "")[-3000:])
        return completed.returncode
    output = completed.stderr or ""
    missing = [kind for kind in ('table', 'line', 'image', 'metrics', 'text')
               if f'"type": "{kind}"' not in output]
    if missing:
        print("展示块缺失", missing)
        print(output[-3000:])
        return 1
    print("展示块齐全：table / line / image / metrics / text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

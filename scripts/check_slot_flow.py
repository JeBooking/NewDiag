"""跑一遍生成代码的最小端到端：构造 → 拉取 → 展示 → 判定。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.services.codegen import generate  # noqa: E402

MODULE = '''
class Presenter:
    def __init__(self, device):
        self.device = device
        self.samples = []

    def fetch(self, metric="vibration"):
        self.samples = [1, 5, 9, 3]
        return {"metric": metric, "count": len(self.samples)}

    def load(self, raw):
        return {"metric": raw["metric"], "series": self.samples}

    def plot(self):
        import node_sdk
        return node_sdk.save_file(PNG)

    def check(self, data):
        exceeded = max(data["series"]) > 5
        return {"result": exceeded, "reason": "峰值 %s 与阈值 5 比较" % max(data["series"])}


PNG = (
    b"\\x89PNG\\r\\n\\x1a\\n" + b"\\x00\\x00\\x00\\rIHDR" + b"\\x00" * 12 + b"\\x00" * 80
)
'''

PLAN = {
    "format": 1,
    "flowId": "f",
    "release": 1,
    "entry": "s1",
    "signature": {},
    "steps": {
        "s1": {
            "kind": "start",
            "next": {"default": ["n1"]},
            "slots": {
                "constructs": [{
                    "object": "presenter", "project": "演示", "module": "presenter",
                    "interface": "Presenter", "function": "Presenter",
                    "arguments": {"device": {"source": "input", "name": "device"}},
                }],
                "fetch": {
                    "object": "presenter", "project": "演示", "module": "presenter",
                    "interface": "Presenter.fetch", "function": "fetch",
                    "arguments": {"metric": {"lit": "vibration"}},
                },
            },
        },
        "n1": {
            "kind": "investigation",
            "next": {"yes": ["n2"], "no": ["n3"]},
            "slots": {
                "fetch": {
                    "object": "presenter", "project": "演示", "module": "presenter",
                    "interface": "Presenter.fetch", "function": "fetch",
                    "arguments": {"metric": {"lit": "vibration"}},
                },
                "process": {
                    "object": "presenter", "project": "演示", "module": "presenter",
                    "interface": "Presenter.load", "function": "load",
                    "arguments": {"raw": {"source": "previous"}},
                },
                "present": {
                    "object": "presenter", "project": "演示", "module": "presenter",
                    "interface": "Presenter.plot", "function": "plot", "view": "image",
                    "arguments": {},
                },
                "decide": {
                    "object": "presenter", "project": "演示", "module": "presenter",
                    "interface": "Presenter.check", "function": "check",
                    "arguments": {"data": {"source": "previous"}},
                },
            },
        },
        "n2": {"kind": "conclusion", "text": "峰值超限", "next": {"default": ["n4"]}},
        "n3": {"kind": "conclusion", "text": "正常", "next": {"default": ["n4"]}},
        "n4": {"kind": "problemSolved", "terminal": True},
    },
}


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="slot-smoke-"))
    (root / "modules" / "demo").mkdir(parents=True)
    (root / "modules" / "demo" / "presenter.py").write_text(MODULE, encoding="utf-8")
    shutil.copytree(BACKEND / "app" / "worker", root / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (root / "flow.py").write_text(generate(PLAN), encoding="utf-8")
    configuration = {
        "projects": {"演示": {"name": "演示", "path": str(root / "modules" / "demo"),
                             "module": "presenter", "factory": "", "python": sys.executable}},
        "runtime": {"mode": "serial", "maxParallel": 4},
        "input": {"global": {"device": "M-1"}},
        "artifactRoot": str(root / "artifacts"),
        "fileRoot": str(root / "files"),
        "meta": {"s1": {"title": "开始", "detail": ""}, "n1": {"title": "检查峰值", "detail": ""}},
    }
    (root / "configuration.json").write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run([sys.executable, "flow.py", "--mode", "serial"], cwd=root,
                               capture_output=True, text=True, encoding="utf-8", timeout=120)
    print("exit", completed.returncode)
    print((completed.stderr or "")[-2000:])
    cache = list((root / "artifacts").rglob("data.json"))
    print("artifacts", len(cache))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

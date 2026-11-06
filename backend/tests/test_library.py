"""能力配方（library/v1）：跨工程装配、展示物化与人工判断。

用一个临时的小库代替真实的取数/绘图/判断工程，只验证平台侧语义：
节点内对象不出进程、节点间只传 JSON、返回值变成展示块、人工待办能带上游证据。
"""

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import db, models
from app.services import signatures
from app.services.compiler import ValidationError, compile_doc, validate_doc
from app.services.scheduler import Scheduler
from app.services.sessions import SessionManager
from test_scheduler import pending_reviews, runs, seed, status_of, wait_for

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

FETCHER = """
import json
import pathlib


class Fetcher:
    def __init__(self, root=None):
        self.root = pathlib.Path(root) if root else pathlib.Path(__file__).parent / "store"

    def fetch(self, device, limit=3, filename=None):
        path = pathlib.Path(filename) if filename else self.root / (device + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        y = [1.0, 5.0, 14.2][:limit]
        path.write_text(json.dumps({"device": device, "y": y}), encoding="utf-8")
        return path
"""

ANALYZER = f"""
import json
import pathlib

PNG = {PNG!r}


class Report:
    def __init__(self, path):
        self.path = pathlib.Path(path)

    def series(self, metric="vibration"):
        y = json.loads(self.path.read_text(encoding="utf-8"))["y"]
        return {{"x": list(range(len(y))), "y": y, "label": metric, "unit": "mm/s"}}

    def summary(self, series, limit=10):
        peak = max(series["y"])
        overall = "yes" if peak > limit else "no"
        return {{"overall": overall, "reasons": [f"峰值 {{peak}} 与阈值 {{limit}}"], "peak": peak}}

    def draw(self, series, path):
        target = pathlib.Path(path)
        target.write_bytes(PNG)
        return target
"""


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWDIAG_DATA_DIR", str(tmp_path / "data"))
    db.configure(f"sqlite:///{(tmp_path / 'library.db').as_posix()}")
    db.init_db()

    fetcher = tmp_path / "fetcher"
    analyzer = tmp_path / "analyzer"
    fetcher.mkdir()
    analyzer.mkdir()
    (fetcher / "fetchlib.py").write_text(FETCHER, encoding="utf-8")
    (analyzer / "analyzelib.py").write_text(ANALYZER, encoding="utf-8")

    projects = {
        "取数": SimpleNamespace(path=str(fetcher), module="fetchlib", python=sys.executable,
                                factory="Fetcher"),
        "分析": SimpleNamespace(path=str(analyzer), module="analyzelib", python=sys.executable,
                                factory=""),
    }
    monkeypatch.setattr(signatures, "_load_project", lambda name: projects.get(name))

    with db.SessionLocal() as session:
        for name, project in projects.items():
            session.add(models.Project(id=name, name=name, path=project.path,
                                       module=project.module, python=project.python,
                                       factory=project.factory))
        session.commit()

    sessions = SessionManager()
    scheduler = Scheduler(db.SessionLocal, sessions=sessions, timeout=20)
    yield SimpleNamespace(factory=db.SessionLocal, tmp=tmp_path, scheduler=scheduler,
                          sessions=sessions, projects=projects)
    for thread in list(scheduler._threads.values()):
        thread.join(20)
    sessions.shutdown()
    db.engine.dispose()


def _start(inputs=None):
    return {
        "id": "start", "kind": "start", "title": "开始", "detail": "",
        "position": {"x": 0, "y": 0}, "inputs": inputs or [],
    }


def doc_with(result: dict | None = None, evidence: str | None = None) -> dict:
    recipe = {
        "protocol": "library/v1",
        "project": "分析",
        "steps": [
            {"call": "Report", "args": {"path": {"$run": "n1", "path": "output.data_file"}},
             "as": "report", "show": "none"},
            {"on": "report", "call": "series", "args": {"metric": {"$snapshot": "metric"}},
             "as": "series", "show": "line", "label": "曲线"},
            {"on": "report", "call": "summary", "args": {"series": {"$var": "series"}},
             "as": "summary", "show": "table", "label": "判定"},
            {"on": "report", "call": "draw", "args": {"series": {"$var": "series"},
                                                      "path": {"$file": "chart.png"}},
             "as": "chart", "show": "image", "label": "图表"},
        ],
        "out": {"overall": {"from": "summary", "path": "overall"}},
    }
    if result is not None:
        recipe["result"] = result
        recipe["reason"] = {"template": "峰值 {summary.peak}，判定 {summary.overall}"}
    if evidence:
        recipe["evidence"] = evidence
    return {
        "format": 1, "name": "装配流程", "mode": "parallel",
        "nodes": [
            _start([
                {"name": "device", "scope": "global", "type": "text", "default": "D001"},
                {"name": "metric", "scope": "global", "type": "text", "default": "vibration"},
            ]),
            {
                "id": "n1", "kind": "capability", "title": "取数", "detail": "取一段数据",
                "position": {"x": 0, "y": 0},
                "recipe": {
                    "protocol": "library/v1", "project": "取数",
                    "steps": [{
                        "call": "fetch",
                        "args": {"device": {"$snapshot": "device"}, "filename": {"$file": "data.json"}},
                        "as": "data_file", "show": "text",
                    }],
                    "out": {"data_file": {"from": "data_file"}},
                },
            },
            {
                "id": "n2", "kind": "investigation", "title": "判断", "detail": "看曲线判断",
                "fields": {"observation": "峰值", "acquisition": "读数据", "assessment": "超阈值"},
                "position": {"x": 0, "y": 0}, "recipe": recipe,
            },
            {"id": "c1", "kind": "conclusion", "title": "超限", "detail": "判定异常",
             "position": {"x": 0, "y": 0}},
            {"id": "r1", "kind": "remediation", "title": "处置", "detail": "停机检查",
             "position": {"x": 0, "y": 0}},
            {"id": "p1", "kind": "problemSolved", "title": "解决", "detail": "完成",
             "position": {"x": 0, "y": 0}},
            {"id": "e1", "kind": "escalation", "title": "上升", "detail": "转人工",
             "position": {"x": 0, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "from": "start", "to": "n1", "order": 1},
            {"id": "e2", "from": "n1", "to": "n2", "order": 1},
            {"id": "e3", "from": "n2", "to": "c1", "branch": "yes", "order": 1},
            {"id": "e4", "from": "n2", "to": "e1", "branch": "no", "order": 1},
            {"id": "e5", "from": "c1", "to": "r1", "order": 1},
            {"id": "e6", "from": "r1", "to": "p1", "order": 1},
        ],
    }


def launch(runtime, doc, snapshot=None):
    probed = signatures.probe_doc(doc)
    plan = seed(runtime.factory, doc, snapshot=snapshot or {"global": {
        "device": "D001", "metric": "vibration",
    }}, functions=probed)
    runtime.scheduler.start("i1")
    return plan, probed


# ---------------------------------------------------------------- 编译


def test_compile_rejects_broken_recipes(runtime):
    cases = [
        lambda doc: doc["nodes"][1]["recipe"].update(project=""),
        lambda doc: doc["nodes"][1]["recipe"]["steps"][0].update(call="fetch", args={"bad": 1}),
        lambda doc: doc["nodes"][1]["recipe"]["steps"][0]["args"].update(
            filename={"$file": "../evil.json"}),
        lambda doc: doc["nodes"][1]["recipe"]["steps"].append(
            {"call": "Report", "on": "missing", "args": {}}),
        lambda doc: doc["nodes"][1]["recipe"].update(
            result={"from": "summary", "path": "overall"}),
        lambda doc: doc["nodes"][2]["recipe"].update(evidence="n2"),
    ]
    for index, mutate in enumerate(cases):
        doc = doc_with(result={"from": "summary", "path": "overall"})
        mutate(doc)
        with pytest.raises(ValidationError):
            compile_doc(doc, "f1", 1, functions=signatures.probe_doc(doc))
        _ = index


def test_capability_requires_recipe(runtime):
    doc = doc_with(result={"from": "summary", "path": "overall"})
    doc["nodes"][1].pop("recipe")
    issues = validate_doc(doc)
    assert any("能力步骤必须配置能力配方" in issue["message"] for issue in issues)


# ---------------------------------------------------------------- 执行


def test_cross_project_chain_runs_and_branches(runtime):
    doc = doc_with(result={"from": "summary", "path": "overall"})
    launch(runtime, doc)
    wait_for(runtime.factory, lambda: any(r.node_id == "r1" for r in pending_reviews(runtime.factory)))
    assert status_of(runtime.factory) == "waiting"

    fetch_run = runs(runtime.factory, "n1")[0]
    assert fetch_run.status == "ok"
    assert json.loads(fetch_run.output_json)["data_file"].endswith("data.json")

    judge = runs(runtime.factory, "n2")[0]
    assert judge.status == "ok"
    assert json.loads(judge.output_json)["overall"] == "yes"
    assert "峰值 14.2" in json.loads(judge.output_json)["reason"]

    blocks = json.loads(judge.evidence_json)["presentation"]["blocks"]
    kinds = [block["type"] for block in blocks]
    assert "line" in kinds and "image" in kinds
    image = next(block for block in blocks if block["type"] == "image")
    with runtime.factory() as session:
        assert session.get(models.FileBlob, image["src"]) is not None

    action = next(r for r in pending_reviews(runtime.factory) if r.node_id == "r1")
    runtime.scheduler.submit_review("i1", action.id, {"result": "done"})
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "success")


def test_manual_judgment_carries_upstream_evidence(runtime):
    doc = doc_with(evidence="n1")
    # n2 没有 result：先自动算完，再停下来等人判断
    doc["nodes"][2]["recipe"].pop("result", None)
    doc["nodes"][2]["recipe"].pop("reason", None)
    launch(runtime, doc)

    wait_for(runtime.factory, lambda: any(r.node_id == "n2" for r in pending_reviews(runtime.factory)))
    review = next(r for r in pending_reviews(runtime.factory) if r.node_id == "n2")
    assert review.kind == "decision"
    blocks = json.loads(review.request_json)["presentation"]["blocks"]
    kinds = [block["type"] for block in blocks]
    assert kinds[0] == "text"  # 上游取数节点的展示被带进来
    assert "image" in kinds and "table" in kinds

    runtime.scheduler.submit_review("i1", review.id, {"result": "yes", "reason": "曲线峰值明显超限"})
    wait_for(runtime.factory, lambda: any(r.node_id == "r1" for r in pending_reviews(runtime.factory)))
    decided = runs(runtime.factory, "n2")[0]
    assert decided.status == "ok"
    assert json.loads(decided.evidence_json)["branch"] == "yes"
    assert json.loads(decided.output_json)["result"] == "yes"

    action = next(r for r in pending_reviews(runtime.factory) if r.node_id == "r1")
    runtime.scheduler.submit_review("i1", action.id, {"result": "done"})
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "success")


def test_reason_template_falls_back_when_value_is_empty():
    from app.worker import library_sdk

    ctx = library_sdk._Context(object(), {"project": "分析"})
    ctx.vars["summary"] = {"overall": "no", "reasons": []}
    built = library_sdk._reason(
        ctx, {"template": "判定 {summary.overall}；{summary.reasons.0|三条规则均未触发}"},
    )
    assert built == "判定 no；三条规则均未触发"
    missing = library_sdk._reason(ctx, {"template": "{summary.nope|没这个字段}"})
    assert missing == "没这个字段"

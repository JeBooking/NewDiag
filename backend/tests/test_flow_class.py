"""流程类：doc 声明 → 生成 Python 源码 → 子进程里按实例执行。"""

import base64
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import db, models
from app.services import flowclass, signatures
from app.services.compiler import ValidationError, compile_doc
from app.services.scheduler import Scheduler
from app.services.sessions import SessionManager
from test_scheduler import pending_reviews, runs, seed, status_of, wait_for

PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)

LIBRARY = '''
import base64

PNG = base64.b64decode({png!r})


class Source:
    def fetch(self, device, limit):
        return {{"device": device, "limit": limit, "values": [1.0, 3.0, 12.0]}}


class Data:
    def __init__(self, payload):
        self.payload = payload

    @classmethod
    def load(cls, path):
        return cls(path)

    def series(self, metric):
        values = self.payload["values"][: self.payload["limit"]]
        return {{"x": list(range(len(values))), "y": values,
                "label": metric + "曲线", "metric": metric}}


def save_plot(data, path, title):
    with open(path, "wb") as handle:
        handle.write(PNG)
    return path


class Judge:
    def __init__(self, thresholds):
        self.limit = thresholds["peak"]

    def check(self, series):
        peak = max(series["y"])
        return {{"hit": "yes" if peak > self.limit else "no",
                "reason": "峰值 {{}} 对阈值 {{}}".format(peak, self.limit)}}
'''.format(png=PNG)


def make_doc() -> dict:
    return {
        "format": 1,
        "name": "流程类示例",
        "mode": "serial",
        "flowClass": {
            "runtime": "",
            "imports": [{"alias": "fp", "project": "lib", "module": "flowproj"}],
            "initialize": [
                {"name": "source", "call": "fp.Source", "args": {}},
                {"name": "payload", "call": "source.fetch", "args": {
                    "device": {"input": "device"}, "limit": {"input": "limit"}}},
                {"name": "data", "call": "fp.Data.load", "args": {"path": {"var": "payload"}}},
            ],
            "methods": [{
                "name": "check_peak",
                "params": [{"name": "threshold", "type": "number", "default": 20.0}],
                "body": [
                    {"name": "series", "call": "data.series", "args": {"metric": {"lit": "vibration"}}},
                    {"name": "figure", "call": "fp.save_plot", "args": {
                        "data": {"var": "series"},
                        "path": {"file": "{threshold}.png"},
                        "title": {"lit": "示例"}}},
                    {"name": "judge", "call": "fp.Judge", "args": {
                        "thresholds": {"object": {"peak": {"param": "threshold"}}}}},
                    {"name": "verdict", "call": "judge.check", "args": {"series": {"var": "series"}}},
                ],
                "returns": {"result": "verdict.hit", "reason": "verdict.reason",
                            "figure": "figure", "series": "series"},
            }],
        },
        "nodes": [
            {"id": "start", "kind": "start", "title": "开始", "detail": "开始",
             "position": {"x": 0, "y": 0}, "inputs": [
                 {"name": "device", "scope": "global", "type": "text", "default": "D001"},
                 {"name": "limit", "scope": "global", "type": "number", "default": 3}]},
            {"id": "check", "kind": "investigation", "title": "峰值检查", "detail": "峰值检查",
             "position": {"x": 240, "y": 0}, "method": "check_peak",
             "fields": {"observation": "振动峰值", "acquisition": "库取数", "assessment": "峰值阈值"}},
            {"id": "ok", "kind": "conclusion", "title": "结论", "detail": "指标正常",
             "position": {"x": 480, "y": 0}},

            {"id": "bad", "kind": "escalation", "title": "异常", "detail": "异常",
             "position": {"x": 480, "y": 140}},
        ],
        "edges": [
            {"id": "e1", "from": "start", "to": "check"},
            {"id": "e2", "from": "check", "to": "bad", "branch": "yes"},
            {"id": "e3", "from": "check", "to": "ok", "branch": "no"},
            {"id": "e4", "from": "ok", "to": "bad"},
        ],
    }


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWDIAG_DATA_DIR", str(tmp_path / "data"))
    db.configure(f"sqlite:///{(tmp_path / 'diag.db').as_posix()}")
    db.init_db()
    (tmp_path / "flowproj.py").write_text(LIBRARY, encoding="utf-8")
    sessions = SessionManager()
    scheduler = Scheduler(db.SessionLocal, sessions=sessions, timeout=20)
    yield SimpleNamespace(factory=db.SessionLocal, tmp=tmp_path,
                          scheduler=scheduler, sessions=sessions)
    for thread in list(scheduler._threads.values()):
        thread.join(20)
    sessions.shutdown()
    db.engine.dispose()


def prepare(runtime, doc: dict | None = None, mutate=None):
    doc = copy.deepcopy(doc or make_doc())
    if mutate:
        mutate(doc)
    project = SimpleNamespace(path=str(runtime.tmp), module="flowproj",
                              python=sys.executable, factory="")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(signatures, "_load_project", lambda name: project)
    try:
        probed = signatures.probe_doc(doc)
        plan = seed(runtime.factory, doc, snapshot={"global": {"device": "D001", "limit": 3}},
                functions=probed)
    finally:
        monkey.undo()
    with runtime.factory() as session:
        registered = session.get(models.Project, "pr1")
        registered.name, registered.path = "lib", str(runtime.tmp)
        registered.module, registered.python = "flowproj", sys.executable
        session.commit()
    return doc, plan, probed


def test_generated_class_source_is_frozen_into_plan(runtime):
    _, plan, probed = prepare(runtime)
    diagnosis = plan["diagnosis"]
    assert diagnosis["flowClass"] is True
    assert diagnosis["projects"] == ["lib"]
    assert diagnosis["module"] == flowclass.module_name("f1")
    assert "class Flow(Diagnosis):" in diagnosis["source"]
    assert "self.data = fp.Data.load(path=self.payload)" in diagnosis["source"]
    assert "dataclient" not in diagnosis["source"]
    entries = signature_keys(probed)
    assert "@flow:Flow.__definition__" in entries
    assert "@flow:Flow.check_peak" in entries


def signature_keys(probed):
    return set(probed)


def test_run_success_attaches_figure_and_line(runtime):
    prepare(runtime)
    runtime.scheduler.start("i1")
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated", timeout=20)
    record = runs(runtime.factory, "check")[0]
    assert record.status == "ok"
    output = json.loads(record.output_json)
    assert json.loads(record.input_json) == {}
    assert output["result"] == "no"
    evidence = json.loads(record.evidence_json)
    blocks = evidence["presentation"]["blocks"]
    assert [block["type"] for block in blocks] == ["image", "line"]
    with runtime.factory() as session:
        assert session.query(models.FileBlob).count() == 1


def test_declaration_errors_are_reported_before_publish(runtime):
    doc = make_doc()
    doc["flowClass"]["initialize"][1]["call"] = "nobody.fetch"
    with pytest.raises(ValidationError) as first:
        compile_doc(doc, "f1", 1)
    assert "既不是导入别名" in str(first.value)

    doc = make_doc()
    doc["flowClass"]["methods"][0]["body"][0]["args"] = {"metric": {"var": "missing"}}
    with pytest.raises(ValidationError) as second:
        compile_doc(doc, "f1", 1)
    assert "不存在的变量" in str(second.value)

    doc = make_doc()
    doc["flowClass"]["methods"][0]["name"] = "initialize"
    with pytest.raises(ValidationError) as third:
        compile_doc(doc, "f1", 1)
    assert "保留字" in str(third.value)


def test_missing_runtime_dependency_blocks_publish(runtime):
    doc = make_doc()
    doc["flowClass"]["imports"].append(
        {"alias": "ghost", "project": "lib", "module": "no_such_module_here"})
    monkey = pytest.MonkeyPatch()
    project = SimpleNamespace(path=str(runtime.tmp), module="flowproj",
                              python=sys.executable, factory="")
    monkey.setattr(signatures, "_load_project", lambda name: project)
    try:
        probed = signatures.probe_doc(doc)
    finally:
        monkey.undo()
    error = probed[f"{flowclass.PROJECT}:{flowclass.CLASS}.__definition__"]
    assert "no_such_module_here" in error["error"]


def test_method_without_result_waits_for_human(runtime):
    def mutate(doc):
        doc["flowClass"]["methods"][0]["returns"] = {"reason": "verdict.reason"}

    prepare(runtime, mutate=mutate)
    runtime.scheduler.start("i1")
    wait_for(runtime.factory, lambda: bool(pending_reviews(runtime.factory)), timeout=20)
    task = pending_reviews(runtime.factory)[0]
    assert task.node_id == "check"
    runtime.scheduler.submit_review("i1", task.id, {"result": "yes", "reason": "人工判异常"})
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated", timeout=20)

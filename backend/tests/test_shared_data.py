"""共享数据的真实进程调用、字段读取和回退版本隔离。"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import db, models
from app.services.compiler import ValidationError, compile_doc
from app.services.scheduler import Scheduler
from app.services.sessions import SessionManager
from app.worker import artifacts
from test_scheduler import seed, pending_reviews, runs, status_of, token_of, wait_for


def binding(function):
    return {"project": "demo", "function": function}


def document(mode="parallel", manual=False):
    nodes = [{"id": "start", "kind": "start", "title": "开始", "detail": "", "position": {"x": 0, "y": 0}}]
    nodes.append({"id": "prepare", "kind": "dataPreparation", "title": "解析报告", "detail": "共享报告",
                  "position": {"x": 200, "y": 0}, "pipeline": {
                      "acquire": binding("fetch"), "parse": binding("parse"), "manualFields": []}})
    for number, field in enumerate(("voltage", "current")):
        pipeline = {"dataSource": {"node": "prepare"}, "manualFields": [],
                    "present": {**binding("present"), "arguments": {"value": {"source": "parsed", "path": field}}}}
        if not manual:
            pipeline["judge"] = {**binding("judge"), "arguments": {"value": {"source": "parsed", "path": field}}}
        nodes.append({"id": field, "kind": "investigation", "title": field, "detail": "检查字段",
                      "fields": {"observation": field, "acquisition": "共享报告", "assessment": "阈值"},
                      "position": {"x": 400, "y": number * 160}, "pipeline": pipeline})
    nodes.append({"id": "end", "kind": "escalation", "title": "结束", "detail": "排查完成", "position": {"x": 600, "y": 0}})
    edges = [{"id": "entry", "from": "start", "to": "prepare"}]
    for field in ("voltage", "current"):
        edges.extend([{"id": "p-" + field, "from": "prepare", "to": field},
                      {"id": "y-" + field, "from": field, "to": "end", "branch": "yes"},
                      {"id": "n-" + field, "from": field, "to": "end", "branch": "no"}])
    return {"format": 1, "name": "共享报告", "mode": mode, "nodes": nodes, "edges": edges}


def param(name, kind="any", required=True, default=None):
    return {"name": name, "type": kind, "required": required, "default": default}


FUNCTIONS = {"demo:fetch": [], "demo:parse": [param("raw")],
             "demo:present": [param("value", "float"), param("decision", required=False)],
             "demo:judge": [param("value", "float")]}


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWDIAG_DATA_DIR", str(tmp_path / "data"))
    db.configure(f"sqlite:///{(tmp_path / 'diag.db').as_posix()}")
    db.init_db()
    module = tmp_path / "shared_module.py"
    module.write_text(
        "from pathlib import Path\nimport json\n"
        "def record(stage):\n    with Path('calls.txt').open('a') as f: f.write(stage + '\\n')\n"
        "def fetch():\n    record('acquire')\n    return {'voltage': 205, 'current': 12, 'large': 'x' * 100000}\n"
        "def parse(raw):\n    record('parse')\n    return raw\n"
        "def present(value: float, decision=None):\n    record('present')\n    return str(value)\n"
        "def judge(value: float):\n    record('judge')\n    return value > 0\n", encoding="utf-8")
    sessions = SessionManager()
    scheduler = Scheduler(db.SessionLocal, sessions=sessions, timeout=10)
    value = SimpleNamespace(factory=db.SessionLocal, scheduler=scheduler, tmp=tmp_path, sessions=sessions)
    yield value
    for thread in list(scheduler._threads.values()):
        thread.join(10)
    sessions.close("i1")
    db.engine.dispose()


def launch(runtime, doc):
    plan = seed(runtime.factory, doc, functions=FUNCTIONS)
    with runtime.factory() as session:
        project = session.get(models.Project, "pr1")
        project.path, project.module = str(runtime.tmp), "shared_module"
        session.commit()
    runtime.scheduler.start("i1")
    return plan


def calls(runtime, stage):
    return (runtime.tmp / "calls.txt").read_text().splitlines().count(stage)


@pytest.mark.parametrize("mode", ["parallel", "serial"])
def test_shared_preparation_runs_once_for_multiple_fields(runtime, mode):
    launch(runtime, document(mode))
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert calls(runtime, "acquire") == calls(runtime, "parse") == 1
    assert calls(runtime, "judge") == 2
    prepared = json.loads(runs(runtime.factory, "prepare")[0].output_json)["data"]
    for node in ("voltage", "current"):
        run = runs(runtime.factory, node)[0]
        assert json.loads(run.output_json)["data"] == prepared
        assert len(run.output_json) < 300
        assert '"large"' not in run.evidence_json


def test_manual_wait_retry_and_new_preparation_version(runtime):
    launch(runtime, document(manual=True))
    wait_for(runtime.factory, lambda: len(pending_reviews(runtime.factory)) == 2)
    old_run = runs(runtime.factory, "prepare")[0]
    old_ref = json.loads(old_run.output_json)["data"]
    for review in pending_reviews(runtime.factory):
        assert json.loads(review.request_json)["pipeline"]["parsed"] == old_ref
    review = next(item for item in pending_reviews(runtime.factory) if item.node_id == "voltage")
    runtime.scheduler.submit_review("i1", review.id, {"result": "yes", "reason": "确认"})
    assert calls(runtime, "acquire") == calls(runtime, "parse") == 1
    target = token_of(runtime.factory, "voltage")
    runtime.scheduler.rollback("i1", None, "default", "重新判断", target_token_id=target.id, mode="retry")
    wait_for(runtime.factory, lambda: len(pending_reviews(runtime.factory)) == 1)
    assert calls(runtime, "acquire") == calls(runtime, "parse") == 1
    assert json.loads(pending_reviews(runtime.factory)[0].request_json)["pipeline"]["parsed"] == old_ref
    runtime.scheduler.rollback("i1", None, "default", "重新取数", target_token_id=old_run.token_id, mode="retry")
    wait_for(runtime.factory, lambda: len(pending_reviews(runtime.factory)) == 2)
    assert calls(runtime, "acquire") == calls(runtime, "parse") == 2
    new_ref = json.loads(runs(runtime.factory, "prepare")[-1].output_json)["data"]
    assert new_ref != old_ref
    assert all(json.loads(item.request_json)["pipeline"]["parsed"] == new_ref for item in pending_reviews(runtime.factory))
    assert json.loads(runs(runtime.factory, "prepare")[0].output_json)["data"] == old_ref


def test_manual_acquisition_preparation(runtime):
    doc = document()
    pipeline = doc["nodes"][1]["pipeline"]
    del pipeline["acquire"]
    pipeline["manualFields"] = [{"name": "voltage", "type": "number", "required": True},
                                {"name": "current", "type": "number", "required": True}]
    launch(runtime, doc)
    wait_for(runtime.factory, lambda: len(pending_reviews(runtime.factory)) == 1)
    review = pending_reviews(runtime.factory)[0]
    assert json.loads(review.request_json)["nodeKind"] == "dataPreparation"
    runtime.scheduler.submit_review("i1", review.id, {"result": "done", "data": {"voltage": 200, "current": 10}})
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert calls(runtime, "acquire") == 0
    assert calls(runtime, "parse") == 1


def test_index_reads_only_selected_field(tmp_path, caplog):
    ref = artifacts.save(tmp_path, {"rows": [{"电压": 205}], "unused": "x" * 2000000})
    with caplog.at_level(logging.INFO, logger="app.worker.artifacts"):
        assert artifacts.load(tmp_path, artifacts.select(ref, "rows.0.电压")) == 205
    assert "bytes=3" in caplog.text
    with pytest.raises(ValueError, match="字段不存在"):
        artifacts.load(tmp_path, artifacts.select(ref, "rows.99"))
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="不存在"):
        artifacts.load(other, ref)
    with pytest.raises(ValueError, match="标识无效"):
        artifacts.load(tmp_path, {"$artifact": "../escape", "path": []})


@pytest.mark.parametrize("case", ["bypass", "self", "nonpreparation", "missing", "mixed", "duplicate", "branch"])
def test_invalid_shared_graph_rejected(case):
    doc = document()
    source = doc["nodes"][2]["pipeline"]["dataSource"]
    if case == "bypass":
        doc["edges"].append({"id": "bypass", "from": "start", "to": "voltage"})
    elif case == "self":
        source["node"] = "voltage"
    elif case == "nonpreparation":
        source["node"] = "current"
    elif case == "missing":
        source["node"] = "absent"
    elif case == "mixed":
        doc["nodes"][2]["pipeline"]["parse"] = binding("parse")
    elif case == "duplicate":
        doc["edges"].append({"id": "duplicate", "from": "prepare", "to": "voltage"})
    else:
        doc["edges"][1]["branch"] = "yes"
    with pytest.raises(ValidationError):
        compile_doc(doc, "flow", 1, FUNCTIONS)


def test_selected_scalar_scope_and_cross_project(runtime):
    doc = document()
    consumer = doc["nodes"][2]["pipeline"]
    consumer["dataSource"]["path"] = "voltage"
    consumer["present"]["arguments"]["value"] = {"source": "parsed"}
    consumer["judge"]["arguments"]["value"] = {"source": "parsed"}
    consumer["present"]["project"] = "other"
    functions = {**FUNCTIONS, "other:present": FUNCTIONS["demo:present"]}
    seed(runtime.factory, doc, functions=functions)
    with runtime.factory() as session:
        project = session.get(models.Project, "pr1")
        project.path, project.module = str(runtime.tmp), "shared_module"
        session.add(models.Project(id="pr2", name="other", path=str(runtime.tmp),
                                   module="shared_module", python=None, factory=None))
        session.commit()
    runtime.scheduler.start("i1")
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert calls(runtime, "acquire") == calls(runtime, "parse") == 1
    data = json.loads(runs(runtime.factory, "voltage")[0].output_json)["data"]
    assert data["path"] == ["voltage"]


def test_missing_field_preserves_manual_review(runtime):
    doc = document(manual=True)
    doc["nodes"][2]["pipeline"]["present"]["arguments"]["value"]["path"] = "absent"
    launch(runtime, doc)
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "waiting")
    assert token_of(runtime.factory, "voltage").status == "failed"
    assert len(pending_reviews(runtime.factory)) == 1
    assert "共享数据字段不存在" in runs(runtime.factory, "voltage")[0].error
    runtime.scheduler.terminate("i1")
    assert status_of(runtime.factory) == "terminated"
    assert pending_reviews(runtime.factory) == []
    assert calls(runtime, "acquire") == calls(runtime, "parse") == 1


def test_literal_reference_shape_is_not_interpreted(runtime):
    doc = document()
    doc["nodes"][2]["pipeline"]["present"]["arguments"]["value"] = {
        "lit": {"$artifact": "not-a-platform-artifact", "path": []}}
    launch(runtime, doc)
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert token_of(runtime.factory, "voltage").status == "done"


def test_legacy_argument_binding_reads_shared_result(runtime):
    doc = document()
    node = doc["nodes"][2]
    del node["pipeline"]
    node["binding"] = binding("judge")
    node["params"] = {"value": {"run": "prepare", "path": "output.data.voltage"}}
    launch(runtime, doc)
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert json.loads(runs(runtime.factory, "voltage")[0].input_json) == {"value": 205}
    assert calls(runtime, "acquire") == calls(runtime, "parse") == 1


def test_artifact_index_damage_fails_explicitly(tmp_path):
    ref = artifacts.save(tmp_path, {"value": 1})
    (tmp_path / ref["$artifact"] / "index.sqlite").write_bytes(b"invalid sqlite")
    with pytest.raises(ValueError, match="索引缺失或损坏"):
        artifacts.load(tmp_path, ref)

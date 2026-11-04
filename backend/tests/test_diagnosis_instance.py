"""诊断类真实子进程生命周期、实例缓存与回退数据视图。"""

import copy
import json
import logging
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import db, models
from app.services import signatures
from app.services.compiler import ValidationError, compile_doc
from app.services.scheduler import Scheduler
from app.services.sessions import SessionManager
from app.worker import artifacts
from app.worker.diagnosis_sdk import ResourceCache
from test_scheduler import seed, pending_reviews, runs, status_of, wait_for

EXAMPLE = BACKEND.parent / "examples" / "diagnosis-instance"


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWDIAG_DATA_DIR", str(tmp_path / "data"))
    db.configure(f"sqlite:///{(tmp_path / 'diag.db').as_posix()}")
    db.init_db()
    for path in EXAMPLE.glob("*.json"):
        shutil.copyfile(path, tmp_path / path.name)
    source = (EXAMPLE / "diagnosis.py").read_text(encoding="utf-8")
    source = source.replace('logger = logging.getLogger(__name__)',
                            'logger = logging.getLogger(__name__)\n'
                            f'logger.addHandler(logging.FileHandler({str(tmp_path / "calls.log")!r}, encoding="utf-8"))\n'
                            'logger.setLevel(logging.INFO)')
    (tmp_path / "diagnosis.py").write_text(source, encoding="utf-8")
    sessions = SessionManager()
    scheduler = Scheduler(db.SessionLocal, sessions=sessions, timeout=10)
    value = SimpleNamespace(factory=db.SessionLocal, tmp=tmp_path, scheduler=scheduler,
                            sessions=sessions)
    yield value
    for thread in list(scheduler._threads.values()):
        thread.join(10)
    sessions.shutdown()
    db.engine.dispose()


def launch(runtime, filename="workflow.json", device="motor-01", mode=None, mutate=None):
    doc = json.loads((runtime.tmp / filename).read_text(encoding="utf-8"))
    doc["diagnosis"]["project"] = "demo"
    if mode:
        doc["mode"] = mode
    if mutate:
        mutate(doc)
    project = SimpleNamespace(path=str(runtime.tmp), module="diagnosis", python=sys.executable, factory="")
    functions, error = signatures._probe_project(project)
    assert not error
    monkey = pytest.MonkeyPatch()
    monkey.setattr(signatures, "_load_project", lambda name: project)
    try:
        probed = signatures.probe_doc(doc)
    finally:
        monkey.undo()
    plan = seed(runtime.factory, doc, snapshot={"global": {
        "device_id": device, "time_point": "2026-11-05T08:00:00+08:00",
    }}, functions=probed)
    with runtime.factory() as session:
        registered = session.get(models.Project, "pr1")
        registered.path, registered.module, registered.python = str(runtime.tmp), "diagnosis", sys.executable
        session.commit()
    assert signatures.compare_signatures({**plan, "signature": signatures.collect_signatures(plan, probed)}) == []
    runtime.scheduler.start("i1")
    return doc, plan, probed


def count(runtime, message):
    return (runtime.tmp / "calls.log").read_text(encoding="utf-8").count(message)


def wait_review(runtime, node):
    wait_for(runtime.factory, lambda: any(r.node_id == node for r in pending_reviews(runtime.factory)), timeout=10)
    return next(r for r in pending_reviews(runtime.factory) if r.node_id == node)


def test_eager_lazy_manual_release_and_restore(runtime):
    launch(runtime)
    action = wait_review(runtime, "action")
    assert count(runtime, "启动获取报告") == count(runtime, "启动解析报告") == 1
    assert count(runtime, "首次获取波形") == 0
    assert "i1" not in runtime.sessions._handles
    runtime.scheduler.submit_review("i1", action.id, {"result": "done"})
    review = wait_review(runtime, "review")
    assert count(runtime, "首次获取波形") == count(runtime, "首次解析波形") == 1
    assert "i1" not in runtime.sessions._handles
    runtime.scheduler.submit_review("i1", review.id, {"result": "yes", "reason": "确认波形"})
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert count(runtime, "启动获取报告") == count(runtime, "首次获取波形") == 1
    assert count(runtime, "创建诊断对象") == 3
    assert runs(runtime.factory, "confirm")[0].status == "ok"
    wait_for(runtime.factory, lambda: count(runtime, "释放诊断对象") == 3)
    assert count(runtime, "释放诊断对象") == 3


def test_unused_branch_does_not_load(runtime):
    launch(runtime, device="motor-02")
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert count(runtime, "启动获取报告") == 1
    assert count(runtime, "首次获取波形") == 0


@pytest.mark.parametrize("mode", ["parallel", "serial"])
def test_sibling_methods_share_resources(runtime, mode):
    launch(runtime, "workflow-parallel.json", mode=mode)
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert count(runtime, "首次获取波形") == 1
    assert count(runtime, "创建诊断对象") == 1


def test_different_requests_have_separate_cache(runtime):
    launch(runtime, "workflow-requests.json")
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "escalated")
    assert count(runtime, "首次获取波形") == 2


def test_retry_uses_selected_visit_cache_and_older_view(runtime):
    launch(runtime)
    action = wait_review(runtime, "action")
    runtime.scheduler.submit_review("i1", action.id, {"result": "done"})
    first = wait_review(runtime, "review")
    waveform_run = runs(runtime.factory, "waveform")[0]
    runtime.scheduler.rollback("i1", None, "default", "重复判断",
                               target_token_id=waveform_run.token_id, mode="retry")
    wait_for(runtime.factory, lambda: len(runs(runtime.factory, "waveform")) == 2)
    second = wait_review(runtime, "review")
    assert second.id != first.id
    assert count(runtime, "首次获取波形") == 1
    runtime.scheduler.rollback("i1", None, "default", "回到尚未取波形的到访",
                               target_token_id=action.token_id, mode="retry")
    again = wait_review(runtime, "action")
    runtime.scheduler.submit_review("i1", again.id, {"result": "done"})
    wait_review(runtime, "review")
    assert count(runtime, "首次获取波形") == 2
    assert count(runtime, "启动获取报告") == 1


def test_missing_saved_data_is_error_without_reacquisition(runtime):
    _, plan, _ = launch(runtime)
    action = wait_review(runtime, "action")
    with runtime.factory() as session:
        saved = session.scalars(select(models.DiagnosisState).where(
            models.DiagnosisState.token_id == action.token_id)).first()
        reference = json.loads(saved.reference_json)
        root = runtime.scheduler._artifact_root("i1")
        manifest = artifacts.load(root, reference)
    (root / manifest["data"]["report"]["$artifact"] / "data.json").write_text("bad", encoding="utf-8")
    # 后续节点明确读取报告，以确认恢复不会绕过损坏的数据重新初始化。
    with runtime.factory() as session:
        release = session.get(models.Release, "rel1")
        plan["steps"]["waveform"]["diagnosisMethod"]["name"] = "check_voltage"
        release.plan_json = json.dumps(plan)
        session.commit()
    runtime.scheduler.submit_review("i1", action.id, {"result": "done"})
    wait_for(runtime.factory, lambda: status_of(runtime.factory) == "failed")
    assert "损坏" in runs(runtime.factory, "waveform")[0].error
    assert count(runtime, "启动获取报告") == 1


def test_invalid_method_and_mixed_protocol_rejected(runtime):
    doc, _, probed = launch(runtime, "workflow-parallel.json")
    for mutate in (lambda value: value["nodes"][1].update(method="waveform"),
                   lambda value: value["nodes"][1].update(pipeline={"manualFields": []}),
                   lambda value: value.pop("diagnosis")):
        invalid = copy.deepcopy(doc)
        mutate(invalid)
        with pytest.raises(ValidationError):
            compile_doc(invalid, "f1", 2, functions=probed)


def test_cache_normalizes_defaults_keeps_empty_and_does_not_cache_failures(tmp_path):
    owner = SimpleNamespace(calls=0)
    cache = ResourceCache(owner, tmp_path, {"device": "one"})

    def loader(self, channel="A", scale=1):
        self.calls += 1
        if channel == "bad":
            raise ValueError("读取失败")
        return None if channel == "empty" else {"values": [scale]}

    assert cache.get(loader, "1", (), {}) == {"values": [1]}
    value = cache.get(loader, "1", ("A",), {"scale": 1})
    value["values"][0] = 999
    assert cache.get(loader, "1", (), {}) == {"values": [1]}
    assert owner.calls == 1
    assert cache.get(loader, "1", ("empty",), {}) is None
    assert cache.get(loader, "1", ("empty",), {}) is None
    assert owner.calls == 2
    for _ in range(2):
        with pytest.raises(ValueError, match="读取失败"):
            cache.get(loader, "1", ("bad",), {})
    assert owner.calls == 4
    restored = ResourceCache(owner, tmp_path, {"device": "one"}, cache.references)
    assert restored.get(loader, "1", (), {}) == {"values": [1]}
    assert owner.calls == 4
    restored.get(loader, "2", (), {})
    assert owner.calls == 5


def test_concurrent_cache_requests_are_coalesced(tmp_path):
    owner = SimpleNamespace(calls=0)
    cache = ResourceCache(owner, tmp_path, {})
    entered, release = threading.Event(), threading.Event()

    def loader(self, device):
        self.calls += 1
        entered.set()
        assert release.wait(3)
        return {"device": device}

    with ThreadPoolExecutor(max_workers=6) as pool:
        first = pool.submit(cache.get, loader, "1", ("one",), {})
        assert entered.wait(3)
        rest = [pool.submit(cache.get, loader, "1", (), {"device": "one"}) for _ in range(5)]
        release.set()
        assert all(task.result() == {"device": "one"} for task in [first, *rest])
    assert owner.calls == 1

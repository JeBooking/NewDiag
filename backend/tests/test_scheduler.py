"""调度状态机测试：假会话管理器 + 临时 SQLite，跑真实 db/token/run/review 写入。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import db, models
from app.services.compiler import compile_doc
from app.services.scheduler import Scheduler

TIMEOUT = 8.0


# ---------------------------------------------------------------- 假工程


class FakeSessions:
    """不碰子进程，只记录调用并返回预设值。"""

    def __init__(self, results: dict | None = None) -> None:
        self.results = dict(results or {})
        self.calls: list[dict] = []
        self.closed: list[str] = []

    def call(self, instance_id, project, function, args, timeout=120):
        self.calls.append({"instance": instance_id, "function": function, "args": args})
        value = self.results.get(function)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(args)
        return value

    def close(self, instance_id):
        self.closed.append(instance_id)


# ---------------------------------------------------------------- 文档


def _start(inputs: list[dict] | None = None) -> dict:
    node = {
        "id": "start",
        "kind": "start",
        "title": "开始",
        "detail": "",
        "position": {"x": 0, "y": 0},
    }
    if inputs is not None:
        node["inputs"] = inputs
    return node


def _node(nid, kind, title, code: bool = False) -> dict:
    node = {
        "id": nid,
        "kind": kind,
        "title": title,
        "detail": f"{title}的说明",
        "position": {"x": 0, "y": 0},
    }
    if kind == "investigation":
        node["fields"] = {"observation": "电压", "acquisition": "读数据", "assessment": "阈值判断"}
        if code:
            node["binding"] = {"project": "demo", "function": "judge_voltage"}
    return node


def straight_doc(mode: str = "parallel") -> dict:
    """排查(代码) → 排查(人工) → 结论 → 处置 → 问题解决，否 → 上升。"""
    return {
        "format": 1,
        "name": "直线流程",
        "mode": mode,
        "nodes": [
            _start(),
            _node("n1", "investigation", "查母线电压", code=True),
            _node("n3", "investigation", "人工确认现象"),
            _node("c1", "conclusion", "电压不足"),
            _node("r1", "remediation", "调整稳压器"),
            _node("p1", "problemSolved", "问题解决"),
            _node("e1", "escalation", "上升"),
        ],
        "edges": [
            {"id": "s0", "from": "start", "to": "n1", "order": 1},
            {"id": "x1", "from": "n1", "to": "n3", "branch": "yes", "order": 1},
            {"id": "x2", "from": "n1", "to": "e1", "branch": "no", "order": 2},
            {"id": "x3", "from": "n3", "to": "c1", "branch": "yes", "order": 1},
            {"id": "x4", "from": "n3", "to": "e1", "branch": "no", "order": 2},
            {"id": "x5", "from": "c1", "to": "r1", "order": 1},
            {"id": "x6", "from": "r1", "to": "p1", "order": 1},
        ],
    }


def branched_doc() -> dict:
    """入口两个「是」分支，用于验证串行队列与成功后暂停。"""
    return {
        "format": 1,
        "name": "两条思路",
        "mode": "serial",
        "nodes": [
            _start(),
            _node("n1", "investigation", "入口排查", code=True),
            _node("m1", "investigation", "思路一判断"),
            _node("c1", "conclusion", "思路一结论"),
            _node("r1", "remediation", "思路一处置"),
            _node("p1", "problemSolved", "思路一解决"),
            _node("m2", "investigation", "思路二判断"),
            _node("c2", "conclusion", "思路二结论"),
            _node("r2", "remediation", "思路二处置"),
            _node("p2", "problemSolved", "思路二解决"),
            _node("e1", "escalation", "上升"),
        ],
        "edges": [
            {"id": "s0", "from": "start", "to": "n1", "order": 1},
            {"id": "a1", "from": "n1", "to": "m1", "branch": "yes", "order": 1},
            {"id": "a2", "from": "n1", "to": "m2", "branch": "yes", "order": 2},
            {"id": "a3", "from": "n1", "to": "e1", "branch": "no", "order": 3},
            {"id": "a4", "from": "m1", "to": "c1", "branch": "yes", "order": 1},
            {"id": "a5", "from": "m1", "to": "e1", "branch": "no", "order": 2},
            {"id": "a6", "from": "c1", "to": "r1", "order": 1},
            {"id": "a7", "from": "r1", "to": "p1", "order": 1},
            {"id": "a8", "from": "m2", "to": "c2", "branch": "yes", "order": 1},
            {"id": "a9", "from": "m2", "to": "e1", "branch": "no", "order": 2},
            {"id": "a10", "from": "c2", "to": "r2", "order": 1},
            {"id": "a11", "from": "r2", "to": "p2", "order": 1},
        ],
    }


# ---------------------------------------------------------------- 夹具


@pytest.fixture()
def env(tmp_path):
    db.configure(f"sqlite:///{(tmp_path / 'diag.db').as_posix()}")
    db.init_db()
    factory = db.SessionLocal
    sessions = FakeSessions({"judge_voltage": True})
    sched = Scheduler(factory, sessions=sessions, timeout=TIMEOUT)
    yield SimpleNamespace(factory=factory, sessions=sessions, scheduler=sched, tmp=tmp_path)
    db.engine.dispose()


def seed(
    factory,
    doc: dict,
    mode: str | None = None,
    snapshot: dict | None = None,
    functions: dict | None = None,
) -> dict:
    plan = compile_doc(doc, "f1", 1, functions=functions)
    mode = mode or doc.get("mode") or "parallel"
    session = factory()
    try:
        session.add(models.Flow(id="f1", name="流程", description="", mode=mode, owner_id="local"))
        session.add(
            models.Revision(
                id="r1",
                flow_id="f1",
                json=json.dumps(doc, ensure_ascii=False),
                status="committed",
                author="local",
            )
        )
        session.add(
            models.Release(
                id="rel1",
                flow_id="f1",
                revision_id="r1",
                version_no=1,
                plan_json=json.dumps(plan, ensure_ascii=False),
                signature_json="{}",
                published_by="local",
            )
        )
        session.add(
            models.Instance(
                id="i1",
                flow_id="f1",
                release_id="rel1",
                status="running",
                snapshot_id="s1",
                started_by="local",
            )
        )
        session.add(
            models.Snapshot(
                id="s1",
                instance_id="i1",
                seq=1,
                payload_json=json.dumps(snapshot or {}, ensure_ascii=False),
                params_json="{}",
            )
        )
        session.add(
            models.Project(id="pr1", name="demo", path="", module="", python=None, factory=None)
        )
        session.commit()
    finally:
        session.close()
    return plan


# ---------------------------------------------------------------- 读取辅助


def read(factory, fn):
    session = factory()
    try:
        return fn(session)
    finally:
        session.close()


def wait_for(factory, predicate, timeout: float = 5.0, label: str = "条件") -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except OperationalError as exc:  # 并发写时可能短暂锁库
            last = exc
        time.sleep(0.02)
    raise AssertionError(f"等待{label}超时：{last}")


def status_of(factory) -> str:
    return read(factory, lambda s: s.get(models.Instance, "i1").status)


def tokens(factory) -> list:
    return read(
        factory,
        lambda s: list(
            s.scalars(
                select(models.Token)
                .where(models.Token.instance_id == "i1")
                .order_by(models.Token.generation, models.Token.sort_order)
            )
        ),
    )


def token_of(factory, node_id: str):
    candidates = [t for t in tokens(factory) if t.node_id == node_id]
    return candidates[-1] if candidates else None


def pending_reviews(factory) -> list:
    return read(
        factory,
        lambda s: list(
            s.scalars(
                select(models.Review).where(
                    models.Review.instance_id == "i1", models.Review.status == "pending"
                )
            )
        ),
    )


def runs(factory, node_id: str | None = None) -> list:
    def query(session):
        statement = select(models.Run).where(models.Run.instance_id == "i1")
        if node_id:
            statement = statement.where(models.Run.node_id == node_id)
        return list(session.scalars(statement.order_by(models.Run.started_at)))

    return read(factory, query)


# ---------------------------------------------------------------- 测试


def test_parallel_flow_reaches_success(env):
    seed(env.factory, straight_doc())
    env.sessions.results["judge_voltage"] = True

    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工排查待办")

    assert token_of(env.factory, "n1").status == "done"
    assert token_of(env.factory, "n3").status == "waiting"
    assert env.sessions.calls == [{"instance": "i1", "function": "judge_voltage", "args": {}}]
    judge_runs = runs(env.factory, "n1")
    assert [run.status for run in judge_runs] == ["ok"]
    assert json.loads(judge_runs[0].output_json) is True
    assert json.loads(judge_runs[0].evidence_json) == {"branch": "yes"}

    review = pending_reviews(env.factory)[0]
    assert review.node_id == "n3"
    assert review.kind == "decision"
    request = json.loads(review.request_json)
    assert request["title"] == "人工确认现象"

    env.scheduler.submit_review("i1", review.id, {"result": "yes"})
    wait_for(
        env.factory,
        lambda: any(r.node_id == "r1" for r in pending_reviews(env.factory)),
        label="处置待办",
    )
    action = pending_reviews(env.factory)[0]
    assert action.kind == "action"
    assert token_of(env.factory, "c1").status == "done"
    assert token_of(env.factory, "r1").status == "waiting"

    env.scheduler.submit_review("i1", action.id, {"result": "done"})
    wait_for(env.factory, lambda: status_of(env.factory) == "success", label="实例成功")

    assert token_of(env.factory, "p1").status == "done"
    assert env.sessions.closed == ["i1"]


def test_manual_no_branch_goes_to_escalation(env):
    seed(env.factory, straight_doc())
    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "waiting")

    review = pending_reviews(env.factory)[0]
    env.scheduler.submit_review("i1", review.id, {"result": "no"})
    wait_for(env.factory, lambda: status_of(env.factory) == "escalated", label="实例上升")

    assert token_of(env.factory, "e1").status == "done"
    assert token_of(env.factory, "c1") is None


def test_auto_escalation_is_not_failure(env):
    """自动排查判否直达上升：结果是 escalated，不是 failed。"""
    seed(env.factory, straight_doc())
    env.sessions.results["judge_voltage"] = False

    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "escalated", label="自动上升")

    assert token_of(env.factory, "e1").status == "done"
    assert token_of(env.factory, "n3") is None
    assert env.sessions.closed == ["i1"]


def test_success_beats_escalation(env):
    """同时有上升路径和解决路径时，实例是 success。"""
    doc = {
        "format": 1,
        "name": "双支路",
        "mode": "parallel",
        "nodes": [
            _start(),
            _node("n1", "investigation", "入口", code=True),
            _node("a1", "investigation", "主路径判断"),
            _node("b1", "investigation", "备路径判断"),
            _node("c1", "conclusion", "需要处置"),
            _node("r1", "remediation", "处置"),
            _node("p1", "problemSolved", "问题解决"),
            _node("e1", "escalation", "上升"),
        ],
        "edges": [
            {"id": "st0", "from": "start", "to": "n1", "order": 1},
            {"id": "s1", "from": "n1", "to": "a1", "branch": "yes", "order": 1},
            {"id": "s2", "from": "n1", "to": "b1", "branch": "yes", "order": 2},
            {"id": "s3", "from": "n1", "to": "e1", "branch": "no", "order": 3},
            {"id": "s4", "from": "a1", "to": "c1", "branch": "yes"},
            {"id": "s5", "from": "a1", "to": "e1", "branch": "no"},
            {"id": "s6", "from": "b1", "to": "e1", "branch": "yes"},
            {"id": "s7", "from": "b1", "to": "e1", "branch": "no"},
            {"id": "s8", "from": "c1", "to": "r1"},
            {"id": "s9", "from": "r1", "to": "p1"},
        ],
    }
    seed(env.factory, doc)
    env.scheduler.start("i1")
    wait_for(env.factory, lambda: len(pending_reviews(env.factory)) == 2, label="两条待办")

    backup = [r for r in pending_reviews(env.factory) if r.node_id == "b1"][0]
    env.scheduler.submit_review("i1", backup.id, {"result": "no"})
    wait_for(env.factory, lambda: token_of(env.factory, "e1").status == "done", label="上升完成")
    assert status_of(env.factory) == "waiting"

    main = [r for r in pending_reviews(env.factory) if r.node_id == "a1"][0]
    env.scheduler.submit_review("i1", main.id, {"result": "yes"})
    wait_for(
        env.factory,
        lambda: any(r.node_id == "r1" for r in pending_reviews(env.factory)),
        label="处置待办",
    )
    fix = [r for r in pending_reviews(env.factory) if r.node_id == "r1"][0]
    env.scheduler.submit_review("i1", fix.id, {"result": "done"})

    wait_for(env.factory, lambda: status_of(env.factory) == "success", label="实例成功")
    assert token_of(env.factory, "e1").status == "done"


def test_business_error_marks_run_failed(env):
    seed(env.factory, straight_doc())
    env.sessions.results["judge_voltage"] = RuntimeError("设备离线")

    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "failed", label="执行失败")

    failed = runs(env.factory, "n1")
    assert len(failed) == 1
    assert failed[0].status == "failed"
    assert "execution/exception" in failed[0].error
    assert token_of(env.factory, "n1").status == "failed"


def test_rollback_abandons_path_and_bumps_generation(env):
    seed(env.factory, straight_doc())
    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "waiting")
    first_review = pending_reviews(env.factory)[0]
    assert first_review.node_id == "n3"

    env.scheduler.rollback("i1", "n1", "yes", "电压判断有误，需要重查")
    wait_for(
        env.factory,
        lambda: any(
            r.node_id == "n3" and r.status == "pending" and r.id != first_review.id
            for r in pending_reviews(env.factory)
        ),
        label="回退后的新待办",
    )

    all_tokens = tokens(env.factory)
    old = [t for t in all_tokens if t.node_id == "n3" and t.generation == 1]
    new = [t for t in all_tokens if t.node_id == "n3" and t.generation == 2]
    assert old and old[0].status == "abandoned"
    assert new and new[0].status == "waiting"
    assert token_of(env.factory, "n1").status == "done"
    assert status_of(env.factory) == "waiting"

    cancelled = read(
        env.factory,
        lambda s: s.get(models.Review, first_review.id).status,
    )
    assert cancelled == "cancelled"


def test_rollback_to_passthrough_node(env):
    """结论节点不产生 run，但令牌走过它就该能作为回退目标。"""
    seed(env.factory, straight_doc())
    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工排查待办")
    env.scheduler.submit_review("i1", pending_reviews(env.factory)[0].id, {"result": "yes"})
    wait_for(
        env.factory,
        lambda: any(r.node_id == "r1" for r in pending_reviews(env.factory)),
        label="处置待办",
    )
    old_action = [t for t in tokens(env.factory) if t.node_id == "r1"][0]
    assert old_action.status == "waiting"

    env.scheduler.rollback("i1", "c1", "default", "结论判错了")
    wait_for(
        env.factory,
        lambda: any(r.node_id == "r1" and r.id != old_action.id for r in pending_reviews(env.factory)),
        label="回退后的新处置待办",
    )

    abandoned = [t for t in tokens(env.factory) if t.node_id == "r1" and t.generation == 1]
    fresh = [t for t in tokens(env.factory) if t.node_id == "r1" and t.generation == 2]
    assert abandoned and abandoned[0].status == "abandoned"
    assert fresh and fresh[0].status == "waiting"
    assert token_of(env.factory, "c1").status == "done"
    assert status_of(env.factory) == "waiting"


def test_rollback_to_waiting_node_redoes_it(env):
    """回退目标正是当前等人工的节点：重排一个令牌重做，而不是 409。"""
    seed(env.factory, straight_doc())
    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工排查待办")
    env.scheduler.submit_review("i1", pending_reviews(env.factory)[0].id, {"result": "yes"})
    wait_for(
        env.factory,
        lambda: any(r.node_id == "r1" for r in pending_reviews(env.factory)),
        label="处置待办",
    )
    first_review = [r for r in pending_reviews(env.factory) if r.node_id == "r1"][0]

    env.scheduler.rollback("i1", "r1", "default", "操作没做成，重来")
    wait_for(
        env.factory,
        lambda: any(r.node_id == "r1" and r.id != first_review.id for r in pending_reviews(env.factory)),
        label="重做后的待办",
    )

    old = [t for t in tokens(env.factory) if t.node_id == "r1" and t.generation == 1]
    fresh = [t for t in tokens(env.factory) if t.node_id == "r1" and t.generation == 2]
    assert old and old[0].status == "abandoned"
    assert fresh and fresh[0].status == "waiting"
    cancelled = read(env.factory, lambda s: s.get(models.Review, first_review.id).status)
    assert cancelled == "cancelled"


def test_rollback_rejected_when_other_path_active(env):
    doc = {
        "format": 1,
        "name": "并行两支",
        "mode": "parallel",
        "nodes": [
            _start(),
            _node("n1", "investigation", "入口", code=True),
            _node("t1", "investigation", "自动支路", code=True),
            _node("t2", "investigation", "人工支路"),
            _node("e1", "escalation", "上升"),
        ],
        "edges": [
            {"id": "st0", "from": "start", "to": "n1", "order": 1},
            {"id": "b1", "from": "n1", "to": "t1", "branch": "yes", "order": 1},
            {"id": "b2", "from": "n1", "to": "t2", "branch": "yes", "order": 2},
            {"id": "b3", "from": "n1", "to": "e1", "branch": "no", "order": 3},
            {"id": "b4", "from": "t1", "to": "e1", "branch": "yes", "order": 1},
            {"id": "b5", "from": "t1", "to": "e1", "branch": "no", "order": 2},
            {"id": "b6", "from": "t2", "to": "e1", "branch": "yes", "order": 1},
            {"id": "b7", "from": "t2", "to": "e1", "branch": "no", "order": 2},
        ],
    }
    seed(env.factory, doc)
    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工支路待办")
    assert token_of(env.factory, "t1").status == "done"
    assert token_of(env.factory, "t2").status == "waiting"

    # t2 仍在等待，属于 t1 之外的活跃路径
    with pytest.raises(ValueError):
        env.scheduler.rollback("i1", "t1", "yes", None)


def test_serial_pauses_after_success_then_continues(env):
    seed(env.factory, branched_doc())
    env.scheduler.start("i1")
    wait_for(env.factory, lambda: status_of(env.factory) == "waiting")
    first = pending_reviews(env.factory)[0]
    assert first.node_id == "m1"

    env.scheduler.submit_review("i1", first.id, {"result": "yes"})
    wait_for(
        env.factory,
        lambda: any(r.node_id == "r1" for r in pending_reviews(env.factory)),
        label="思路一处置",
    )
    action = [r for r in pending_reviews(env.factory) if r.node_id == "r1"][0]
    env.scheduler.submit_review("i1", action.id, {"result": "done"})

    wait_for(env.factory, lambda: status_of(env.factory) == "paused", label="成功后暂停")
    decision = [r for r in pending_reviews(env.factory) if r.node_id == "p1"][0]
    request = json.loads(decision.request_json)
    assert request["type"] == "after_success"
    assert token_of(env.factory, "p1").status == "done"

    env.scheduler.submit_review("i1", decision.id, {"result": "continue"})
    wait_for(
        env.factory,
        lambda: any(r.node_id == "m2" for r in pending_reviews(env.factory)),
        label="继续第二条思路",
    )
    second = [r for r in pending_reviews(env.factory) if r.node_id == "m2"][0]
    env.scheduler.submit_review("i1", second.id, {"result": "yes"})
    wait_for(
        env.factory,
        lambda: any(r.node_id == "r2" for r in pending_reviews(env.factory)),
        label="思路二处置",
    )
    last = [r for r in pending_reviews(env.factory) if r.node_id == "r2"][0]
    env.scheduler.submit_review("i1", last.id, {"result": "done"})

    wait_for(env.factory, lambda: status_of(env.factory) == "success", label="实例成功")
    assert token_of(env.factory, "p2").status == "done"


def test_failed_path_does_not_block_other_tokens(env):
    """并行：一条路径失败只结束自己，另一条继续。"""
    doc = straight_doc()
    doc["nodes"].append(_node("m2", "investigation", "并行的另一条"))
    doc["edges"].append({"id": "y1", "from": "n1", "to": "m2", "branch": "yes", "order": 2})
    doc["edges"].append({"id": "y2", "from": "m2", "to": "e1", "branch": "yes"})
    doc["edges"].append({"id": "y3", "from": "m2", "to": "e1", "branch": "no"})
    seed(env.factory, doc)

    env.scheduler.start("i1")
    wait_for(env.factory, lambda: len(pending_reviews(env.factory)) == 2, label="两条待办")
    assert token_of(env.factory, "n3").status == "waiting"
    assert token_of(env.factory, "m2").status == "waiting"

    review = [r for r in pending_reviews(env.factory) if r.node_id == "m2"][0]
    env.scheduler.submit_review("i1", review.id, {"result": "no"})
    wait_for(env.factory, lambda: token_of(env.factory, "e1").status == "done", label="上升分支")
    assert token_of(env.factory, "m2").status == "done"
    assert token_of(env.factory, "n3").status == "waiting"
    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="回到等待")


# ---------------------------------------------------------------- 开始节点


def fanout_doc(mode: str) -> dict:
    """开始节点两条出线，分别接一个人工排查。"""
    return {
        "format": 1,
        "name": "开始分叉",
        "mode": mode,
        "nodes": [
            _start(),
            _node("a1", "investigation", "第一条排查"),
            _node("a2", "investigation", "第二条排查"),
            _node("e1", "escalation", "上升一"),
            _node("e2", "escalation", "上升二"),
        ],
        "edges": [
            {"id": "f1", "from": "start", "to": "a1", "order": 1},
            {"id": "f2", "from": "start", "to": "a2", "order": 2},
            {"id": "f3", "from": "a1", "to": "e1", "branch": "yes", "order": 1},
            {"id": "f4", "from": "a2", "to": "e2", "branch": "yes", "order": 1},
        ],
    }


def test_start_fans_out_in_parallel(env):
    seed(env.factory, fanout_doc("parallel"))
    env.scheduler.start("i1")

    wait_for(
        env.factory,
        lambda: len([r for r in pending_reviews(env.factory) if r.node_id in ("a1", "a2")]) == 2,
        label="两条并行待办",
    )

    spawned = [t for t in tokens(env.factory) if t.node_id in ("a1", "a2")]
    assert len(spawned) == 2
    assert all(t.status == "waiting" for t in spawned)
    assert token_of(env.factory, "start").status == "done"
    assert runs(env.factory, "start") == []  # 开始节点不执行代码，不落 run
    assert status_of(env.factory) == "waiting"


def test_start_fans_out_in_serial(env):
    seed(env.factory, fanout_doc("serial"))
    env.scheduler.start("i1")

    wait_for(
        env.factory,
        lambda: any(r.node_id == "a1" for r in pending_reviews(env.factory)),
        label="队首先跑",
    )

    assert token_of(env.factory, "a1").status == "waiting"
    assert token_of(env.factory, "a2").status == "queued"  # 串行只跑队首
    assert len(pending_reviews(env.factory)) == 1


def bound_doc(params: dict | None = None) -> dict:
    """单个绑定节点，参数走启动参数快照。"""
    doc = straight_doc()
    node = next(item for item in doc["nodes"] if item["id"] == "n1")
    node["params"] = params or {"voltage": {"snapshot": "voltage"}}
    return doc


def test_snapshot_reads_layered_payload(env):
    seed(
        env.factory,
        bound_doc(),
        snapshot={"global": {"voltage": 12}, "scope": {"judge_voltage": {"voltage": 99}}},
    )
    env.scheduler.start("i1")

    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工待办")
    # 函数局部参数优先于全局
    assert env.sessions.calls[0]["args"] == {"voltage": 99}


def test_snapshot_falls_back_to_global(env):
    seed(
        env.factory,
        bound_doc(),
        snapshot={"global": {"voltage": 12}, "scope": {"other_function": {"voltage": 99}}},
    )
    env.scheduler.start("i1")

    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工待办")
    # 别的函数的作用域不可见
    assert env.sessions.calls[0]["args"] == {"voltage": 12}


def test_snapshot_reads_legacy_flat_payload(env):
    seed(env.factory, bound_doc(), snapshot={"voltage": 12})
    env.scheduler.start("i1")

    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工待办")
    assert env.sessions.calls[0]["args"] == {"voltage": 12}


def test_snapshot_missing_field_fails_argument(env):
    seed(env.factory, bound_doc(), snapshot={"global": {"current": 3}})
    env.scheduler.start("i1")

    wait_for(env.factory, lambda: status_of(env.factory) == "failed", label="参数失败")
    failed = runs(env.factory, "n1")
    assert failed and "execution/argument" in failed[0].error


def with_start_inputs(doc: dict, inputs: list[dict]) -> dict:
    start = next(item for item in doc["nodes"] if item["id"] == "start")
    start["inputs"] = inputs
    return doc


def test_optional_param_omitted_when_snapshot_missing(env):
    """可选参数留空：省略该参数，让函数用自己的默认值，不报 execution/argument。"""
    doc = with_start_inputs(straight_doc(), [{"name": "threshold", "scope": "global"}])
    functions = {
        "demo:judge_voltage": [
            {"name": "threshold", "type": "float", "required": False, "default": 210.0}
        ]
    }
    seed(env.factory, doc, snapshot={"global": {}}, functions=functions)
    env.scheduler.start("i1")

    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工待办")
    assert env.sessions.calls[0]["args"] == {}
    assert runs(env.factory, "n1")[0].status == "ok"


def test_optional_param_passed_when_snapshot_has_value(env):
    doc = with_start_inputs(straight_doc(), [{"name": "threshold", "scope": "global"}])
    functions = {
        "demo:judge_voltage": [
            {"name": "threshold", "type": "float", "required": False, "default": 210.0}
        ]
    }
    seed(env.factory, doc, snapshot={"global": {"threshold": 198}}, functions=functions)
    env.scheduler.start("i1")

    wait_for(env.factory, lambda: status_of(env.factory) == "waiting", label="人工待办")
    assert env.sessions.calls[0]["args"] == {"threshold": 198}


def test_required_param_missing_still_fails(env):
    """必填参数没有 optional 标记，缺值必须报 execution/argument。"""
    doc = with_start_inputs(straight_doc(), [{"name": "voltage", "scope": "global"}])
    functions = {
        "demo:judge_voltage": [
            {"name": "voltage", "type": "float", "required": True, "default": None}
        ]
    }
    seed(env.factory, doc, snapshot={"global": {}}, functions=functions)
    env.scheduler.start("i1")

    wait_for(env.factory, lambda: status_of(env.factory) == "failed", label="参数失败")
    failed = runs(env.factory, "n1")
    assert failed and "execution/argument" in failed[0].error


# ---------------------------------------------------------------- 跳转节点


def jump_doc() -> dict:
    """排查：是 → 跳转（转交别的链路）→ 问题解决；否 → 上升。"""
    return {
        "format": 1,
        "name": "转交流程",
        "mode": "parallel",
        "nodes": [
            _start(),
            _node("n1", "investigation", "判断能否本流程处理", code=True),
            _node("j1", "jump", "转交整机链路"),
            _node("p1", "problemSolved", "问题解决"),
            _node("e1", "escalation", "上升"),
        ],
        "edges": [
            {"id": "s0", "from": "start", "to": "n1", "order": 1},
            {"id": "x1", "from": "n1", "to": "j1", "branch": "yes", "order": 1},
            {"id": "x2", "from": "n1", "to": "e1", "branch": "no", "order": 2},
            {"id": "x3", "from": "j1", "to": "p1", "order": 1},
        ],
    }


def test_jump_waits_review_then_reaches_success(env):
    seed(env.factory, jump_doc())
    env.sessions.results["judge_voltage"] = True

    env.scheduler.start("i1")
    wait_for(
        env.factory,
        lambda: any(r.node_id == "j1" for r in pending_reviews(env.factory)),
        label="转交待办",
    )

    review = next(r for r in pending_reviews(env.factory) if r.node_id == "j1")
    assert review.kind == "action"
    request = json.loads(review.request_json)
    assert request["nodeKind"] == "jump"
    assert "转交" in request["instructions"]
    assert token_of(env.factory, "j1").status == "waiting"

    env.scheduler.submit_review("i1", review.id, {"result": "done"})
    wait_for(env.factory, lambda: status_of(env.factory) == "success", label="转交后成功")

    assert token_of(env.factory, "j1").status == "done"
    assert token_of(env.factory, "p1").status == "done"

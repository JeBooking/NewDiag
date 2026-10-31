"""后端 API 冒烟测试。

每个用例用独立的临时数据目录与独立 SQLite 文件，绝不碰 backend/data。
编译 / 调度引擎尚未就绪时相关用例跳过，不算失败。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def valid_doc() -> dict:
    """一份能通过校验的最小流程：排查 → 结论 → 处置 → 问题解决。"""
    return {
        "format": 1,
        "name": "冷却诊断",
        "mode": "parallel",
        "nodes": [
            {
                "id": "n1",
                "kind": "investigation",
                "title": "检查母线电压",
                "detail": "观察母线电压是否低于 210V",
                "fields": {
                    "observation": "母线电压",
                    "acquisition": "读取数据服务器的 voltage",
                    "assessment": "低于 210V 判为异常",
                },
                "position": {"x": 120, "y": 80},
            },
            {
                "id": "n2",
                "kind": "conclusion",
                "title": "电压不足",
                "detail": "母线电压低于下限",
                "position": {"x": 120, "y": 220},
            },
            {
                "id": "n3",
                "kind": "remediation",
                "title": "更换电源模块",
                "detail": "更换后复测母线电压",
                "position": {"x": 120, "y": 360},
            },
            {
                "id": "n4",
                "kind": "problemSolved",
                "title": "已解决",
                "detail": "电压恢复正常",
                "position": {"x": 120, "y": 500},
            },
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1},
            {"id": "e2", "from": "n2", "to": "n3", "order": 1},
            {"id": "e3", "from": "n3", "to": "n4", "order": 1},
        ],
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWDIAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("NEWDIAG_DB", raising=False)

    from fastapi.testclient import TestClient

    from app import db

    db.configure()
    db.init_db()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def create_flow(client, name: str = "冷却诊断") -> dict:
    response = client.post("/api/flows", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def seed_committed_flow(client) -> dict:
    """建流程并提交一份合法文档，返回 FlowDetail。"""
    flow = create_flow(client)
    client.post(f"/api/flows/{flow['id']}/checkout").raise_for_status()
    client.put(f"/api/flows/{flow['id']}/working", json={"doc": valid_doc()}).raise_for_status()
    response = client.post(f"/api/flows/{flow['id']}/commit")
    assert response.status_code == 200, response.text
    return response.json()


def publish_committed_flow(client, flow_id: str, note: str | None = None) -> dict:
    """发布；只有引擎模块整体缺失（503）才跳过，其余失败都算契约破了。"""
    response = client.post(
        f"/api/flows/{flow_id}/releases", json={"note": note} if note else {}
    )
    if response.status_code == 503:
        pytest.skip("编译引擎未就绪")
    assert response.status_code == 200, response.text
    return response.json()


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_create_and_list_flow(client):
    flow = create_flow(client)
    assert flow["name"] == "冷却诊断"
    assert flow["doc"]["format"] == 1
    assert flow["doc"]["nodes"] == [] and flow["doc"]["edges"] == []
    assert flow["currentRevisionId"]
    assert flow["releaseCount"] == 0
    assert flow["lastRunStatus"] is None

    items = client.get("/api/flows").json()
    assert [item["id"] for item in items] == [flow["id"]]
    assert items[0]["releaseCount"] == 0
    assert items[0]["lastPublishedAt"] is None

    detail = client.get(f"/api/flows/{flow['id']}").json()
    assert detail["doc"]["name"] == "冷却诊断"
    assert detail["releases"] == []
    assert detail["workingRevisionId"] is None

    assert client.get("/api/flows/does-not-exist").status_code == 404


def test_patch_flow_metadata(client):
    flow = create_flow(client)
    response = client.patch(
        f"/api/flows/{flow['id']}",
        json={"name": "冷却诊断 v2", "description": "母线电压排查", "mode": "serial"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "冷却诊断 v2"
    assert body["description"] == "母线电压排查"
    assert body["mode"] == "serial"


def test_checkout_save_and_discard(client):
    flow = create_flow(client)
    flow_id = flow["id"]

    checked = client.post(f"/api/flows/{flow_id}/checkout").json()
    assert checked["editingBy"] == "local"
    working_id = checked["workingRevisionId"]
    assert working_id

    again = client.post(f"/api/flows/{flow_id}/checkout").json()
    assert again["workingRevisionId"] == working_id

    saved = client.put(f"/api/flows/{flow_id}/working", json={"doc": valid_doc()})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revisionId"] == working_id

    detail = client.get(f"/api/flows/{flow_id}").json()
    assert [node["id"] for node in detail["doc"]["nodes"]] == ["n1", "n2", "n3", "n4"]
    assert any(item["editingBy"] == "local" for item in client.get("/api/flows").json())

    discarded = client.post(f"/api/flows/{flow_id}/discard").json()
    assert discarded["editingBy"] is None
    assert discarded["workingRevisionId"] is None
    assert discarded["doc"]["nodes"] == []


def test_commit_then_list(client):
    flow = create_flow(client)
    flow_id = flow["id"]
    working = client.post(f"/api/flows/{flow_id}/checkout").json()

    client.put(f"/api/flows/{flow_id}/working", json={"doc": valid_doc()}).raise_for_status()
    response = client.post(f"/api/flows/{flow_id}/commit")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["editingBy"] is None
    assert body["workingRevisionId"] is None
    assert body["revisionId"] == working["workingRevisionId"]
    assert body["currentRevisionId"] == working["workingRevisionId"]
    assert body["doc"]["edges"][0]["branch"] == "yes"
    # 保存放过的 warning 从这里下发（阻断级问题走 422 detail）
    assert isinstance(body["issues"], list)

    items = client.get("/api/flows").json()
    assert items[0]["releaseCount"] == 0
    assert items[0]["editingBy"] is None


def test_commit_rejects_invalid_doc_and_keeps_lock(client):
    """结构性问题仍然阻断保存，且不能悄悄释放编辑锁。"""
    flow = create_flow(client)
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout").raise_for_status()
    client.put(
        f"/api/flows/{flow_id}/working",
        json={"doc": {"format": 1, "name": "空图", "mode": "parallel", "nodes": [], "edges": []}},
    ).raise_for_status()

    response = client.post(f"/api/flows/{flow_id}/commit")
    assert response.status_code == 422, response.text
    issues = response.json()["detail"]
    assert isinstance(issues, list) and issues
    assert all(item.get("level") == "error" for item in issues)

    detail = client.get(f"/api/flows/{flow_id}").json()
    assert detail["editingBy"] == "local"
    assert detail["workingRevisionId"]


def test_commit_accepts_doc_in_body(client):
    """前端保存时会把还没暂存的 doc 一起抛过来，不能丢。"""
    flow = create_flow(client)
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout")

    response = client.post(f"/api/flows/{flow_id}/commit", json={"doc": valid_doc()})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [node["id"] for node in body["doc"]["nodes"]] == ["n1", "n2", "n3", "n4"]
    assert body["doc"]["name"] == "冷却诊断"


def test_working_requires_lock(client):
    flow = create_flow(client)
    response = client.put(f"/api/flows/{flow['id']}/working", json={"doc": valid_doc()})
    assert response.status_code == 409

    assert client.post(f"/api/flows/{flow['id']}/discard").status_code == 409
    assert client.post("/api/flows/nope/checkout").status_code == 404


def test_admin_commit_without_lock(client):
    flow = create_flow(client)
    assert client.post(f"/api/flows/{flow['id']}/admin/commit").status_code == 409


def test_release_and_instance(client):
    flow = seed_committed_flow(client)
    flow_id = flow["id"]

    release = publish_committed_flow(client, flow_id, note="首版")
    assert release["versionNo"] == 1

    listed = client.get(f"/api/flows/{flow_id}/releases").json()
    assert [item["id"] for item in listed] == [release["id"]]

    summary = client.get("/api/flows").json()[0]
    assert summary["releaseCount"] == 1
    assert summary["lastPublishedAt"] == release["publishedAt"]

    detail = client.get(f"/api/releases/{release['id']}").json()
    assert detail["flowId"] == flow_id
    assert detail["plan"]
    assert detail["note"] == "首版"
    assert [node["id"] for node in detail["doc"]["nodes"]] == ["n1", "n2", "n3", "n4"]

    created = client.post(
        f"/api/flows/{flow_id}/instances",
        json={"releaseId": release["id"], "input": {"voltage": 205.0}},
    )
    assert created.status_code == 200, created.text
    instance = created.json()
    assert instance["status"] in {"running", "waiting", "success", "failed", "paused", "escalated"}
    assert instance["releaseNo"] == 1
    assert instance["flowName"] == "冷却诊断"

    instance_detail = client.get(f"/api/instances/{instance['id']}").json()
    assert instance_detail["snapshot"]["payload"] == {"voltage": 205.0}
    assert isinstance(instance_detail["tokens"], list)
    assert isinstance(instance_detail["reviews"], list)

    listed_instances = client.get("/api/instances", params={"flowId": flow_id}).json()
    assert [item["id"] for item in listed_instances] == [instance["id"]]

    # 待办提交返回整份详情；前端直接替换 store
    pending = [item for item in instance_detail["reviews"] if item["status"] == "pending"]
    if pending:
        decided = client.post(
            f"/api/instances/{instance['id']}/reviews/{pending[0]['id']}",
            json={"result": "yes"},
        )
        assert decided.status_code == 200, decided.text
        assert "tokens" in decided.json() and "reviews" in decided.json()

    # 回退载荷用前端的 targetNodeId，字段名要能对上（引擎拒绝时是 409，不是 422）
    rolled = client.post(
        f"/api/instances/{instance['id']}/rollback",
        json={"targetNodeId": "n1", "branch": "no", "reason": "复测"},
    )
    assert rolled.status_code != 422, rolled.text

    # SSE 是无尽流，这里只验证实例存在性校验，不消费流本身
    assert client.get("/api/instances/missing/events").status_code == 404


def test_instance_requires_release(client):
    flow = create_flow(client)
    response = client.post(f"/api/flows/{flow['id']}/instances", json={})
    assert response.status_code == 409


def test_file_upload_and_download(client):
    payload = b"bus voltage log\n205.3\n"
    response = client.post("/api/files", files={"file": ("voltage.txt", payload, "text/plain")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["hash"]) == 64
    assert body["size"] == len(payload)
    assert body["name"] == "voltage.txt"
    assert body["mediaType"] == "text/plain"

    again = client.post("/api/files", files={"file": ("copy.txt", payload, "text/plain")}).json()
    assert again["hash"] == body["hash"]

    download = client.get(f"/api/files/{body['hash']}")
    assert download.status_code == 200
    assert download.content == payload

    assert client.get("/api/files/" + "0" * 64).status_code == 404


def test_project_registration_and_discovery(client):
    created = client.post(
        "/api/projects",
        json={
            "name": "冷却系统",
            "path": str(BACKEND_DIR / "no-such-project"),
            "module": "cooling.diagnosis",
            "factory": "registry",
        },
    )
    assert created.status_code == 200, created.text
    project = created.json()
    assert project["name"] == "冷却系统"
    assert project["factory"] == "registry"
    assert project["python"] is None

    assert [item["id"] for item in client.get("/api/projects").json()] == [project["id"]]

    # 工程名是绑定与签名的唯一口径
    duplicate = client.post(
        "/api/projects",
        json={"name": "冷却系统", "path": str(BACKEND_DIR), "module": "other.module"},
    )
    assert duplicate.status_code == 409, duplicate.text

    # 发现失败不能冒 500，只返回空列表
    functions = client.get(f"/api/projects/{project['id']}/functions")
    assert functions.status_code == 200, functions.text
    assert isinstance(functions.json(), list)

    refreshed = client.post(f"/api/projects/{project['id']}/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert isinstance(body["functions"], list)
    assert "error" in body

    assert client.get("/api/projects/missing/functions").status_code == 404


def register_demo_project(client, tmp_path, name: str = "冷却系统") -> dict:
    """在本机真实目录里造一个可 import 的工程，让 probe 子进程能跑起来。"""
    project_dir = tmp_path / "demo-project"
    package = project_dir / "cooling"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "diagnosis.py").write_text(
        "def judge_voltage(voltage: float, threshold: float = 210.0) -> bool:\n"
        "    return voltage >= threshold\n",
        encoding="utf-8",
    )
    response = client.post(
        "/api/projects",
        json={"name": name, "path": str(project_dir), "module": "cooling.diagnosis"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def publish_doc(client, doc: dict) -> tuple[int, dict]:
    """建流程 → 提交 → 发布，返回 (状态码, 响应体)。"""
    flow = create_flow(client)
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout").raise_for_status()
    client.put(f"/api/flows/{flow_id}/working", json={"doc": doc}).raise_for_status()
    commit = client.post(f"/api/flows/{flow_id}/commit")
    if commit.status_code != 200:
        return commit.status_code, commit.json()
    response = client.post(f"/api/flows/{flow_id}/releases", json={})
    return response.status_code, response.json()


def test_publish_binds_only_real_params(client, tmp_path):
    """发布时按探测到的签名过滤参数：threshold 有默认值就不要传。"""
    register_demo_project(client, tmp_path)

    doc = valid_doc()
    doc["snapshotFields"] = ["voltage"]
    doc["nodes"][0]["binding"] = {"project": "冷却系统", "function": "judge_voltage"}

    status, body = publish_doc(client, doc)
    assert status == 200, body

    detail = client.get(f"/api/releases/{body['id']}").json()
    assert detail["plan"]["steps"]["n1"]["call"] == {
        "project": "冷却系统",
        "function": "judge_voltage",
        "args": {"voltage": {"snapshot": "voltage"}},
    }
    assert "冷却系统:judge_voltage" in detail["signature"]


def test_publish_reports_missing_required_param(client, tmp_path):
    """快照里没有 voltage 时，缺必填参数要在发布期就报错，不能拖到执行。"""
    register_demo_project(client, tmp_path, name="冷却系统2")

    doc = valid_doc()
    doc["nodes"][0]["binding"] = {"project": "冷却系统2", "function": "judge_voltage"}

    status, body = publish_doc(client, doc)
    assert status == 422, body
    messages = " ".join(item.get("message", "") for item in body["detail"])
    assert "voltage" in messages


def test_publish_rejects_unprobeable_project(client):
    """探测失败不降级放行：工程没登记就直接拒绝发布。"""
    doc = valid_doc()
    doc["nodes"][0]["binding"] = {"project": "没登记的工程", "function": "judge_voltage"}

    status, body = publish_doc(client, doc)
    assert status == 422, body
    messages = " ".join(item.get("message", "") for item in body["detail"])
    assert "接口探测失败" in messages


def test_escalation_ends_as_escalated(client):
    """到达「上升」节点是交接结束，不是失败。"""
    doc = {
        "format": 1,
        "name": "上升诊断",
        "mode": "parallel",
        "nodes": [
            {
                "id": "n1",
                "kind": "investigation",
                "title": "能否现场处理",
                "detail": "判断故障是否属于本岗范围",
                "fields": {
                    "observation": "故障现象",
                    "acquisition": "现场确认",
                    "assessment": "超出本岗范围则上升",
                },
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "n2",
                "kind": "escalation",
                "title": "上升",
                "detail": "转交专家处理",
                "position": {"x": 0, "y": 120},
            },
        ],
        "edges": [{"id": "e1", "from": "n1", "to": "n2", "branch": "no", "order": 1}],
    }

    flow = create_flow(client, name="上升诊断")
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout").raise_for_status()
    client.put(f"/api/flows/{flow_id}/working", json={"doc": doc}).raise_for_status()
    commit = client.post(f"/api/flows/{flow_id}/commit")
    assert commit.status_code == 200, commit.text
    publish_committed_flow(client, flow_id)

    instance = client.post(f"/api/flows/{flow_id}/instances", json={"input": {}}).json()
    review = wait_for_review(client, instance["id"], "decision")
    client.post(
        f"/api/instances/{instance['id']}/reviews/{review['id']}", json={"result": "no"}
    ).raise_for_status()

    deadline = time.time() + 8
    detail = client.get(f"/api/instances/{instance['id']}").json()
    while time.time() < deadline and detail["status"] not in {"success", "failed", "escalated"}:
        time.sleep(0.2)
        detail = client.get(f"/api/instances/{instance['id']}").json()

    assert detail["status"] == "escalated", detail["status"]
    assert detail["endedAt"]
    assert client.get("/api/flows").json()[0]["lastRunStatus"] == "escalated"


def wait_for_review(client, instance_id: str, kind: str, timeout: float = 8.0) -> dict:
    """轮询到指定类型的待办出现；超时说明调度没推进到预期位置，算失败。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/instances/{instance_id}").json()
        for review in detail["reviews"]:
            if review["status"] == "pending" and review["kind"] == kind:
                return review
        if detail["status"] in {"success", "failed", "escalated"}:
            break
        time.sleep(0.2)
    pytest.fail(f"等待 {kind} 待办超时，实例状态：{detail['status']}")


def test_commit_is_lenient_but_publish_is_strict(client, tmp_path):
    """保存允许半成品：参数歧义只给 warning；发布时才 422 拦下。"""
    register_demo_project(client, tmp_path, name="冷却系统B")

    fields = {
        "observation": "母线电压",
        "acquisition": "读取 voltage",
        "assessment": "低于 210V 判为异常",
    }
    doc = {
        "format": 1,
        "name": "半成品",
        "mode": "parallel",
        "snapshotFields": ["voltage"],
        "nodes": [
            {
                "id": "n1",
                "kind": "investigation",
                "title": "读电压",
                "detail": "读取母线电压",
                "fields": fields,
                "binding": {"project": "冷却系统B", "function": "judge_voltage"},
                "outputs": ["voltage"],
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "n2",
                "kind": "investigation",
                "title": "复核",
                "detail": "再次判断",
                "fields": fields,
                # voltage 有快照与 n1 两个来源，保存时放过、发布时必须显式指定
                "binding": {"project": "冷却系统B", "function": "judge_voltage"},
                "position": {"x": 0, "y": 100},
            },
            {
                "id": "n3",
                "kind": "escalation",
                "title": "上升",
                "detail": "转交专家",
                "position": {"x": 0, "y": 200},
            },
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1},
            {"id": "e2", "from": "n2", "to": "n3", "branch": "yes", "order": 1},
        ],
    }

    flow = create_flow(client, name="半成品")
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout").raise_for_status()
    client.put(f"/api/flows/{flow_id}/working", json={"doc": doc}).raise_for_status()

    commit = client.post(f"/api/flows/{flow_id}/commit")
    assert commit.status_code == 200, commit.text
    warnings = commit.json()["issues"]
    assert any("多个来源" in item["message"] for item in warnings), warnings

    publish = client.post(f"/api/flows/{flow_id}/releases", json={})
    assert publish.status_code == 422, publish.text
    messages = " ".join(item["message"] for item in publish.json()["detail"])
    assert "多个来源" in messages


def test_rollback_to_single_exit_node(client):
    """回退目标是非排查节点时，分支键是 default，不能被 schema 当成非法值拦下。"""
    flow = seed_committed_flow(client)
    flow_id = flow["id"]
    publish_committed_flow(client, flow_id)

    created = client.post(
        f"/api/flows/{flow_id}/instances", json={"input": {"voltage": 205.0}}
    )
    assert created.status_code == 200, created.text
    instance = created.json()

    decision = wait_for_review(client, instance["id"], "decision")
    client.post(
        f"/api/instances/{instance['id']}/reviews/{decision['id']}", json={"result": "yes"}
    ).raise_for_status()

    # 动作待办出现 = 结论 n2 已执行、n3 正在等人工
    wait_for_review(client, instance["id"], "action")

    response = client.post(
        f"/api/instances/{instance['id']}/rollback",
        json={"targetNodeId": "n2", "branch": "default", "reason": "结论有误，重判"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] in {"running", "waiting", "paused"}
    # 旧待办被取消，不该再有待处理项挂在新一代上
    assert any(item["status"] == "cancelled" for item in body["reviews"])

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


def start_doc_node(inputs: list[dict] | None = None) -> dict:
    """开始节点：只声明启动参数，没有诊断描述。"""
    return {
        "id": "n0",
        "kind": "start",
        "title": "开始",
        "detail": "启动参数",
        "inputs": inputs or [],
        "position": {"x": 120, "y": 0},
    }


def valid_doc() -> dict:
    """一份能通过校验的最小流程：开始 → 排查 → 结论 → 处置 → 问题解决。"""
    return {
        "format": 1,
        "name": "冷却诊断",
        "mode": "parallel",
        "nodes": [
            start_doc_node([{"name": "voltage", "scope": "global"}]),
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
            {"id": "e0", "from": "n0", "to": "n1"},
            {"id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1},
            {"id": "e2", "from": "n2", "to": "n3", "order": 1},
            {"id": "e3", "from": "n3", "to": "n4", "order": 1},
        ],
    }


def node_of(doc: dict, node_id: str) -> dict:
    return next(node for node in doc["nodes"] if node["id"] == node_id)


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
    assert [node["id"] for node in detail["doc"]["nodes"]] == ["n0", "n1", "n2", "n3", "n4"]
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
    assert body["doc"]["edges"][1]["branch"] == "yes"
    # 保存放过的 warning 从这里下发（阻断级问题走 422 detail）
    assert isinstance(body["issues"], list)

    items = client.get("/api/flows").json()
    assert items[0]["releaseCount"] == 0
    assert items[0]["editingBy"] is None


def test_commit_saves_invalid_doc_but_reports_issues(client):
    """保存不阻断：结构性问题随响应返回，留给发布拦截。"""
    flow = create_flow(client)
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout").raise_for_status()
    client.put(
        f"/api/flows/{flow_id}/working",
        json={"doc": {"format": 1, "name": "空图", "mode": "parallel", "nodes": [], "edges": []}},
    ).raise_for_status()

    response = client.post(f"/api/flows/{flow_id}/commit")
    assert response.status_code == 200, response.text
    issues = response.json()["issues"]
    assert isinstance(issues, list) and issues
    assert any(item.get("level") == "error" for item in issues)

    detail = client.get(f"/api/flows/{flow_id}").json()
    assert detail["editingBy"] is None
    assert detail["doc"]["nodes"] == []


def test_commit_accepts_doc_in_body(client):
    """前端保存时会把还没暂存的 doc 一起抛过来，不能丢。"""
    flow = create_flow(client)
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout")

    response = client.post(f"/api/flows/{flow_id}/commit", json={"doc": valid_doc()})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [node["id"] for node in body["doc"]["nodes"]] == ["n0", "n1", "n2", "n3", "n4"]
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
    assert [node["id"] for node in detail["doc"]["nodes"]] == ["n0", "n1", "n2", "n3", "n4"]

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
    # 快照是 global/scope 两层：前端按层提交，调度器按层取值
    assert instance_detail["snapshot"]["payload"] == {"global": {"voltage": 205.0}, "scope": {}}
    assert instance_detail["snapshot"]["source"] == "start"
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
    node_of(doc, "n1")["binding"] = {"project": "冷却系统", "function": "judge_voltage"}

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
    """启动参数里没有 voltage 时，缺必填参数要在发布期就报错，不能拖到执行。"""
    register_demo_project(client, tmp_path, name="冷却系统2")

    doc = valid_doc()
    node_of(doc, "n0")["inputs"] = []  # 开始节点没声明 voltage，节点也就没有来源
    node_of(doc, "n1")["binding"] = {"project": "冷却系统2", "function": "judge_voltage"}

    status, body = publish_doc(client, doc)
    assert status == 422, body
    messages = " ".join(item.get("message", "") for item in body["detail"])
    assert "voltage" in messages


def test_publish_rejects_unprobeable_project(client):
    """探测失败不降级放行：工程没登记就直接拒绝发布。"""
    doc = valid_doc()
    node_of(doc, "n1")["binding"] = {"project": "没登记的工程", "function": "judge_voltage"}

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
            start_doc_node(),
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
        "edges": [
            {"id": "e0", "from": "n0", "to": "n1"},
            {"id": "e1", "from": "n1", "to": "n2", "branch": "no", "order": 1},
        ],
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
    """保存允许半成品：参数来源找不到只给 warning；发布时才 422 拦下。"""
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
        "nodes": [
            start_doc_node([{"name": "voltage", "scope": "global"}]),
            {
                "id": "n1",
                "kind": "investigation",
                "title": "读电压",
                "detail": "读取母线电压",
                "fields": fields,
                "binding": {"project": "冷却系统B", "function": "judge_voltage"},
                # 显式指向一个不存在的来源：保存放过，发布必须拦
                "params": {"voltage": "还没建的上游节点"},
                "outputs": ["voltage"],
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "n2",
                "kind": "escalation",
                "title": "上升",
                "detail": "转交专家",
                "position": {"x": 0, "y": 200},
            },
        ],
        "edges": [
            {"id": "e0", "from": "n0", "to": "n1"},
            {"id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1},
        ],
    }

    flow = create_flow(client, name="半成品")
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout").raise_for_status()
    client.put(f"/api/flows/{flow_id}/working", json={"doc": doc}).raise_for_status()

    commit = client.post(f"/api/flows/{flow_id}/commit")
    assert commit.status_code == 200, commit.text
    warnings = commit.json()["issues"]
    assert any("还没建的上游节点" in item["message"] for item in warnings), warnings
    assert all(item["level"] == "warning" for item in warnings), warnings

    publish = client.post(f"/api/flows/{flow_id}/releases", json={})
    assert publish.status_code == 422, publish.text
    messages = " ".join(item["message"] for item in publish.json()["detail"])
    assert "还没建的上游节点" in messages


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


# ---------------------------------------------------------------- 目录与移动


def make_folder(client, name: str, parent_id: str | None = None) -> dict:
    response = client.post("/api/folders", json={"name": name, "parentId": parent_id})
    assert response.status_code == 200, response.text
    return response.json()


def folder_counts(client) -> dict[str, int]:
    return {item["id"]: item["flowCount"] for item in client.get("/api/folders").json()}


def test_folder_crud_and_guards(client):
    root = make_folder(client, "冷却")
    assert root["parentId"] is None
    assert root["flowCount"] == 0
    assert root["createdAt"]

    child = make_folder(client, "水泵", root["id"])
    assert child["parentId"] == root["id"]

    # 同名同父 409，不同父可同名
    assert client.post("/api/folders", json={"name": "冷却"}).status_code == 409
    make_folder(client, "冷却", root["id"])

    # 父目录必须存在
    assert client.post("/api/folders", json={"name": "孤儿", "parentId": "missing"}).status_code == 404

    listed = client.get("/api/folders").json()
    assert len(listed) == 3
    assert {item["parentId"] for item in listed} == {None, root["id"]}

    renamed = client.patch(f"/api/folders/{root['id']}", json={"name": "冷却系统"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "冷却系统"

    # 换父不能挂到自己或自己的后代下
    assert client.patch(f"/api/folders/{root['id']}", json={"parentId": root["id"]}).status_code == 409
    assert client.patch(f"/api/folders/{root['id']}", json={"parentId": child["id"]}).status_code == 409
    # 换父撞名同样 409
    assert client.patch(f"/api/folders/{child['id']}", json={"parentId": None, "name": "冷却系统"}).status_code == 409

    # 有子目录不能删
    assert client.delete(f"/api/folders/{root['id']}").status_code == 409

    # 有流程不能删
    flow = create_flow(client)
    client.patch(f"/api/flows/{flow['id']}", json={"folderId": child["id"]}).raise_for_status()
    counts = folder_counts(client)
    assert counts[child["id"]] == 1
    # 父目录的数字含子目录合计
    assert counts[root["id"]] == 1
    assert client.delete(f"/api/folders/{child['id']}").status_code == 409

    # 流程移走后空目录可删
    client.patch(f"/api/flows/{flow['id']}", json={"folderId": None}).raise_for_status()
    assert client.delete(f"/api/folders/{child['id']}").status_code == 204
    assert child["id"] not in folder_counts(client)

    assert client.delete("/api/folders/missing").status_code == 404


def test_flow_folder_filter_search_and_move(client):
    alpha = make_folder(client, "Alpha")
    beta = make_folder(client, "Beta")

    pump = create_flow(client, name="PUMP 诊断")
    valve = create_flow(client, name="阀门诊断")
    loose = create_flow(client, name="未分类")

    client.patch(f"/api/flows/{pump['id']}", json={"folderId": alpha["id"]}).raise_for_status()
    client.patch(f"/api/flows/{valve['id']}", json={"folderId": beta["id"]}).raise_for_status()
    client.patch(f"/api/flows/{valve['id']}", json={"description": "Cooling Loop 复测"}).raise_for_status()

    def ids(params: dict | None = None) -> set[str]:
        return {item["id"] for item in client.get("/api/flows", params=params or {}).json()}

    assert ids() == {pump["id"], valve["id"], loose["id"]}
    assert ids({"folderId": "root"}) == {loose["id"]}
    # none / null 是前端可能直接传的字面量，等同 root
    assert ids({"folderId": "none"}) == {loose["id"]}
    assert ids({"folderId": "null"}) == {loose["id"]}
    assert ids({"folderId": alpha["id"]}) == {pump["id"]}
    # 目录不存在时按空列表处理，不要把 404 抛给列表页
    assert ids({"folderId": "missing"}) == set()

    # search 不区分大小写，名称与描述都参与
    assert ids({"search": "pump"}) == {pump["id"]}
    assert ids({"search": "cooling"}) == {valve["id"]}
    assert ids({"search": "冷却"}) == set()
    assert ids({"folderId": alpha["id"], "search": "阀门"}) == set()

    # 移到未分类：显式传 null
    moved = client.patch(f"/api/flows/{pump['id']}", json={"folderId": None})
    assert moved.status_code == 200, moved.text
    assert moved.json()["folderId"] is None
    assert ids({"folderId": "root"}) == {loose["id"], pump["id"]}
    assert folder_counts(client)[alpha["id"]] == 0

    # 只改名字不能把目录清空
    renamed = client.patch(f"/api/flows/{valve['id']}", json={"name": "阀门诊断 v2"})
    assert renamed.json()["folderId"] == beta["id"]

    # 目录不存在要 404，别把流程挪丢
    assert client.patch(f"/api/flows/{valve['id']}", json={"folderId": "missing"}).status_code == 404
    assert client.get(f"/api/flows/{valve['id']}").json()["folderId"] == beta["id"]


# ---------------------------------------------------------------- 启动参数冻结与校验


def register_input_project(client, tmp_path, name: str = "输入工程") -> dict:
    """登记一个带 datetime / int 参数的工程，用来验证启动表单的类型与必填推导。"""
    project_dir = tmp_path / "input-project"
    package = project_dir / "inputs"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "diagnosis.py").write_text(
        "from datetime import datetime\n"
        "\n"
        "\n"
        "def check(\n"
        "    fault_time: datetime, device_id: str, chamber: str = 'A1', retry: int = 1\n"
        ") -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )
    response = client.post(
        "/api/projects",
        json={"name": name, "path": str(project_dir), "module": "inputs.diagnosis"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def input_doc(project: str, inputs: list[dict] | None = None) -> dict:
    """开始节点声明 global 与 function 两种作用域的启动参数。"""
    return {
        "format": 1,
        "name": "带启动参数",
        "mode": "parallel",
        "nodes": [
            start_doc_node(
                inputs
                if inputs is not None
                else [
                    {"name": "fault_time", "scope": "global"},
                    {"name": "device_id", "scope": "function", "function": "check"},
                ]
            ),
            {
                "id": "n1",
                "kind": "investigation",
                "title": "读取故障",
                "detail": "调用 check 判断",
                "fields": {
                    "observation": "故障时间与设备",
                    "acquisition": "调用 check",
                    "assessment": "返回 True 判为命中",
                },
                "binding": {"project": project, "function": "check"},
                "position": {"x": 0, "y": 100},
            },
            {
                "id": "n2",
                "kind": "escalation",
                "title": "上升",
                "detail": "转交专家",
                "position": {"x": 0, "y": 200},
            },
        ],
        "edges": [
            {"id": "e0", "from": "n0", "to": "n1"},
            {"id": "e1", "from": "n1", "to": "n2", "branch": "yes", "order": 1},
        ],
    }


def seed_published(client, doc: dict) -> tuple[str, dict]:
    flow = create_flow(client, name=doc.get("name") or "带启动参数")
    flow_id = flow["id"]
    client.post(f"/api/flows/{flow_id}/checkout").raise_for_status()
    client.put(f"/api/flows/{flow_id}/working", json={"doc": doc}).raise_for_status()
    client.post(f"/api/flows/{flow_id}/commit").raise_for_status()
    publish = client.post(f"/api/flows/{flow_id}/releases", json={})
    assert publish.status_code == 200, publish.text
    return flow_id, publish.json()


def test_release_freezes_start_inputs(client, tmp_path):
    """发布时把启动表单定稿：签名决定必填与控件类型，之后改 doc 不影响老版本。"""
    register_input_project(client, tmp_path)
    flow_id, release = seed_published(client, input_doc("输入工程"))

    detail = client.get(f"/api/releases/{release['id']}").json()
    inputs = {item["name"]: item for item in detail["inputs"]}
    assert set(inputs) == {"fault_time", "device_id"}

    # datetime 注解 → datetime 控件；签名无默认值 → 必填
    assert inputs["fault_time"]["type"] == "datetime"
    assert inputs["fault_time"]["scope"] == "global"
    assert inputs["fault_time"]["required"] is True
    assert inputs["fault_time"]["function"] is None

    # 函数作用域按函数名隔离
    assert inputs["device_id"]["scope"] == "function"
    assert inputs["device_id"]["function"] == "check"
    assert inputs["device_id"]["required"] is True

    # 改 doc 后重新发布，老版本的冻结结果不能变
    changed = input_doc(
        "输入工程",
        [
            {"name": "fault_time", "scope": "global"},
            {"name": "device_id", "scope": "function", "function": "check"},
            {"name": "retry", "scope": "global"},
        ],
    )
    client.post(f"/api/flows/{flow_id}/checkout").raise_for_status()
    client.put(f"/api/flows/{flow_id}/working", json={"doc": changed}).raise_for_status()
    client.post(f"/api/flows/{flow_id}/commit").raise_for_status()
    second = client.post(f"/api/flows/{flow_id}/releases", json={})
    assert second.status_code == 200, second.text

    old = client.get(f"/api/releases/{release['id']}").json()
    assert [item["name"] for item in old["inputs"]] == ["fault_time", "device_id"]

    new = client.get(f"/api/releases/{second.json()['id']}").json()
    newest = {item["name"]: item for item in new["inputs"]}
    assert set(newest) == {"fault_time", "device_id", "retry"}
    # 有默认值的 int 参数不算必填，默认值一并冻结下来
    assert newest["retry"]["required"] is False
    assert newest["retry"]["type"] == "number"
    assert newest["retry"]["default"] == 1


def test_instance_start_requires_required_inputs(client, tmp_path):
    register_input_project(client, tmp_path, name="输入工程2")
    flow_id, release = seed_published(client, input_doc("输入工程2"))

    missing_all = client.post(f"/api/flows/{flow_id}/instances", json={"releaseId": release["id"]})
    assert missing_all.status_code == 400, missing_all.text
    assert missing_all.json()["detail"] == "缺少启动参数：fault_time, device_id"

    # 只给全局那一层还差函数作用域的 device_id
    partial = client.post(
        f"/api/flows/{flow_id}/instances",
        json={"releaseId": release["id"], "input": {"global": {"fault_time": "2026-01-01T08:00:00"}}},
    )
    assert partial.status_code == 400, partial.text
    assert partial.json()["detail"] == "缺少启动参数：device_id"

    full = client.post(
        f"/api/flows/{flow_id}/instances",
        json={
            "releaseId": release["id"],
            "input": {
                "global": {"fault_time": "2026-01-01T08:00:00"},
                "scope": {"check": {"device_id": "D-17"}},
            },
        },
    )
    assert full.status_code == 200, full.text
    detail = client.get(f"/api/instances/{full.json()['id']}").json()
    assert detail["snapshot"]["payload"] == {
        "global": {"fault_time": "2026-01-01T08:00:00"},
        "scope": {"check": {"device_id": "D-17"}},
    }
    assert detail["snapshot"]["source"] == "start"

    # 老前端提交扁平对象时按全局处理，同样落成两层
    flat = client.post(
        f"/api/flows/{flow_id}/instances",
        json={
            "releaseId": release["id"],
            "input": {"fault_time": "2026-01-02T08:00:00", "device_id": "D-18"},
        },
    )
    # 扁平对象里的 device_id 属于 global 层，函数作用域仍然缺
    assert flat.status_code == 400, flat.text


def test_instance_fills_release_defaults(client, tmp_path):
    """启动时先补发布版本冻结的默认值再校验必填；函数局部默认值不泄漏到 global。"""
    register_input_project(client, tmp_path, name="输入工程3")
    doc = input_doc(
        "输入工程3",
        [
            {"name": "fault_time", "scope": "global"},
            {"name": "chamber", "scope": "global"},
            {"name": "device_id", "scope": "function", "function": "check"},
            {"name": "retry", "scope": "function", "function": "check", "default": 3},
        ],
    )
    flow_id, release = seed_published(client, doc)

    frozen = {
        item["name"]: item
        for item in client.get(f"/api/releases/{release['id']}").json()["inputs"]
    }
    # 签名默认值 chamber='A1' 冻结下来，不算必填
    assert frozen["chamber"]["required"] is False
    assert frozen["chamber"]["default"] == "A1"
    # 作者在设计期填的默认值优先于签名默认值
    assert frozen["retry"]["required"] is False
    assert frozen["retry"]["default"] == 3

    started = client.post(
        f"/api/flows/{flow_id}/instances",
        json={
            "releaseId": release["id"],
            "input": {
                "global": {"fault_time": "2026-03-01T09:00:00"},
                "scope": {"check": {"device_id": "D-9"}},
            },
        },
    )
    assert started.status_code == 200, started.text
    payload = client.get(f"/api/instances/{started.json()['id']}").json()["snapshot"]["payload"]
    assert payload["global"] == {"fault_time": "2026-03-01T09:00:00", "chamber": "A1"}
    assert payload["scope"]["check"] == {"device_id": "D-9", "retry": 3}

    # 请求里给了值就以请求为准，默认不覆盖用户输入
    override = client.post(
        f"/api/flows/{flow_id}/instances",
        json={
            "releaseId": release["id"],
            "input": {
                "global": {"fault_time": "2026-03-02T09:00:00", "chamber": "B2"},
                "scope": {"check": {"device_id": "D-10", "retry": 5}},
            },
        },
    )
    assert override.status_code == 200, override.text
    payload = client.get(f"/api/instances/{override.json()['id']}").json()["snapshot"]["payload"]
    assert payload["global"]["chamber"] == "B2"
    assert payload["scope"]["check"]["retry"] == 5


def test_probe_returns_function_signatures(client, tmp_path):
    register_input_project(client, tmp_path, name="探测工程")
    flow = create_flow(client)
    doc = input_doc("探测工程")

    response = client.post(f"/api/flows/{flow['id']}/probe", json={"doc": doc})
    assert response.status_code == 200, response.text
    probed = response.json()
    assert "探测工程:check" in probed
    params = {item["name"]: item for item in probed["探测工程:check"]}
    assert params["fault_time"]["required"] is True
    assert params["fault_time"]["type"] == "datetime"
    assert params["retry"]["required"] is False

    # 探测失败按 {"error"} 回给面板，不冒 500（工程未登记时错误挂在工程名上）
    unknown = client.post(
        f"/api/flows/{flow['id']}/probe",
        json={"doc": input_doc("没登记的工程")},
    )
    assert unknown.status_code == 200, unknown.text
    reported = unknown.json()
    failed = reported.get("没登记的工程") or reported.get("没登记的工程:check")
    assert isinstance(failed, dict) and failed.get("error"), reported

    assert client.post("/api/flows/missing/probe", json={"doc": doc}).status_code == 404


# ---------------------------------------------------------------- 老库迁移


def test_init_db_migrates_old_database(tmp_path, monkeypatch):
    """只有旧列的库，启动后必须能直接查询，不能被 no such column 打死。"""
    import sqlite3

    from fastapi.testclient import TestClient

    db_path = tmp_path / "old.db"
    monkeypatch.delenv("NEWDIAG_DATA_DIR", raising=False)
    monkeypatch.setenv("NEWDIAG_DB", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE flows (
            id VARCHAR(32) PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            mode VARCHAR(16) NOT NULL DEFAULT 'parallel',
            owner_id VARCHAR(64) NOT NULL DEFAULT 'local',
            current_revision_id VARCHAR(32),
            editing_by VARCHAR(64),
            editing_revision_id VARCHAR(32),
            editing_since VARCHAR(32),
            created_at VARCHAR(32) NOT NULL,
            updated_at VARCHAR(32) NOT NULL
        );
        CREATE TABLE releases (
            id VARCHAR(32) PRIMARY KEY,
            flow_id VARCHAR(32) NOT NULL,
            revision_id VARCHAR(32) NOT NULL,
            version_no INTEGER NOT NULL DEFAULT 1,
            plan_json TEXT NOT NULL DEFAULT '{}',
            signature_json TEXT NOT NULL DEFAULT '{}',
            published_by VARCHAR(64) NOT NULL DEFAULT 'local',
            published_at VARCHAR(32) NOT NULL,
            note TEXT
        );
        INSERT INTO flows (id, name, description, mode, owner_id, created_at, updated_at)
        VALUES ('f1', '老流程', '', 'parallel', 'local', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO releases (id, flow_id, revision_id, version_no, published_at)
        VALUES ('r1', 'f1', 'rev1', 1, '2026-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    from app import db

    db.configure()
    db.init_db()
    db.init_db()  # 幂等：重复启动不能报错

    from app.main import app

    with TestClient(app) as client:
        items = client.get("/api/flows").json()
        assert [item["id"] for item in items] == ["f1"]
        assert items[0]["folderId"] is None

        # 老版本没有启动表单列，读出空数组而不是报错
        release = client.get("/api/releases/r1").json()
        assert release["inputs"] == []

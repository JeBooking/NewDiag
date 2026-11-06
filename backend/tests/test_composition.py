"""组合配置的编译与运行语义；不依赖外部工程时全部用假实现。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.compiler import ValidationError, compile_doc, validate_doc
from app.services.composition import CompositionEngine, CompositionFailure, CompositionState


def composition_doc(**overrides) -> dict:
    doc = {
        "format": 1,
        "name": "组合诊断",
        "mode": "parallel",
        "composition": {
            "objects": [
                {"id": "client", "project": "MockDataService", "module": "dataclient",
                 "entry": "DataClient", "args": {"root": {"system": "directory"}}},
            ],
            "resources": [
                {"id": "main", "trigger": "start", "steps": [
                    {"id": "fetch", "project": "MockDataService", "module": "dataclient",
                     "kind": "method", "target": "client", "function": "fetch",
                     "args": {"device_id": {"input": "device"}}, "output": "file"},
                    {"id": "dataset", "project": "PlotEngine", "module": "dataset",
                     "kind": "function", "function": "Dataset.load",
                     "args": {"path": {"step": "fetch"}}, "output": "object",
                     "saveAs": "dataset"},
                ]},
            ],
            "operations": {
                "check": {
                    "parameters": ["metric"],
                    "steps": [
                        {"id": "series", "project": "PlotEngine", "module": "dataset",
                         "kind": "method", "target": "dataset", "function": "plot_series",
                         "args": {"device": {"input": "device"}, "metric": {"param": "metric"}}},
                        {"id": "chart", "project": "PlotEngine", "module": "plot_window",
                         "kind": "function", "function": "save_plot",
                         "args": {"data": {"step": "series"},
                                  "path": {"system": "file", "name": "chart.png"}},
                         "output": "image"},
                        {"id": "judge", "project": "JudgeEngine", "module": "judgekit",
                         "kind": "constructor", "function": "JudgeEngine",
                         "args": {"thresholds": {"fields": {"peak": {"input": "peak"}}}},
                         "output": "object", "saveAs": "judge"},
                        {"id": "verdicts", "project": "JudgeEngine", "module": "judgekit",
                         "kind": "method", "target": "judge", "function": "judge_series",
                         "args": {"item": {"step": "series"}}, "output": "object"},
                        {"id": "summary", "project": "JudgeEngine", "module": "judgekit",
                         "kind": "method", "target": "judge", "function": "summary",
                         "args": {"verdicts": {"step": "verdicts"}}},
                    ],
                    "result": {"step": "summary", "outcome": "overall", "reason": "reasons",
                               "normalReason": "未触发"},
                    "presentation": [
                        {"type": "image", "step": "chart", "caption": "曲线"},
                        {"type": "table", "step": "summary", "path": "verdicts",
                         "columns": ["label", "reason"]},
                    ],
                },
            },
        },
        "nodes": [
            {"id": "s1", "kind": "start", "title": "开始", "detail": "",
             "inputs": [{"name": "device", "scope": "global", "type": "text"},
                        {"name": "peak", "scope": "global", "type": "number", "default": 12}],
             "position": {"x": 0, "y": 0}},
            {"id": "n1", "kind": "investigation", "title": "振动", "detail": "看振动",
             "fields": {"observation": "振动", "acquisition": "取数", "assessment": "超阈值"},
             "operation": "check", "operationArguments": {"metric": {"lit": "vibration"}},
             "position": {"x": 1, "y": 1}},
            {"id": "c1", "kind": "conclusion", "title": "异常", "detail": "异常",
             "position": {"x": 2, "y": 0}},
            {"id": "r1", "kind": "remediation", "title": "处置", "detail": "停机",
             "position": {"x": 3, "y": 0}},
            {"id": "p1", "kind": "problemSolved", "title": "完成", "detail": "完成",
             "position": {"x": 4, "y": 0}},
            {"id": "e1", "kind": "escalation", "title": "上升", "detail": "上升",
             "position": {"x": 2, "y": 2}},
        ],
        "edges": [
            {"id": "e0", "from": "s1", "to": "n1"},
            {"id": "e1", "from": "n1", "to": "c1", "branch": "yes"},
            {"id": "e2", "from": "n1", "to": "e1", "branch": "no"},
            {"id": "e3", "from": "c1", "to": "r1"},
            {"id": "e4", "from": "r1", "to": "p1"},
        ],
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------- 编译


def test_compile_composition_plan():
    doc = composition_doc()
    assert validate_doc(doc) == []
    plan = compile_doc(doc, "f1", 1)
    composition = plan["composition"]
    assert composition["startup"] == ["main"]
    assert composition["resources"]["main"]["steps"][1]["saveAs"] == "dataset"
    assert composition["operations"]["check"]["parameters"] == ["metric"]
    # 实例方法按对象声明的类名展开成可探测的接口名
    assert composition["operations"]["check"]["steps"][0]["interface"] == "Dataset.plot_series"
    assert plan["steps"]["s1"]["composeResources"] == ["main"]
    assert plan["steps"]["n1"]["composeOperation"] == "check"
    assert plan["steps"]["n1"]["operationArgs"] == {"metric": {"lit": "vibration"}}
    assert "human" not in plan["steps"]["n1"]


def test_compile_rejects_unknown_parameter():
    doc = composition_doc()
    doc["nodes"][1]["operationArguments"] = {"other": {"lit": 1}}
    issues = validate_doc(doc)
    messages = " ".join(item["message"] for item in issues)
    assert "缺少参数：metric" in messages
    assert "未声明参数：other" in messages


def test_compile_rejects_forward_step_reference():
    doc = composition_doc()
    steps = doc["composition"]["resources"][0]["steps"]
    steps[0]["args"]["device_id"] = {"step": "dataset"}
    issues = validate_doc(doc)
    assert any("尚未执行的步骤" in item["message"] for item in issues)


def test_compile_rejects_duplicate_object_name():
    doc = composition_doc()
    doc["composition"]["operations"]["check"]["steps"][2]["saveAs"] = "client"
    issues = validate_doc(doc)
    assert any("对象名重复" in item["message"] for item in issues)


def test_compile_rejects_mixing_diagnosis():
    doc = composition_doc()
    doc["diagnosis"] = {"project": "P", "function": "C", "protocol": "diagnosis/v1",
                        "version": "1", "config": {}}
    issues = validate_doc(doc)
    assert any("不能同时使用" in item["message"] for item in issues)


def test_compile_checks_signatures_when_probe_given():
    doc = composition_doc()
    functions = {
        "MockDataService:dataclient:DataClient": [
            {"name": "root", "type": "any", "required": False, "default": None},
        ],
        "MockDataService:dataclient:DataClient.fetch": [
            {"name": "device_id", "type": "any", "required": True, "default": None},
        ],
        "PlotEngine:dataset:Dataset.load": [
            {"name": "path", "type": "any", "required": False, "default": None},
        ],
        "PlotEngine:dataset:Dataset.plot_series": [
            {"name": "device", "type": "any", "required": False, "default": None},
            {"name": "metric", "type": "any", "required": False, "default": None},
        ],
        "PlotEngine:plot_window:save_plot": [
            {"name": "data", "type": "any", "required": True, "default": None},
            {"name": "path", "type": "any", "required": True, "default": None},
        ],
        "JudgeEngine:judgekit:JudgeEngine": [
            {"name": "rules", "type": "any", "required": False, "default": None},
            {"name": "thresholds", "type": "any", "required": False, "default": None},
        ],
        "JudgeEngine:judgekit:JudgeEngine.judge_series": [
            {"name": "item", "type": "any", "required": True, "default": None},
        ],
        "JudgeEngine:judgekit:JudgeEngine.summary": [
            {"name": "verdicts", "type": "any", "required": True, "default": None},
        ],
    }
    assert validate_doc(doc, functions) == []

    broken = dict(functions)
    broken["JudgeEngine:judgekit:JudgeEngine.summary"] = [
        {"name": "verdicts", "type": "any", "required": True, "default": None},
        {"name": "extra", "type": "any", "required": True, "default": None},
    ]
    issues = validate_doc(doc, broken)
    assert any("缺少必填参数：extra" in item["message"] for item in issues)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWDIAG_DATA_DIR", str(tmp_path / "data"))


# ---------------------------------------------------------------- 运行


class FakeBridge:
    """用固定返回值模拟工程进程，验证表达式与结果映射。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.objects: dict[str, object] = {}

    def project(self, name):
        return SimpleNamespace(name=name) if name else None

    def __call__(self, project, payload):
        self.calls.append({"project": project.name, **payload})
        action = payload.get("action")
        if action == "export":
            return {"value": {"kind": "value", "value": {"overall": "yes",
                                                         "reasons": ["峰值超限"]}}}
        function = payload["call"]["function"]
        if function == "DataClient":
            self.objects[payload["handle"]] = object()
            return {"value": {"kind": "handle", "handle": payload["handle"]},
                    "preview": {"$object": "DataClient"}}
        if function == "fetch":
            path = Path(payload["outputDir"]) / "data.json"
            path.write_text('{"series": []}', encoding="utf-8")
            return {"value": {"kind": "file", "path": str(path), "name": path.name,
                              "size": 14, "sha256": "0" * 64}}
        if function == "Dataset.load":
            self.objects[payload["handle"]] = object()
            return {"value": {"kind": "handle", "handle": payload["handle"]},
                    "preview": {"$object": "Dataset"}}
        if function == "plot_series":
            return {"value": {"kind": "value", "value": {"x": [0, 1], "y": [1, 2]}}}
        if function == "save_plot":
            from app.services import storage

            digest, _ = storage.store(b"\x89PNG\r\n\x1a\nfake")
            return {"value": {"kind": "image", "hash": digest}}
        if function == "JudgeEngine" or function.endswith("Rule"):
            self.objects[payload["handle"]] = object()
            return {"value": {"kind": "handle", "handle": payload["handle"]},
                    "preview": {"$object": function}}
        if function == "judge_series":
            self.objects.setdefault(payload["handle"], object())
            return {"value": {"kind": "handle", "handle": payload["handle"]},
                    "preview": {"$object": "list"}}
        if function == "summary":
            return {"value": {"kind": "value", "value": {
                "overall": "yes", "reasons": ["实测 13.05 > 阈值 12"],
                "verdicts": [{"rule": "peak", "label": "峰值超阈值", "result": "yes",
                              "reason": "实测 13.05 > 阈值 12"}],
            }}}
        raise AssertionError(f"未模拟的接口 {function}")


def make_engine(tmp_path, bridge):
    doc = composition_doc()
    plan = compile_doc(doc, "f1", 1)
    return CompositionEngine(
        plan["composition"], CompositionState(),
        inputs={"device": "D001", "peak": 12},
        artifact_root=tmp_path,
        project_of=bridge.project,
        call=bridge,
    )


def test_resource_is_cached_and_file_crosses_projects(tmp_path):
    bridge = FakeBridge()
    engine = make_engine(tmp_path, bridge)
    first = engine.run_resources(["main"])
    second = engine.run_resources(["main"])
    assert first["main"] is second["main"]
    fetch_calls = [call for call in bridge.calls if call["call"]["function"] == "fetch"]
    assert len(fetch_calls) == 1
    # 跨工程：文件以本地路径传递，对象留在原工程
    dataset_call = next(call for call in bridge.calls
                        if call["call"]["function"] == "Dataset.load")
    assert "files" in dataset_call["args"]["path"]
    assert Path(dataset_call["args"]["path"]).is_file()
    assert first["main"]["dataset"]["kind"] == "handle"


def test_operation_maps_decision_and_presentation(tmp_path):
    bridge = FakeBridge()
    engine = make_engine(tmp_path, bridge)
    engine.run_resources(["main"])
    params = engine.resolve_params({"metric": {"lit": "vibration"}})
    outcome = engine.run_operation("check", params)
    assert outcome["decision"]["branch"] == "yes"
    assert outcome["decision"]["reason"] == "实测 13.05 > 阈值 12"
    blocks = outcome["presentation"]["blocks"]
    assert blocks[0]["type"] == "image" and blocks[0]["caption"] == "曲线"
    assert len(blocks[0]["src"]) == 64
    assert blocks[1]["type"] == "table"
    assert blocks[1]["rows"] == [["峰值超阈值", "实测 13.05 > 阈值 12"]]


def test_same_project_object_stays_a_handle(tmp_path):
    bridge = FakeBridge()
    engine = make_engine(tmp_path, bridge)
    engine.run_resources(["main"])
    params = engine.resolve_params({"metric": {"lit": "vibration"}})
    engine.run_operation("check", params)
    judge_call = next(call for call in bridge.calls
                      if call["call"]["function"] == "judge_series")
    assert judge_call["args"]["item"] == {"x": [0, 1], "y": [1, 2]}
    summary_call = next(call for call in bridge.calls if call["call"]["function"] == "summary")
    assert summary_call["args"]["verdicts"] == {"$handle": "operation:check:verdicts"}


def test_export_turns_objects_into_values(tmp_path):
    bridge = FakeBridge()
    engine = make_engine(tmp_path, bridge)
    engine.run_resources(["main"])
    dataset = engine.state.resources["main"]["dataset"]
    assert dataset["kind"] == "handle"
    exported = engine._export(dataset)
    assert exported["kind"] == "value"
    assert exported["value"]["overall"] == "yes"


@pytest.mark.skipif(
    not all(Path(path).is_dir() for path in
            (r"D:\FILE\Projects\MockDataService", r"D:\FILE\Projects\PlotEngine",
             r"D:\FILE\Projects\JudgeEngine")),
    reason="本机没有示例用到的三个工程",
)
def test_example_runs_end_to_end(tmp_path):
    script = BACKEND_DIR.parent / "examples" / "composed-diagnosis" / "run.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--device", "D002", "--activity", "空载",
         "--data-dir", str(tmp_path / "data")],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
        cwd=str(BACKEND_DIR.parent),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "实例状态：success" in completed.stdout
    assert "n1: no" in completed.stdout
    assert "n2: no" in completed.stdout


@pytest.mark.skipif(
    not all(Path(path).is_dir() for path in
            (r"D:\FILE\Projects\MockDataService", r"D:\FILE\Projects\PlotEngine",
             r"D:\FILE\Projects\JudgeEngine")),
    reason="本机没有示例用到的三个工程",
)
def test_example_supports_manual_judgment_with_chart(tmp_path):
    script = BACKEND_DIR.parent / "examples" / "composed-diagnosis" / "run.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--device", "D002", "--activity", "空载",
         "--manual", "--answer", "no", "--data-dir", str(tmp_path / "data")],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
        cwd=str(BACKEND_DIR.parent),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "实例状态：success" in completed.stdout
    assert completed.stdout.count("振动是否异常 → no") == 1
    assert completed.stdout.count("温度是否偏高 → no") == 1

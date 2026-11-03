"""外部节点适配协议；只依赖标准库，可随业务工程独立分发。"""

from __future__ import annotations

import inspect
import json
import logging
import time
from datetime import datetime
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)
PROTOCOL = "investigation/v1"


class Node(ABC):
    """业务对象不跨调用保存，必要状态必须进入输入或返回值。"""

    version = "1.0.0"


class InvestigationNode(Node):
    protocol = PROTOCOL
    acquire = None
    judge = None
    # 未提供 acquire 时，仍可声明时间点等节点启动参数。
    inputs: list[dict] = []
    manual_fields: list[dict] = []

    @abstractmethod
    def parse(self, raw_data):
        raise NotImplementedError

    @abstractmethod
    def present(self, parsed_data, decision=None) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def dump_raw(self, value):
        return value

    def dump_parsed(self, value):
        return value

    def load_parsed(self, value):
        return value


def is_adapter(obj) -> bool:
    return inspect.isclass(obj) and issubclass(obj, InvestigationNode) and obj is not InvestigationNode


def json_value(value):
    # 禁止 repr 兜底，避免历史记录与下一阶段收到的是两种不同数据。
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def validate_fields(fields: list[dict], data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("人工数据必须是对象")
    known = {field["name"] for field in fields}
    if set(data) - known:
        raise ValueError(f"存在未声明字段：{', '.join(sorted(set(data) - known))}")
    result = {}
    for field in fields:
        name = field["name"]
        value = data.get(name, field.get("default"))
        if value is None or value == "":
            if field.get("required", True):
                raise ValueError(f"{field.get('label', name)}不能为空")
            continue
        kind = field.get("type", "text")
        valid = {
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "text": isinstance(value, str),
            "datetime": isinstance(value, str),
            "json": True,
            "file": isinstance(value, dict) and isinstance(value.get("hash"), str),
        }.get(kind, False)
        if not valid:
            raise ValueError(f"{field.get('label', name)}类型必须为 {kind}")
        if kind == "number":
            if "min" in field and value < field["min"]:
                raise ValueError(f"{name}不得小于 {field['min']}")
            if "max" in field and value > field["max"]:
                raise ValueError(f"{name}不得大于 {field['max']}")
        if kind == "datetime":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"{name}必须是 ISO8601 时间") from exc
            if parsed.tzinfo is None:
                raise ValueError(f"{name}必须包含时区，例如 +08:00")
        result[name] = value
    return json_value(result)


def describe_adapter(cls) -> dict:
    if inspect.isabstract(cls):
        raise ValueError(f"{cls.__name__} 必须实现 parse 和 present")
    if not isinstance(cls.version, str) or not cls.version.strip():
        raise ValueError("适配器必须声明 version")
    fields = json_value(cls.manual_fields)
    if not isinstance(fields, list) or not isinstance(cls.inputs, list):
        raise ValueError("manual_fields 和 inputs 必须为数组")
    input_names = set()
    for item in cls.inputs:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
            raise ValueError("inputs 每项必须声明非空 name")
        if item["name"] in input_names or item.get("type", "text") not in ("text", "number", "boolean", "datetime"):
            raise ValueError("inputs 不允许重复字段或不支持的类型")
        input_names.add(item["name"])
    names = set()
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str):
            raise ValueError("manual_fields 每项必须声明 name")
        if not field["name"] or field["name"] in names:
            raise ValueError("manual_fields 字段名不能为空或重复")
        names.add(field["name"])
        if field.get("type", "text") not in ("text", "number", "boolean", "datetime", "json", "file"):
            raise ValueError("manual_fields 包含不支持的字段类型")
    if cls.acquire is None and not fields:
        raise ValueError("未实现 acquire 时必须声明 manual_fields")
    for method in ("acquire", "judge"):
        if getattr(cls, method) is not None and not callable(getattr(cls, method)):
            raise ValueError(f"{method} 必须为方法或 None")
    for name, count in (("parse", 1), ("present", 2), ("judge", 1),
                        ("dump_raw", 1), ("dump_parsed", 1), ("load_parsed", 1), ("close", 0)):
        method = getattr(cls, name)
        if method is None and name == "judge":
            continue
        if not callable(method) or inspect.iscoroutinefunction(method):
            raise ValueError(f"{name} 必须是同步方法")
        descriptor = inspect.getattr_static(cls, name)
        extra = 0 if isinstance(descriptor, (staticmethod, classmethod)) else 1
        try:
            inspect.signature(method).bind(*([None] * (count + extra)))
        except TypeError as exc:
            raise ValueError(f"{name} 的参数不符合四阶段协议：{exc}") from exc
    if cls.acquire is not None:
        if inspect.iscoroutinefunction(cls.acquire):
            raise ValueError("acquire 必须是同步方法")
        for param in inspect.signature(cls.acquire).parameters.values():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD, param.POSITIONAL_ONLY):
                raise ValueError("acquire 参数必须显式声明且支持关键字传参")
    config = []
    for param in inspect.signature(cls).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD, param.POSITIONAL_ONLY):
            raise ValueError("适配器构造参数必须显式声明且支持关键字传参")
        item = {"name": param.name, "required": param.default is param.empty}
        if param.default is not param.empty:
            item["default"] = json_value(param.default)
        config.append(item)
    return {
        "protocol": PROTOCOL,
        "version": cls.version,
        "configParams": config,
        "manualFields": fields,
        "automaticAcquisition": cls.acquire is not None,
        "automaticJudgment": cls.judge is not None,
    }


def presentation(value) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("blocks"), list):
        raise ValueError("present 必须返回 {blocks: [...]} 展示描述")
    for block in value["blocks"]:
        if not isinstance(block, dict) or block.get("type") not in ("text", "metrics", "table", "line"):
            raise ValueError("展示块仅支持 text、metrics、table、line")
        kind = block["type"]
        if kind == "text" and not isinstance(block.get("text"), str):
            raise ValueError("text 展示块需要 text 字符串")
        if kind == "metrics" and (
            not isinstance(block.get("items"), list)
            or any(not isinstance(x, dict) or "label" not in x or "value" not in x for x in block["items"])
        ):
            raise ValueError("metrics 展示块需要 items: [{label, value, unit?}]")
        if kind == "table" and (
            not isinstance(block.get("columns"), list)
            or any(not isinstance(x, str) for x in block["columns"])
            or not isinstance(block.get("rows"), list)
            or any(not isinstance(x, list) or len(x) != len(block["columns"]) for x in block["rows"])
        ):
            raise ValueError("table 展示块需要 columns 字符串数组及等宽 rows 数组")
        if kind == "line" and (
            not isinstance(block.get("values"), list)
            or not block["values"]
            or any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in block["values"])
        ):
            raise ValueError("line 展示块需要非空数字数组 values")
    return json_value(value)


def execute_adapter(cls, request: dict) -> dict:
    meta = describe_adapter(cls)
    if request.get("version") != meta["version"]:
        raise ValueError("适配器版本与发布版本不一致，请重新发布流程")
    config = request.get("config") or {}
    inspect.signature(cls).bind(**config)
    state = json_value(request.get("state") or {"inputs": request.get("inputs") or {}, "stages": []})
    operation = request.get("operation", "start")
    if operation == "start" and cls.acquire is None:
        return {"status": "acquisition", "state": state, "fields": meta["manualFields"]}
    obj = cls(**config)

    def invoke(stage, func, *args, **kwargs):
        started = time.perf_counter()
        logger.info("适配器 %s@%s 阶段 %s 开始", cls.__name__, cls.version, stage)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            raise ValueError(f"{stage}: {exc}") from exc
        state["stages"].append({"stage": stage, "durationMs": round((time.perf_counter() - started) * 1000)})
        return result

    try:
        if operation in ("start", "acquisition"):
            raw = (
                invoke("acquire", obj.acquire, **state["inputs"])
                if operation == "start"
                else validate_fields(meta["manualFields"], request.get("data"))
            )
            if operation == "acquisition":
                state["stages"].append({"stage": "manualAcquisition"})
            state["raw"] = json_value(invoke("dumpRaw", obj.dump_raw, raw))
            parsed = invoke("parse", obj.parse, raw)
            state["parsed"] = json_value(invoke("dumpParsed", obj.dump_parsed, parsed))
            state["presentation"] = presentation(invoke("present", obj.present, parsed, None))
            if obj.judge is None:
                return {"status": "judgment", "state": state}
            decision = invoke("judge", obj.judge, parsed)
        elif operation == "judgment":
            parsed = invoke("loadParsed", obj.load_parsed, state["parsed"])
            decision = request.get("decision")
            state["stages"].append({"stage": "manualJudgment"})
        else:
            raise ValueError("未知的适配器执行阶段")
        if not isinstance(decision, dict) or decision.get("result") not in ("yes", "no"):
            raise ValueError("judge 必须返回 {result: 'yes'|'no', reason: 字符串}")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise ValueError("判断必须提供非空 reason")
        decision = {"result": decision["result"], "reason": decision["reason"]}
        state["decision"] = decision
        state["presentation"] = presentation(invoke("presentResult", obj.present, parsed, decision))
        return {"status": "complete", "state": state}
    finally:
        try:
            obj.close()
        except Exception:
            logger.exception("适配器 %s 资源关闭失败", cls.__name__)

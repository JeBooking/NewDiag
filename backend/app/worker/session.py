"""会话子进程：常驻一个业务工程实例，按 JSON 行接收调用。

用法：python app/worker/session.py <config.json>
config: {"path": ..., "module": ..., "factory": ...}

协议（stdin/stdout，每行一个 JSON）：
请求 {"id": "1", "function": "judge", "args": {...}}
响应 {"id": "1", "ok": true, "value": ...} / {"id": "1", "ok": false, "error": "..."}
关闭 {"id": "0", "op": "shutdown"}
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import sys
import traceback
from contextlib import redirect_stdout

if __package__:
    from . import node_sdk
    sys.modules.setdefault("node_sdk", node_sdk)
else:
    import node_sdk

execute_adapter = node_sdk.execute_adapter
is_adapter = node_sdk.is_adapter
json_value = node_sdk.json_value

MAX_DEPTH = 5


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        _write({"id": None, "ok": False, "error": "缺少 config 路径参数"})
        return 2
    with open(sys.argv[1], "r", encoding="utf-8-sig") as fh:
        config = json.load(fh)

    with redirect_stdout(sys.stderr):
        target = _bootstrap(config)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("请求必须是 JSON 对象")
            request_id = request.get("id")
            if request.get("op") == "shutdown":
                _write({"id": request_id, "ok": True, "value": None})
                break
            function_name = request.get("function")
            args = request.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError("args 必须是对象")
            with redirect_stdout(sys.stderr):
                if function_name == "__newdiag_pipeline__":
                    cls = _resolve_callable(target, args["adapter"])
                    if not is_adapter(cls):
                        raise TypeError("目标不是 InvestigationNode 适配器类")
                    value = json_value(execute_adapter(cls, args))
                elif function_name == "__newdiag_function__":
                    if __package__:
                        from .probe import _describe
                    else:
                        from probe import _describe
                    func = _resolve_callable(target, args["function"])
                    if inspect.iscoroutinefunction(func):
                        raise TypeError("四阶段函数必须为同步函数")
                    actual = _describe(args["function"], func)["params"]
                    fields = ("name", "type", "required", "default")
                    def comparable(params):
                        return [{key: item.get(key) for key in fields} for item in params]
                    if comparable(actual) != comparable(args["signature"]):
                        raise ValueError("函数签名与发布计划不一致，请重新发布")
                    value = json_value(func(**args["arguments"]))
                else:
                    func = _resolve_callable(target, function_name)
                    value = _json_safe(func(**args))
            _write({"id": request_id, "ok": True, "value": value})
        except BaseException:  # noqa: BLE001 - 业务异常不能带崩子进程
            _write({"id": request_id, "ok": False, "error": traceback.format_exc()})
    return 0


def _bootstrap(config: dict):
    path = config.get("path") or ""
    if path:
        os.chdir(path)
        if path not in sys.path:
            sys.path.insert(0, path)
    module_name = config.get("module") or ""
    if not module_name:
        raise ValueError("module 为空")
    module = importlib.import_module(module_name)
    factory = config.get("factory") or ""
    if not factory:
        return module
    obj = module
    for part in str(factory).split("."):
        obj = getattr(obj, part)
    if inspect.isclass(obj):
        return obj()
    if callable(obj):
        return obj()
    raise TypeError(f"factory 不可调用：{factory}")


def _resolve_callable(target, name: str):
    if not name:
        raise ValueError("function 为空")
    obj = target
    for part in str(name).split("."):
        if isinstance(obj, dict):
            obj = obj[part]
        else:
            obj = getattr(obj, part)
    if not callable(obj):
        raise TypeError(f"{name} 不是可调用对象")
    return obj


def _json_safe(value, depth: int = 0):
    """把业务返回值投影成 JSON；不可序列化对象保留类型与 repr 便于排查。"""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else repr(value)
    if depth >= MAX_DEPTH:
        return _opaque(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item, depth + 1) for item in value]
    return _opaque(value)


def _opaque(value) -> dict:
    return {"__type__": type(value).__name__, "repr": repr(value)}


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=repr) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.exit(main())
    except BaseException:  # noqa: BLE001 - 启动失败也要给父进程一个可解析的响应
        _write({"id": None, "ok": False, "error": traceback.format_exc()})
        sys.exit(1)

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
import os
import sys
import traceback

MAX_DEPTH = 5


def main() -> int:
    if len(sys.argv) < 2:
        _write({"id": None, "ok": False, "error": "缺少 config 路径参数"})
        return 2
    with open(sys.argv[1], "r", encoding="utf-8-sig") as fh:
        config = json.load(fh)

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
            func = _resolve_callable(target, function_name)
            value = func(**args)
            _write({"id": request_id, "ok": True, "value": _json_safe(value)})
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

"""组合运行的工程内对象注册表：按平台下发的调用执行，对象留在本进程。

平台只下发接口名和参数，不生成代码；这里负责把公开函数、构造器和实例方法
按同一套规则调用，并把返回值分成「值 / 对象句柄 / 文件 / 图片」四类回传。
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
from pathlib import Path

if __package__:
    from . import node_sdk
else:
    import node_sdk

logger = logging.getLogger(__name__)

# 运行记录里能直接读的预览上限；超过就只留字节数，避免把整份数据塞进 SQLite。
MAX_PREVIEW_CHARS = 4000
HANDLE_KEY = "$handle"

_CALL_KINDS = ("constructor", "function", "method")
_OUTPUTS = ("auto", "value", "object", "file", "files", "image")


class CompositionRuntime:
    """一个业务进程内的对象表；句柄只在当前进程、当前实例内有效。"""

    def __init__(self, default_module: str = "") -> None:
        self.objects: dict[str, object] = {}
        self.default_module = default_module or ""
        self.closed = False

    # ------------------------------------------------------------------ 入口

    def execute(self, request: dict) -> dict:
        action = str(request.get("action") or "call")
        if action == "call":
            return self._call(request)
        if action == "export":
            return self._export(request)
        raise ValueError(f"未知的组合操作：{action}")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for handle in reversed(list(self.objects)):
            value = self.objects.pop(handle, None)
            close = getattr(value, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception:  # noqa: BLE001 - 关闭失败不能盖住正常退出
                logger.exception("组合对象关闭失败 handle=%s", handle)

    # ------------------------------------------------------------------ 调用

    def _call(self, request: dict) -> dict:
        call = request.get("call") or {}
        if not isinstance(call, dict):
            raise ValueError("调用描述必须是对象")
        kind = str(call.get("kind") or "function")
        if kind not in _CALL_KINDS:
            raise ValueError(f"不支持的调用类型：{kind}")

        function = self._resolve(kind, call, request.get("module") or self.default_module)
        if inspect.iscoroutinefunction(function):
            raise ValueError("组合调用只支持同步接口")
        arguments = _decode(request.get("args") or {}, self.objects)
        try:
            inspect.signature(function).bind(**arguments)
        except TypeError as exc:
            raise ValueError(f"{_label(call)} 的参数与接口签名不匹配：{exc}") from exc

        logger.info("组合调用 %s 参数 %s", _label(call), sorted(arguments))
        value = function(**arguments)
        output = str(request.get("output") or "auto")
        if output not in _OUTPUTS:
            raise ValueError(f"不支持的输出类型：{output}")
        reference, native = self._classify(value, output, request)
        return {"value": reference, "preview": _preview(native, reference)}

    def _resolve(self, kind: str, call: dict, fallback_module):
        name = str(call.get("function") or "").strip()
        if not name:
            raise ValueError("调用缺少接口名")
        if kind == "method":
            handle = str(call.get("target") or "")
            if not handle:
                raise ValueError(f"调用 {name} 缺少对象")
            owner = self.objects.get(handle)
            if owner is None:
                raise ValueError(f"对象 {handle} 不在当前业务进程内，句柄已失效，请重新执行该路径")
            if name.startswith("_") or "." in name:
                raise ValueError("对象调用只能选择公开方法")
            function = getattr(owner, name, None)
            if not callable(function):
                raise TypeError(f"{type(owner).__name__} 没有可调用的公开方法 {name}")
            return function

        module_name = str(call.get("module") or fallback_module or "")
        if not module_name:
            raise ValueError("调用缺少模块名")
        module = importlib.import_module(module_name)
        target = module
        for part in name.split("."):
            if part.startswith("_"):
                raise ValueError("只能调用公开接口")
            target = getattr(target, part) if target is not None else None
            if target is None:
                raise AttributeError(f"{module_name} 里没有公开接口 {name}")
        if kind == "constructor":
            if not inspect.isclass(target):
                raise TypeError(f"{name} 不是类，不能作为构造器")
        elif not callable(target) or inspect.isclass(target):
            raise TypeError(f"{name} 不是可调用的普通函数")
        return target

    def _classify(self, value, output: str, request: dict) -> tuple[dict, object]:
        if output == "object":
            return self._remember(value, request)
        if output == "file":
            return self._capture_file(value), value
        if output == "files":
            return self._capture_files(value), value
        if output == "image":
            return _capture_image(value), value
        if output == "value":
            return {"kind": "value", "value": node_sdk.json_value(value)}, value

        # auto：先按值处理，失败再按对象/文件收，作者不必为每个接口选类型。
        path = _existing_file(value)
        if path is not None:
            return self._capture_file(path), value
        if isinstance(value, (bytes, bytearray)):
            return _capture_image(bytes(value)), value
        try:
            return {"kind": "value", "value": node_sdk.json_value(value)}, value
        except (TypeError, ValueError):
            return self._remember(value, request)

    def _remember(self, value, request: dict) -> tuple[dict, object]:
        handle = str(request.get("handle") or "")
        if not handle:
            raise ValueError("对象输出缺少句柄名")
        self.objects[handle] = value
        return {"kind": "handle", "handle": handle}, value

    def _capture_file(self, value) -> dict:
        path = _existing_file(value)
        if path is None:
            raise ValueError(
                f"输出类型声明为 file，但返回值不是已存在的文件路径：{_brief(value)}"
            )
        data = path.read_bytes()
        return {
            "kind": "file",
            "name": path.name,
            "size": len(data),
            "sha256": _sha256(data),
            "path": str(path.resolve()),
        }

    def _capture_files(self, value) -> dict:
        if not isinstance(value, (list, tuple)):
            raise ValueError("输出类型声明为 files，但返回值不是路径列表")
        return {"kind": "files", "items": [self._capture_file(item) for item in value]}

    # ------------------------------------------------------------------ 跨工程

    def _export(self, request: dict) -> dict:
        handle = str(request.get("handle") or "")
        if not handle or handle not in self.objects:
            raise ValueError(f"对象 {handle or '(空)'} 不在当前业务进程内，句柄已失效")
        value = self.objects[handle]
        path = _existing_file(value)
        if path is not None:
            return self._capture_file(path)
        if isinstance(value, (bytes, bytearray)):
            return _capture_image(bytes(value))
        try:
            return {"kind": "value", "value": node_sdk.json_value(value)}
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{type(value).__name__} 不能跨工程传递：平台只支持 JSON、文件或图片；"
                f"请把使用它的步骤放在同一个工程内"
            ) from exc


def _decode(value, objects: dict):
    if isinstance(value, dict):
        if set(value) == {HANDLE_KEY}:
            handle = str(value[HANDLE_KEY])
            if handle not in objects:
                raise ValueError(f"对象 {handle} 不在当前业务进程内，句柄已失效，请重新执行该路径")
            return objects[handle]
        return {key: _decode(item, objects) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item, objects) for item in value]
    return value


def _existing_file(value) -> Path | None:
    if isinstance(value, Path):
        return value.resolve() if value.is_file() else None
    if isinstance(value, str) and value and "\n" not in value and len(value) < 4096:
        try:
            path = Path(value)
            return path.resolve() if path.is_absolute() and path.is_file() else None
        except OSError:
            return None
    return None


def _capture_image(value) -> dict:
    if isinstance(value, (bytes, bytearray)):
        return {"kind": "image", "hash": node_sdk.save_file(bytes(value))}
    path = _existing_file(value)
    if path is None:
        raise ValueError(f"输出类型声明为 image，但返回值不是图片路径或 bytes：{_brief(value)}")
    return {"kind": "image", "hash": node_sdk.save_file(path.read_bytes())}


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _preview(native, reference) -> object:
    if reference.get("kind") == "handle":
        return {"$object": type(native).__name__, "repr": repr(native)[:200]}
    if reference.get("kind") == "file":
        return {"$file": reference.get("name"), "size": reference.get("size")}
    if reference.get("kind") == "files":
        return {"$files": len(reference.get("items") or [])}
    if reference.get("kind") == "image":
        return {"$image": reference.get("hash")}
    text = json.dumps(reference.get("value"), ensure_ascii=False, default=repr)
    if len(text) <= MAX_PREVIEW_CHARS:
        return reference.get("value")
    return {"omitted": True, "bytes": len(text.encode("utf-8"))}


def _label(call: dict) -> str:
    name = str(call.get("function") or "")
    target = str(call.get("target") or "")
    return f"{target}.{name}" if target else name


def _brief(value) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        text = repr(value)
    return text[:200]

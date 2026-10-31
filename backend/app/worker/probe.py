"""接口探针：在工程目录里 import 模块，输出公开函数的签名。

用法：python app/worker/probe.py <config.json>
config: {"path": ..., "module": ..., "python": ..., "factory": ...}

为什么用子进程：业务工程可能有自己的依赖与副作用，不能污染 API 进程。
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import sys
import traceback

SKIP_PARAM_KINDS = (
    inspect.Parameter.VAR_POSITIONAL,
    inspect.Parameter.VAR_KEYWORD,
)


def main() -> int:
    if len(sys.argv) < 2:
        _emit({"functions": [], "error": "缺少 config 路径参数"})
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8-sig") as fh:
            config = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - 探针只负责报告
        _emit({"functions": [], "error": f"读取 config 失败：{exc}"})
        return 2

    # 目标模块可能打印东西，stdout 只留 JSON
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        functions = _discover(config)
        payload = {"functions": functions}
    except BaseException as exc:  # noqa: BLE001 - 导入期任何异常都要报回
        payload = {"functions": [], "error": f"{exc}\n{traceback.format_exc()}"}
    finally:
        sys.stdout = real_stdout
    _emit(payload)
    return 0 if "error" not in payload else 1


def _discover(config: dict) -> list[dict]:
    path = config.get("path") or ""
    module_name = config.get("module") or ""
    factory_name = config.get("factory") or ""
    if path and path not in sys.path:
        sys.path.insert(0, path)
    if not module_name:
        raise ValueError("module 为空")
    module = importlib.import_module(module_name)

    if not factory_name:
        return _module_functions(module)

    target = _resolve(module, factory_name)
    if inspect.isclass(target):
        return _class_functions(target)
    if callable(target):
        annotation = inspect.signature(target).return_annotation
        api_class = annotation if inspect.isclass(annotation) else None
        if api_class is not None:
            return _class_functions(api_class)
        return [_describe(factory_name, target)]
    raise TypeError(f"factory 不是可调用对象：{factory_name}")


def _module_functions(module) -> list[dict]:
    names = getattr(module, "__all__", None)
    if names is None:
        names = [
            name
            for name, obj in vars(module).items()
            if not name.startswith("_")
            and callable(obj)
            and getattr(obj, "__module__", None) == module.__name__
            and not inspect.isclass(obj)
        ]
    result = []
    for name in names:
        obj = getattr(module, name, None)
        if callable(obj) and not inspect.isclass(obj):
            result.append(_describe(name, obj))
    result.sort(key=lambda item: item["name"])
    return result


def _class_functions(cls: type) -> list[dict]:
    result = []
    for name in dir(cls):
        if name.startswith("_"):
            continue
        obj = inspect.getattr_static(cls, name, None)
        func = obj.__func__ if isinstance(obj, (staticmethod, classmethod)) else obj
        if callable(func):
            result.append(_describe(name, func))
    result.sort(key=lambda item: item["name"])
    return result


def _resolve(module, dotted: str):
    obj = module
    for part in dotted.split("."):
        obj = getattr(obj, part)
        if obj is None:
            raise AttributeError(dotted)
    return obj


def _describe(name: str, func) -> dict:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return {
            "name": name,
            "params": [],
            "returns": "any",
            "error": "无法读取签名",
        }

    params = []
    for param in signature.parameters.values():
        if param.name in ("self", "cls") or param.kind in SKIP_PARAM_KINDS:
            continue
        has_default = param.default is not inspect.Parameter.empty
        params.append(
            {
                "name": param.name,
                "type": _type_name(param.annotation),
                "default": _jsonable(param.default) if has_default else None,
                "required": not has_default,
            }
        )
    return {"name": name, "params": params, "returns": _type_name(signature.return_annotation)}


def _type_name(annotation) -> str:
    if annotation is inspect.Parameter.empty or annotation is inspect.Signature.empty:
        return "any"
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, str):
        return annotation
    if hasattr(annotation, "__name__"):
        return str(annotation.__name__)
    text = str(annotation)
    return text.replace("typing.", "")


def _jsonable(value):
    if value is inspect.Parameter.empty:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


def _emit(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())

"""接口探针：在工程目录里 import 模块，输出公开函数的签名。

用法：python app/worker/probe.py <config.json>
config: {"path": ..., "module": ..., "python": ..., "factory": ...}

为什么用子进程：业务工程可能有自己的依赖与副作用，不能污染 API 进程。
"""

from __future__ import annotations

import importlib
import inspect
import hashlib
import json
import sys
import traceback

if __package__:
    from . import node_sdk
    sys.modules.setdefault("node_sdk", node_sdk)
else:
    import node_sdk

SKIP_PARAM_KINDS = (
    inspect.Parameter.VAR_POSITIONAL,
    inspect.Parameter.VAR_KEYWORD,
)


def main() -> int:
    _force_utf8_stdio()
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

    if config.get('catalog'):
        return _catalog(module)

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
            and callable(obj) and not inspect.isclass(obj)
            and getattr(obj, "__module__", None) == module.__name__
        ]
    result = []
    for name in names:
        obj = getattr(module, name, None)
        if callable(obj) and not inspect.isclass(obj):
            result.append(_describe(name, obj))
    result.sort(key=lambda item: item["name"])
    return result


def _catalog(module):
    names = getattr(module, '__all__', None)
    if names is None:
        names = [name for name, value in vars(module).items()
                 if not name.startswith('_') and getattr(value, '__module__', None) == module.__name__]
    result = []
    for name in names:
        value = getattr(module, name, None)
        if not callable(value) or isinstance(value, type) and issubclass(value, BaseException):
            continue
        entry = _describe(name, value)
        entry['kind'] = 'constructor' if inspect.isclass(value) else 'function'
        result.append(entry)
        if inspect.isclass(value):
            for method in _class_functions(value):
                descriptor = inspect.getattr_static(value, method['name'])
                kind = 'static_method' if isinstance(descriptor, staticmethod) else ('class_method' if isinstance(descriptor, classmethod) else 'instance_method')
                result.append({**method, 'name': name + '.' + method['name'], 'kind': kind})
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
                # repr 兜底的默认值（datetime、枚举、自定义对象）不是真值，调用方不能拿它填参
                "defaultTrusted": bool(has_default and _plain(param.default)),
            }
        )
    try:
        fingerprint = hashlib.sha256(inspect.getsource(func).encode('utf-8')).hexdigest()
    except (OSError, TypeError):
        fingerprint = ''
    return {"name": name, "params": params, "returns": _type_name(signature.return_annotation),
            'doc': inspect.getdoc(func) or '', 'fingerprint': fingerprint,
            'compositionError': '接口必须同步且支持关键字参数' if inspect.iscoroutinefunction(func)
                or any(p.kind == p.POSITIONAL_ONLY for p in signature.parameters.values()) else None,
            'variadicKeywords': any(p.kind == p.VAR_KEYWORD for p in signature.parameters.values())}


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


def _plain(value) -> bool:
    """默认值本身是否可原样传给业务函数；只有走 repr 兜底的对象才不可信。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_plain(item) for item in value)
    if isinstance(value, dict):
        return all(_plain(item) for item in value.values())
    return False


def _emit(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _force_utf8_stdio() -> None:
    """stdio 在解释器启动时按 locale 定死，只能重设流本身；改环境变量已经来不及了。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())

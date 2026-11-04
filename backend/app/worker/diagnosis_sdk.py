"""诊断实例协议：业务类、持久共享数据及首次访问缓存；只依赖标准库。"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
import threading
import time
import traceback
from collections.abc import Mapping
from concurrent.futures import Future
from functools import wraps

if __package__:
    from . import artifacts
    from .node_sdk import json_value, presentation
else:
    import artifacts
    from node_sdk import json_value, presentation

logger = logging.getLogger(__name__)
PROTOCOL = "diagnosis/v1"


class Diagnosis:
    """构造器只建立轻量运行环境；initialize 仅在诊断首次启动时执行。"""

    version = "1.0.0"

    def initialize(self):
        pass

    def close(self):
        pass


def resource(*, version="1"):
    """加载方法包住获取与解析，同一有效请求只在首次访问时执行。"""
    def decorate(loader):
        @wraps(loader)
        def cached(self, *args, **kwargs):
            return self.cache.get(loader, version, args, kwargs)
        cached.resource_version = version
        return cached
    return decorate


def is_diagnosis(value):
    return inspect.isclass(value) and issubclass(value, Diagnosis) and value is not Diagnosis


def _parameters(function, *, skip_self=False):
    result = []
    if inspect.iscoroutinefunction(function):
        raise ValueError("诊断接口必须为同步方法")
    for param in inspect.signature(function).parameters.values():
        if skip_self and param.name in ("self", "cls"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD, param.POSITIONAL_ONLY):
            raise ValueError("诊断接口参数必须显式命名且支持关键字传参")
        item = {"name": param.name, "type": getattr(param.annotation, "__name__", str(param.annotation))
                if param.annotation is not param.empty else "any",
                "required": param.default is param.empty, "defaultTrusted": True}
        item["default"] = None if item["required"] else json_value(param.default)
        result.append(item)
    return result


def describe_diagnosis(cls):
    if not isinstance(cls.version, str) or not cls.version.strip():
        raise ValueError("诊断类必须声明非空 version")
    methods, resources = [], []
    for name in dir(cls):
        if name.startswith("_") or name in ("initialize", "close"):
            continue
        method = getattr(cls, name)
        if not callable(method):
            continue
        entry = {"name": name, "params": _parameters(method, skip_self=True)}
        if hasattr(method, "resource_version"):
            if not isinstance(method.resource_version, str) or not method.resource_version:
                raise ValueError("缓存资源必须声明非空版本")
            resources.append({**entry, "version": method.resource_version})
        else:
            methods.append(entry)
    return {"protocol": PROTOCOL, "version": cls.version,
            "params": _parameters(cls.initialize, skip_self=True),
            "configParams": _parameters(cls), "methods": methods, "resources": resources}


def definition_function(name, meta):
    return {"name": name + ".__definition__", "params": [{
        "name": "definition", "type": json.dumps(meta, ensure_ascii=False, sort_keys=True),
        "required": True,
    }]}


class SharedData(Mapping):
    def __init__(self, root, references=None):
        self.root = root
        self.references = dict(references or {})
        self._values = {}
        self.writable = False

    def __getitem__(self, name):
        if name not in self._values:
            self._values[name] = artifacts.load(self.root, self.references[name])
        # 排查拿到副本，避免修改共享结果使其他节点的结论依赖执行顺序。
        return copy.deepcopy(self._values[name])

    def __setitem__(self, name, value):
        if not self.writable:
            raise ValueError("共享数据只允许在 initialize 中写入，排查结果请通过返回值提供")
        if not isinstance(name, str) or not name:
            raise ValueError("共享数据名称必须为非空字符串")
        value = json_value(value)
        self.references[name] = artifacts.save(self.root, value)
        self._values[name] = value

    def __iter__(self):
        return iter(self.references)

    def __len__(self):
        return len(self.references)

    def field(self, name, path=""):
        return artifacts.load(self.root, artifacts.select(self.references[name], path))


class ResourceCache:
    def __init__(self, owner, root, identity, references=None):
        self.owner, self.root, self.identity = owner, root, identity
        self.references = dict(references or {})
        self._values = {}
        self._pending = {}
        self._lock = threading.Lock()

    def get(self, loader, version, args, kwargs):
        bound = inspect.signature(loader).bind(self.owner, *args, **kwargs)
        bound.apply_defaults()
        arguments = {name: value for name, value in bound.arguments.items() if name != "self"}
        request = json_value({"identity": self.identity, "resource": loader.__name__,
                              "version": version, "arguments": arguments})
        key = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode("utf-8")).hexdigest()
        with self._lock:
            if key in self.references:
                if key not in self._values:
                    self._values[key] = artifacts.load(self.root, self.references[key])
                logger.info("诊断资源缓存命中 resource=%s key=%s", loader.__name__, key)
                return copy.deepcopy(self._values[key])
            pending = self._pending.get(key)
            leader = pending is None
            if leader:
                pending = self._pending[key] = Future()
        if not leader:
            return copy.deepcopy(pending.result())
        try:
            logger.info("诊断资源首次加载 resource=%s key=%s", loader.__name__, key)
            value = json_value(loader(self.owner, *args, **kwargs))
            reference = artifacts.save(self.root, value)
            with self._lock:
                self.references[key] = reference
                self._values[key] = value
            pending.set_result(value)
            return copy.deepcopy(value)
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)


def normalize_result(value):
    if not isinstance(value, dict) or "presentation" not in value:
        raise ValueError("诊断方法须返回包含 presentation 的对象，可附 result、reason、data")
    view = presentation(value["presentation"])
    result = value.get("result")
    if result is True:
        result = "yes"
    elif result is False:
        result = "no"
    if result not in (None, "yes", "no"):
        raise ValueError("result 必须为 yes/no、布尔值，或空值表示人工判断")
    reason = value.get("reason")
    if result is not None and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("自动判断必须提供非空 reason")
    return json_value({"presentation": view, "result": result, "reason": reason,
                       "data": value.get("data")})


class DiagnosisRuntime:
    def __init__(self):
        self.obj = None
        self.reference = None
        self.identity = None

    def close(self):
        if self.obj is not None:
            try:
                self.obj.close()
            finally:
                self.obj = None
                self.reference = None

    def execute(self, target, request):
        cls = target
        for name in request["class"].split("."):
            cls = getattr(cls, name)
        if not is_diagnosis(cls):
            raise ValueError("流程入口必须为 Diagnosis 业务类")
        meta = describe_diagnosis(cls)
        if meta != request["definition"] or request["version"] != cls.version:
            raise ValueError("诊断类接口或资源版本与发布计划不一致，请重新发布")
        root = request["artifactRoot"]
        reference = request.get("state")
        identity = json_value({"class": request["class"], "version": cls.version,
                               "config": request.get("config") or {}, "definition": meta})
        if self.obj is None or self.reference != reference or self.identity != identity:
            self.close()
            saved = artifacts.load(root, reference) if reference is not None else None
            if saved is not None and saved.get("identity") != identity:
                raise ValueError("诊断数据版本与当前业务类不一致")
            if reference is None and request["operation"] != "initialize":
                raise ValueError("当前执行路径缺少诊断初始化数据，禁止重新采集代替恢复")
            obj = cls(**(request.get("config") or {}))
            inputs = saved["inputs"] if saved else request.get("inputs") or {}
            obj.inputs = copy.deepcopy(inputs)
            obj.data = SharedData(root, saved["data"] if saved else None)
            obj.cache = ResourceCache(obj, root, {**identity, "inputs": inputs},
                                      saved["resources"] if saved else None)
            self.obj, self.identity = obj, identity
            self.reference = reference
        obj = self.obj
        started = time.perf_counter()
        result, error = None, None
        try:
            if request["operation"] == "initialize":
                if reference is not None:
                    raise ValueError("不能在已有诊断数据上重复初始化")
                obj.data.writable = True
                try:
                    obj.initialize(**obj.inputs)
                finally:
                    obj.data.writable = False
            else:
                name = request["method"]
                if name not in {item["name"] for item in meta["methods"]}:
                    raise ValueError("目标不是可调用的排查方法")
                result = normalize_result(getattr(obj, name)(**(request.get("arguments") or {})))
                if result["data"] is not None:
                    result["data"] = artifacts.save(root, result["data"])
        except Exception:
            error = traceback.format_exc()
        # 判断失败也保留已成功加载的资源，失败资源从未进入缓存。
        self.reference = artifacts.save(root, {
            "identity": identity, "inputs": obj.cache.identity["inputs"],
            "data": obj.data.references, "resources": obj.cache.references,
        })
        logger.info("诊断对象%s class=%s state=%s durationMs=%s",
                    "初始化" if request["operation"] == "initialize" else "执行方法",
                    request["class"], self.reference["$artifact"],
                    round((time.perf_counter() - started) * 1000))
        return {"result": result, "error": error, "state": self.reference}

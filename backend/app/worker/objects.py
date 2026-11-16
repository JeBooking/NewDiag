"""工程内对象管理：构造、调用、快照。

槽位是唯一的接入方式，业务对象只活在自己的工程进程里；平台按名字引用，
跨工程只传 JSON 与文件。对象图用 pickle 保存，供人工等待、回退与进程重建使用。
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import logging

if __package__:
    from . import artifacts, node_sdk
else:
    import artifacts
    import node_sdk

logger = logging.getLogger(__name__)


class ProjectObjects:
    def __init__(self):
        self.objects = {}
        self.reference = None

    def save(self, root):
        snapshot = artifacts.save_python(root, self.objects)
        self.reference = artifacts.save(root, {"format": "slot-state/v1", "objects": snapshot})
        return self.reference

    def execute(self, target, request):
        """request.op：construct / call / restore。

        target 是工程模块入口（session 已按登记信息 import 好），模块级函数从它取。
        """
        root = request["artifactRoot"]
        reference = request.get("state")
        if reference != self.reference:
            self.objects = {}
            if reference:
                manifest = artifacts.load(root, reference)
                saved = manifest.get("objects") if isinstance(manifest, dict) else None
                if saved:
                    self.objects = artifacts.load_python(root, saved)
            self.reference = reference
            logger.info("工程对象状态恢复 objects=%s saved=%s", len(self.objects), bool(reference))

        operation = request.get("op")
        if operation == "restore":
            return {"state": self.reference}

        module_name = request.get("module") or ""
        module = importlib.import_module(module_name) if module_name else target
        arguments = self.decode(request.get("arguments") or {}, root)

        if operation == "construct":
            name = request.get("object") or ""
            if not name:
                raise ValueError("构造槽缺少对象名")
            function = _resolve(module, request.get("interface") or request.get("function") or "")
            _check(function, arguments)
            value = function(**arguments)
            self.objects[name] = value
            logger.info("构造对象 name=%s interface=%s", name, request.get("interface"))
            return {"value": {"$object": name}, "state": self.save(root)}

        owner = None
        if request.get("object"):
            owner = self.objects.get(request["object"])
            if owner is None:
                raise ValueError(f"对象 {request['object']} 尚未构造")
        if owner is not None:
            method = request.get("method") or request.get("function") or ""
            if method.startswith("_") or "." in method:
                raise ValueError("槽位只能调用公开方法")
            function = getattr(owner, method)
            _check(function, arguments, bound=True)
        else:
            function = _resolve(module, request.get("interface") or request.get("function") or "")
            _check(function, arguments)
        if request.get("fingerprint"):
            try:
                current = hashlib.sha256(inspect.getsource(function).encode("utf-8")).hexdigest()
            except (OSError, TypeError) as exc:
                raise ValueError("无法核对已发布接口的代码版本") from exc
            if current != request["fingerprint"]:
                raise ValueError("接口实现已变化，请重新发布流程")
        value = function(**arguments)
        result = node_sdk.json_value(value)
        # 对象成员可能在调用中被修改，每次调用后保存一次，恢复时不重跑构造器。
        return {"value": result, "state": self.save(root)}

    def decode(self, value, root):
        if isinstance(value, dict):
            if "$object" in value and set(value) <= {"$object", "project"}:
                name = value["$object"]
                if name not in self.objects:
                    raise ValueError(f"对象 {name} 尚未构造")
                return self.objects[name]
            if artifacts.is_reference(value):
                return self.decode(artifacts.load(root, value), root)
            return {key: self.decode(item, root) for key, item in value.items()}
        if isinstance(value, list):
            return [self.decode(item, root) for item in value]
        return value


def _resolve(module, dotted: str):
    if not dotted:
        raise ValueError("接口名为空")
    value = module
    for part in dotted.split("."):
        if part.startswith("_"):
            raise ValueError("只能调用公开接口")
        value = getattr(value, part)
        if value is None:
            raise AttributeError(dotted)
    return value


def _check(function, arguments, bound=False):
    if not callable(function):
        raise TypeError("槽位只能填可调用对象")
    if inspect.iscoroutinefunction(function):
        raise ValueError("槽位需要同步接口")
    signature = inspect.signature(function)
    parameters = list(signature.parameters.values())
    if not bound and parameters and parameters[0].name in ("self", "cls"):
        raise ValueError("实例方法必须指定目标对象")
    signature.bind(**arguments)

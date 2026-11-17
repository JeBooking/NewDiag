"""槽位链运行时：构造 → 拉取 → 处理 → 展示 → 判定。

槽位顺序固定，不配置连线；拉取与处理的返回值是「上游值」，展示与判定的参数
用 `{"source": "previous"}` 取它。拉取结果按对象、接口与参数缓存，同实例只取一次。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path

if __package__:
    from . import artifacts
    from .node_sdk import presentation
else:
    import artifacts
    from node_sdk import presentation

logger = logging.getLogger(__name__)
SUMMARY_CHARS = 4096


def _summary(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)
    if len(text) <= SUMMARY_CHARS:
        return value
    encoded = text.encode("utf-8")
    return {"omitted": True, "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


class SlotRuntime:
    def __init__(self, root, invoke, file_root=None, state=None):
        self.root = Path(root)
        self.file_root = Path(file_root) if file_root else None
        self.invoke = invoke
        loaded = None
        if isinstance(state, dict) and artifacts.is_reference(state):
            loaded = artifacts.load(self.root, state)
        elif isinstance(state, dict) and state.get("format") == "slot-runtime/v1":
            loaded = state
        self.state = loaded if isinstance(loaded, dict) else {
            "format": "slot-runtime/v1", "objects": {}, "cache": {}, "workers": {},
        }
        self.state.setdefault("objects", {})
        self.state.setdefault("cache", {})
        self.state.setdefault("workers", {})
        self.inputs = {}
        self.parameters = {}
        self.previous = None
        self.stages = []
        self.reference = None

    # ------------------------------------------------------------ 状态

    def save(self):
        self.reference = artifacts.save(self.root, self.state)
        return self.reference

    def restore(self):
        return {"state": self.save()}

    # ------------------------------------------------------------ 执行

    def run(self, step, parameters=None):
        """执行一个节点的槽位；返回展示块、判定结果与是否需要人工。"""
        self.parameters = parameters or {}
        self.presentation = None
        slots = step.get("slots") or {}
        for call in slots.get("constructs") or []:
            self.construct(call)
        # 「上一槽结果」只在本节点内有效：拉取 → 处理 → 展示／判定。
        # 跨节点不传值；下一个节点要数据就自己取，或从对象、数据源里拿。
        value = None
        if isinstance(slots.get("fetch"), dict):
            value = self.fetch(slots["fetch"])
        if isinstance(slots.get("process"), dict):
            value = self.call(slots["process"], value)
        self.previous = value
        blocks = []
        if isinstance(slots.get("present"), dict):
            blocks = self.present(slots["present"], value)
        result, reason, human = None, None, False
        decide = slots.get("decide")
        if isinstance(decide, dict):
            if decide.get("human"):
                human = True
            else:
                result, reason = self.decide(decide, value)
        return {"blocks": blocks, "result": result, "reason": reason, "human": human, "value": value}

    def construct(self, call):
        name = str(call.get("object") or "")
        if not name:
            raise ValueError("构造槽缺少对象名")
        if name in self.state["objects"]:
            return self.state["objects"][name]
        project = call["project"]
        arguments = {key: self.resolve(value, project) for key, value in (call.get("arguments") or {}).items()}
        response = self._invoke(project, {
            "op": "construct", "object": name, "module": call.get("module", ""),
            "interface": call.get("interface") or call.get("function", ""),
            "arguments": arguments, "fingerprint": call.get("fingerprint", ""),
        })
        value = response["value"]
        handle = {"$object": name, "project": project}
        self.state["objects"][name] = handle
        logger.info("构造槽 name=%s project=%s", name, project)
        return handle

    def fetch(self, call):
        """拉取槽：默认按对象、接口、参数缓存，回退不重新取。

        槽位取消「使用缓存」时跳过读取，取完仍写回缓存，后面的节点跟着用新值。
        """
        arguments = {key: self.resolve(value, call["project"]) for key, value in (call.get("arguments") or {}).items()}
        key = self._cache_key(call, arguments)
        use_cache = call.get("useCache") is not False
        if use_cache and key in self.state["cache"]:
            logger.info("拉取缓存命中 %s", call.get("interface"))
            return self.state["cache"][key]
        if not use_cache:
            logger.info("拉取跳过缓存 %s", call.get("interface"))
        value = self._call(call, arguments)
        self.state["cache"][key] = value
        self.save()
        return value

    def call(self, call, previous=None):
        arguments = {key: self.resolve(value, call["project"], previous)
                     for key, value in (call.get("arguments") or {}).items()}
        return self._call(call, arguments)

    def present(self, call, previous=None):
        view = call.get("view")
        if view not in ("image", "line", "metrics", "table", "text"):
            raise ValueError("展示槽必须先选择展示类型")
        value = self.call(call, previous)
        self.presentation = value
        if view == "image":
            src = value.get("image") if isinstance(value, dict) else value
            if not isinstance(src, str) or not re.fullmatch(r"[0-9a-f]{64}", src):
                raise ValueError("图片展示需要返回图片摘要：请用 node_sdk.save_file 保存图片或返回文件路径")
            block = {"type": "image", "src": src}
        elif view == "line":
            if not isinstance(value, list) or not value:
                raise ValueError("折线展示需要返回非空数值数组")
            block = {"type": "line", "values": value}
        elif view == "metrics":
            block = {"type": "metrics", "items": value}
        elif view == "table":
            block = {**value, "type": "table"}
        else:
            block = {"type": "text",
                     "text": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)}
        return presentation({"blocks": [block]})["blocks"]

    def decide(self, call, previous=None):
        value = self.call(call, previous)
        reason = ""
        if isinstance(value, dict):
            result = value.get("result")
            reason = str(value.get("reason") or "")
        else:
            result = value
        if isinstance(result, bool):
            result = "yes" if result else "no"
        elif isinstance(result, str):
            result = result.strip().lower()
        if result not in ("yes", "no"):
            raise ValueError("判定槽必须返回布尔值、yes／no，或 {result, reason}")
        return result, reason.strip() or "由判定方法给出结论"

    # ------------------------------------------------------------ 参数

    def resolve(self, argument, project=None, previous=None):
        if not isinstance(argument, dict):
            raise ValueError("参数来源必须为对象")
        if "lit" in argument:
            return argument["lit"]
        if "items" in argument:
            return [self.resolve(item, project, previous) for item in argument["items"]]
        if "fields" in argument:
            return {key: self.resolve(item, project, previous) for key, item in argument["fields"].items()}
        source = argument.get("source")
        if source == "input":
            value = self.inputs[argument["name"]]
        elif source == "parameter":
            value = self.parameters[argument["name"]]
        elif source == "previous":
            value = previous
        elif source == "presentation":
            value = self.presentation
        else:
            raise ValueError(f"未知参数来源 {source}")
        if argument.get("file"):
            return self.file_path(value)
        return value

    def file_path(self, value):
        if isinstance(value, str) and self.file_root is not None:
            path = Path(value).resolve()
            if path.is_relative_to(self.file_root.resolve()) and path.is_file():
                return str(path)
        if not isinstance(value, dict) or not isinstance(value.get("hash"), str):
            raise ValueError("文件参数需要平台上传引用")
        digest = value["hash"].removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or self.file_root is None:
            raise ValueError("上传文件引用无效")
        path = self.file_root / digest[:2] / digest
        if not path.is_file():
            raise ValueError("上传文件不存在")
        return str(path.resolve())

    # ------------------------------------------------------------ 调用

    def _call(self, call, arguments):
        project = call["project"]
        started = time.perf_counter()
        try:
            response = self._invoke(project, {
                "op": "call", "module": call.get("module", ""),
                "object": call.get("object") or "", "method": call.get("function", ""),
                "interface": call.get("interface") or call.get("function", ""),
                "arguments": arguments, "fingerprint": call.get("fingerprint", ""),
            })
            return response["value"]
        except Exception as exc:
            raise ValueError(f"{project} / {call.get('interface') or call.get('function')}: {exc}") from exc
        finally:
            self.stages.append({
                "project": project, "function": call.get("function", ""),
                "arguments": {name: _summary(item) for name, item in arguments.items()},
                "durationMs": round((time.perf_counter() - started) * 1000),
            })

    def _invoke(self, project, payload):
        request = {
            **payload, "artifactRoot": str(self.root),
            "outputRoot": str(self.root / "outputs"), "fileRoot": str(self.file_root) if self.file_root else "",
            "callDirectory": str(self.root / "outputs" / uuid.uuid4().hex),
            "state": self.state["workers"].get(project),
        }
        Path(request["callDirectory"]).mkdir(parents=True, exist_ok=True)
        response = self.invoke(project, request)
        self.state["workers"][project] = response.get("state")
        return response

    def _cache_key(self, call, arguments):
        identity = [call.get("project", ""), call.get("module", ""),
                    call.get("interface") or call.get("function", ""), call.get("object", "")]
        text = json.dumps([identity, arguments], ensure_ascii=False, sort_keys=True, default=repr)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

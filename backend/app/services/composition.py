"""配置驱动的诊断组合：表达式求值、跨工程桥接、资源缓存与结果映射。

平台不生成代码：每一步都是一次 RPC，参数按来源表达式在当前实例内解析。
业务对象留在所属工程的进程里，跨工程只传 JSON、不可变文件和图片摘要。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from app.services import storage
from app.worker.node_sdk import presentation as normalize_presentation

logger = logging.getLogger(__name__)

ERR_CALL = "execution/compose"
ERR_ARGUMENT = "execution/argument"

MAX_BRIDGE_BYTES = 256 * 1024 * 1024
MAX_RUN_VALUE_CHARS = 8000

YES_VALUES = {"yes", "y", "true", "1", "是"}
NO_VALUES = {"no", "n", "false", "0", "否"}

RESOURCE_START = "start"
RESOURCE_LAZY = "lazy"


class CompositionFailure(Exception):
    """组合执行的失败；code 带分类前缀，写进 run.error。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class CompositionState:
    """一个诊断实例的组合状态：已创建的对象和已加载的资源。

    只在平台进程内保存；业务对象始终留在各自的 worker 里。
    平台重启或 worker 崩溃后句柄失效，后续调用会明确报错，不静默重新取数。
    """

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.resources: dict[str, dict] = {}
        self.calls = 0

    def next_call(self) -> int:
        self.calls += 1
        return self.calls


class CompositionEngine:
    """一次执行（启动资源或一个排查节点）的组合器。"""

    def __init__(
        self,
        definition: dict,
        state: CompositionState,
        *,
        inputs: dict,
        artifact_root: Path,
        project_of,
        call,
    ) -> None:
        self.definition = definition or {}
        self.state = state
        self.inputs = inputs or {}
        self.artifact_root = Path(artifact_root)
        self._project_of = project_of
        self._call = call

    # ------------------------------------------------------------------ 资源

    def run_resources(self, ids: list[str]) -> dict[str, dict]:
        """按声明顺序执行启动资源；已加载的直接复用，不重复取数。"""
        done = {}
        for resource_id in ids:
            done[resource_id] = self.load_resource(resource_id)
        return done

    def load_resource(self, resource_id: str) -> dict:
        cached = self.state.resources.get(resource_id)
        if cached is not None:
            logger.info("组合资源复用 resource=%s", resource_id)
            return cached
        resource = (self.definition.get("resources") or {}).get(resource_id)
        if resource is None:
            raise CompositionFailure(ERR_ARGUMENT, f"资源 {resource_id} 未在组合配置里声明")
        # 资源按实例缓存：启动输入在一次诊断内冻结，键只需要资源身份。
        results = self._run_steps(
            resource.get("steps") or [], scope=f"resource:{resource_id}",
            params={}, allow_params=False,
            # 启动资源产出的对象要被后续操作共用，登记为实例级；按需资源只在本资源内可见。
            global_objects=resource.get("trigger") == RESOURCE_START,
        )
        self.state.resources[resource_id] = results
        return results

    # ------------------------------------------------------------------ 操作

    def resolve_params(self, expressions: dict) -> dict:
        """节点上的 operationArguments：只允许启动输入、常量和组合值。"""
        context = {"scope": "operation:params", "params": {}, "allowParams": False, "results": {}}
        return {name: self._eval(expression, context) for name, expression in (expressions or {}).items()}

    def run_operation(self, operation_id: str, params: dict) -> dict:
        operation = (self.definition.get("operations") or {}).get(operation_id)
        if operation is None:
            raise CompositionFailure(ERR_ARGUMENT, f"操作 {operation_id} 未在组合配置里声明")
        results = self._run_steps(
            operation.get("steps") or [], scope=f"operation:{operation_id}",
            params=params, allow_params=True,
        )
        return {
            "results": results,
            "presentation": self.build_presentation(operation.get("presentation"), results),
            "decision": self.decide(operation.get("result"), results),
        }

    # ------------------------------------------------------------------ 步骤

    def _run_steps(self, steps: list[dict], *, scope: str, params: dict, allow_params: bool,
                   global_objects: bool = False) -> dict:
        results: dict[str, dict] = {}
        context = {"scope": scope, "params": params, "allowParams": allow_params,
                   "globalObjects": global_objects, "results": results}
        for step in steps:
            step_id = str(step.get("id") or "")
            if not step_id:
                raise CompositionFailure(ERR_ARGUMENT, "组合步骤缺少 id")
            results[step_id] = self._run_step(step, context)
        return results

    def _run_step(self, step: dict, context: dict) -> dict:
        project_name = str(step.get("project") or "")
        project = self._project_of(project_name)
        if project is None:
            raise CompositionFailure(ERR_CALL, f"工程 {project_name} 未登记，无法执行步骤 {step.get('id')}")

        kind = str(step.get("kind") or "function")
        if kind == "method":
            target = self._object_handle(str(step.get("target") or ""), context["scope"])
        else:
            target = ""
        output_dir = self._workdir(context, str(step.get("id") or ""))
        args = {}
        for name, expression in (step.get("args") or {}).items():
            reference = self._eval(expression, context)
            args[name] = self._materialize(reference, project_name)
        request = {
            "action": "call",
            "call": {
                "kind": kind,
                "target": target,
                "function": str(step.get("function") or ""),
                "module": str(step.get("module") or ""),
            },
            "args": args,
            "output": str(step.get("output") or "auto"),
            "handle": f"{context['scope']}:{step.get('id')}",
            "outputDir": str(output_dir),
            "fileRoot": str(storage.files_dir()),
        }
        response = self._call(project, request)
        reference = self._reference(response, project_name)
        save_as = str(step.get("saveAs") or "")
        if save_as:
            if reference.get("kind") != "handle":
                raise CompositionFailure(
                    ERR_CALL, f"步骤 {step.get('id')} 声明了 saveAs，但返回的不是对象")
            # 启动资源产出的对象是实例级；操作内产出的按作用域隔离，并行操作互不覆盖。
            key = save_as if context.get("globalObjects") else f"{context['scope']}|{save_as}"
            self.state.objects[key] = {"project": project_name, "handle": reference["handle"]}
            logger.info("组合对象已登记 object=%s project=%s", save_as, project_name)
        return reference

    def _workdir(self, context: dict, step_id: str) -> Path:
        """每次实际调用一个独立目录：同参数重复取数也不会覆盖历史文件。"""
        seq = self.state.next_call()
        name = f"{seq:04d}-{_slug(context['scope'])}-{_slug(step_id)}"
        target = self.artifact_root / "calls" / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _object_handle(self, object_id: str, scope: str = "") -> str:
        if not object_id:
            raise CompositionFailure(ERR_ARGUMENT, "实例方法调用缺少对象")
        definition = (self.definition.get("objects") or {}).get(object_id)
        cached = None
        if definition is None:
            # 操作内产出的对象按作用域隔离：两个并行操作各建一个 judge 互不覆盖。
            if scope:
                cached = self.state.objects.get(f"{scope}|{object_id}")
            if cached is None:
                cached = self.state.objects.get(object_id)
        else:
            cached = self.state.objects.get(object_id)
        if cached is not None:
            return cached["handle"]
        if definition is None:
            raise CompositionFailure(
                ERR_ARGUMENT,
                f"对象 {object_id} 尚未创建：它需要由更早的步骤用 saveAs 产出，或先在组合配置里声明",
            )
        project_name = str(definition.get("project") or "")
        project = self._project_of(project_name)
        if project is None:
            raise CompositionFailure(ERR_CALL, f"工程 {project_name} 未登记，无法创建对象 {object_id}")

        args = {}
        for name, expression in (definition.get("args") or {}).items():
            reference = self._eval(expression, {"scope": f"object:{object_id}", "params": {},
                                                "allowParams": False, "results": {}})
            args[name] = self._materialize(reference, project_name)
        handle = f"object:{object_id}"
        response = self._call(project, {
            "action": "call",
            "call": {"kind": "constructor", "function": str(definition.get("entry") or ""),
                     "module": str(definition.get("module") or "")},
            "args": args,
            "output": "object",
            "handle": handle,
            "outputDir": str(self._workdir({"scope": f"object:{object_id}"}, "init")),
            "fileRoot": str(storage.files_dir()),
        })
        created = response.get("value") or {}
        if created.get("kind") != "handle":
            raise CompositionFailure(ERR_CALL, f"对象 {object_id} 的构造器没有返回对象")
        self.state.objects[object_id] = {"project": project_name, "handle": created["handle"]}
        logger.info("组合对象已创建 object=%s project=%s", object_id, project_name)
        return created["handle"]

    # ------------------------------------------------------------------ 表达式

    def _eval(self, expression, context: dict) -> dict:
        if not isinstance(expression, dict):
            raise CompositionFailure(ERR_ARGUMENT, f"参数表达式必须是对象：{expression!r}")

        if "lit" in expression:
            return _value_ref(expression["lit"])
        if "input" in expression:
            return _value_ref(_lookup_path(self.inputs, str(expression.get("input") or ""),
                                           f"启动输入 {expression.get('input')}"))
        if "param" in expression:
            if not context["allowParams"]:
                raise CompositionFailure(ERR_ARGUMENT, "启动资源不能引用节点参数")
            name = str(expression.get("param") or "")
            if name not in context["params"]:
                raise CompositionFailure(ERR_ARGUMENT, f"节点没有提供参数 {name}")
            return _with_path(context["params"][name], expression.get("path"), self)
        if "step" in expression:
            step_id = str(expression.get("step") or "")
            reference = context["results"].get(step_id)
            if reference is None:
                raise CompositionFailure(ERR_ARGUMENT, f"步骤 {step_id} 尚未执行或不在前面")
            return _with_path(reference, expression.get("path"), self)
        if "resource" in expression:
            resource_id = str(expression.get("resource") or "")
            step_id = str(expression.get("step") or "")
            results = self.state.resources.get(resource_id)
            if results is None:
                results = self.load_resource(resource_id)
            reference = results.get(step_id)
            if reference is None:
                raise CompositionFailure(ERR_ARGUMENT, f"资源 {resource_id} 没有步骤 {step_id}")
            return _with_path(reference, expression.get("path"), self)
        if "object" in expression:
            object_id = str(expression.get("object") or "")
            scope = context.get("scope", "")
            handle = self._object_handle(object_id, scope)
            cached = (self.state.objects.get(f"{scope}|{object_id}") if scope else None) \
                or self.state.objects.get(object_id) or {}
            return {"kind": "handle", "project": cached.get("project", ""), "handle": handle}
        if "system" in expression:
            return _value_ref(self._system_path(expression, context))
        if "items" in expression:
            raw = expression.get("items")
            if not isinstance(raw, list):
                raise CompositionFailure(ERR_ARGUMENT, "items 必须是数组")
            return {"kind": "list", "items": [self._eval(item, context) for item in raw]}
        if "fields" in expression:
            raw = expression.get("fields")
            if not isinstance(raw, dict):
                raise CompositionFailure(ERR_ARGUMENT, "fields 必须是对象")
            return {"kind": "dict", "items": {key: self._eval(item, context)
                                              for key, item in raw.items()}}
        raise CompositionFailure(ERR_ARGUMENT, f"无法识别的参数表达式：{sorted(expression)}")

    def _system_path(self, expression: dict, context: dict):
        kind = str(expression.get("system") or "")
        directory = self._workdir(context, "out")
        if kind == "directory":
            return str(directory)
        if kind == "file":
            name = _slug(Path(str(expression.get("name") or "output.bin")).name, keep_dots=True)
            return str(directory / name)
        raise CompositionFailure(ERR_ARGUMENT, f"不支持的平台路径类型：{kind}")

    # ------------------------------------------------------------------ 桥接

    def _value_of(self, reference: dict):
        reference = self._as_value(reference)
        return reference.get("value")

    def _as_value(self, reference: dict) -> dict:
        kind = reference.get("kind")
        if kind == "value":
            return reference
        if kind == "list":
            return _value_ref([self._value_of(item) for item in reference.get("items") or []])
        if kind == "dict":
            return _value_ref({key: self._value_of(item)
                               for key, item in (reference.get("items") or {}).items()})
        if kind == "handle":
            return self._as_value(self._export(reference))
        raise CompositionFailure(
            ERR_ARGUMENT,
            f"需要普通值，但来源是 {kind}；请换一个返回 JSON 的步骤",
        )

    def _export(self, reference: dict) -> dict:
        project = self._project_of(reference.get("project"))
        if project is None:
            raise CompositionFailure(ERR_CALL, f"工程 {reference.get('project')} 未登记")
        response = self._call(project, {"action": "export", "handle": reference.get("handle")})
        return self._reference(response, reference.get("project"))

    def _materialize(self, reference: dict, project_name: str):
        kind = reference.get("kind")
        if kind == "value":
            return reference.get("value")
        if kind == "handle":
            if reference.get("project") == project_name:
                return {"$handle": reference["handle"]}
            return self._materialize(self._export(reference), project_name)
        if kind in ("file", "image"):
            return reference["path"]
        if kind == "files":
            return [item["path"] for item in reference.get("items") or []]
        if kind == "list":
            return [self._materialize(item, project_name) for item in reference.get("items") or []]
        if kind == "dict":
            return {key: self._materialize(item, project_name)
                    for key, item in (reference.get("items") or {}).items()}
        raise CompositionFailure(ERR_ARGUMENT, f"无法传递的数据类型：{kind}")

    def _reference(self, response: dict, project_name: str) -> dict:
        value = (response or {}).get("value") or {}
        kind = value.get("kind")
        if kind == "handle":
            return {"kind": "handle", "project": project_name, "handle": value["handle"],
                    "preview": (response or {}).get("preview")}
        if kind == "value":
            return _value_ref(value.get("value"))
        if kind == "file":
            return self._store_file(value)
        if kind == "files":
            return {"kind": "files", "items": [self._store_file(item) for item in value.get("items") or []]}
        if kind == "image":
            return self._store_image(value.get("hash"))
        raise CompositionFailure(ERR_CALL, f"业务进程返回了未知的结果类型：{kind}")

    def _store_file(self, value: dict) -> dict:
        path = Path(str(value.get("path") or ""))
        if not path.is_file():
            raise CompositionFailure(ERR_CALL, f"业务返回的文件不存在：{path}")
        if path.stat().st_size > MAX_BRIDGE_BYTES:
            raise CompositionFailure(ERR_CALL, f"文件超过 {MAX_BRIDGE_BYTES // 1024 // 1024} MB")
        digest, target = storage.store(path.read_bytes())
        return {"kind": "file", "path": str(target), "hash": digest,
                "name": str(value.get("name") or path.name), "size": target.stat().st_size}

    def _store_image(self, digest) -> dict:
        if not isinstance(digest, str) or not storage.is_digest(digest):
            raise CompositionFailure(ERR_CALL, f"图片摘要不合法：{digest!r}")
        path = storage.resolve(digest)
        if path is None:
            raise CompositionFailure(ERR_CALL, f"图片没有落盘：{digest}")
        return {"kind": "image", "hash": digest, "path": str(path)}

    # ------------------------------------------------------------------ 输出

    def decide(self, result: dict | None, results: dict) -> dict | None:
        if not result:
            return None
        step_id = str(result.get("step") or "")
        reference = results.get(step_id)
        if reference is None:
            raise CompositionFailure(ERR_ARGUMENT, f"结果映射引用了未执行的步骤 {step_id}")
        value = self._value_of(reference)
        outcome = _lookup_optional(value, str(result.get("outcome") or ""))
        branch = _branch_of(outcome)
        if branch is None:
            raise CompositionFailure(
                ERR_CALL,
                f"步骤 {step_id} 的 {result.get('outcome') or '返回值'} = {_brief(outcome)}，无法判定为是/否",
            )
        raw_reason = _lookup_optional(value, str(result.get("reason") or ""))
        reason = _reason_text(raw_reason) or str(result.get("normalReason") or "").strip()
        if not reason:
            reason = "已按配置规则完成判断" if branch == "no" else "命中配置的异常规则"
        return {
            "branch": branch,
            "reason": reason,
            "outcome": _brief_value(outcome),
            "value": _run_value(value),
        }

    def build_presentation(self, items, results: dict) -> dict:
        if not items:
            return self._auto_presentation(results)
        blocks = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise CompositionFailure(ERR_ARGUMENT, f"展示配置第 {index + 1} 项必须是对象")
            blocks.append(self._block(item, results))
        return normalize_presentation({"blocks": blocks})

    def _block(self, item: dict, results: dict) -> dict:
        kind = str(item.get("type") or "")
        if kind == "text":
            if "text" in item:
                return {"type": "text", "text": str(item["text"])}
            value = self._step_value(item, results)
            return {"type": "text", "text": _text_of(value)}
        if kind == "image":
            reference = self._step_ref(item, results)
            if reference.get("kind") != "image":
                raise CompositionFailure(ERR_ARGUMENT, "image 展示块必须引用 output=image 的步骤")
            block = {"type": "image", "src": reference["hash"]}
            for key in ("alt", "caption"):
                if item.get(key):
                    block[key] = str(item[key])
            return block
        if kind == "table":
            value = self._step_value(item, results)
            return _table_block(item, value)
        if kind == "metrics":
            value = self._step_value(item, results)
            return _metrics_block(item, value)
        if kind == "line":
            value = self._step_value(item, results)
            values = value if isinstance(value, list) else [value]
            return {"type": "line", "values": values}
        raise CompositionFailure(ERR_ARGUMENT, f"不支持的展示块类型：{kind}")

    def _step_ref(self, item: dict, results: dict) -> dict:
        step_id = str(item.get("step") or "")
        reference = results.get(step_id)
        if reference is None:
            raise CompositionFailure(ERR_ARGUMENT, f"展示配置引用了未执行的步骤 {step_id}")
        return reference

    def _step_value(self, item: dict, results: dict):
        reference = self._step_ref(item, results)
        value = self._value_of(reference)
        return _lookup_optional(value, str(item.get("path") or ""))

    def _auto_presentation(self, results: dict) -> dict:
        """没配展示时也要让人看见结果：图片全部展示，其余给一行摘要。"""
        blocks = []
        lines = []
        for step_id, reference in results.items():
            kind = reference.get("kind")
            if kind == "image":
                blocks.append({"type": "image", "src": reference["hash"], "alt": step_id})
                continue
            if kind == "file":
                lines.append(f"{step_id}：文件 {reference.get('name')}（{reference.get('size')} 字节）")
                continue
            if kind == "handle":
                preview = reference.get("preview") or {}
                lines.append(f"{step_id}：对象 {preview.get('$object') or ''}".strip())
                continue
            lines.append(f"{step_id}：{_brief(reference.get('value'))}")
        if lines:
            blocks.append({"type": "text", "text": "\n".join(lines)})
        if not blocks:
            blocks.append({"type": "text", "text": "本次执行没有可展示的结果"})
        return normalize_presentation({"blocks": blocks})


# ---------------------------------------------------------------- 辅助


def _value_ref(value) -> dict:
    return {"kind": "value", "value": value}


def _with_path(reference: dict, path, engine: CompositionEngine) -> dict:
    text = str(path or "")
    if not text:
        return reference
    return _value_ref(_lookup_path(engine._value_of(reference), text, f"字段路径 {text}"))


def _lookup_path(root, path: str, label: str):
    value = root
    for part in str(path).split("."):
        if not part:
            continue
        if isinstance(value, dict):
            if part not in value:
                raise CompositionFailure(ERR_ARGUMENT, f"{label} 不存在：缺少 {part}")
            value = value[part]
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(part)]
            except (ValueError, IndexError) as exc:
                raise CompositionFailure(ERR_ARGUMENT, f"{label} 不存在：下标 {part} 越界") from exc
        else:
            raise CompositionFailure(ERR_ARGUMENT, f"{label} 不存在：{type(value).__name__} 没有 {part}")
    return value


def _lookup_optional(root, path: str):
    value = root
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, (list, tuple)) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _branch_of(value) -> str | None:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, str):
        text = value.strip().lower()
        if text in YES_VALUES:
            return "yes"
        if text in NO_VALUES:
            return "no"
    return None


def _reason_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "；".join(parts)
    if value is None:
        return ""
    return str(value).strip()


def _text_of(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=repr)


def _table_block(item: dict, value) -> dict:
    rows = value if isinstance(value, list) else [value]
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return {"type": "text", "text": _text_of(value)}
    columns = item.get("columns")
    if not isinstance(columns, list) or not columns:
        columns = list(rows[0])
    headers = item.get("headers")
    headers = headers if isinstance(headers, list) and len(headers) == len(columns) else [str(c) for c in columns]
    body = [[_cell(row.get(column)) for column in columns] for row in rows[:200]]
    return {"type": "table", "columns": [str(h) for h in headers], "rows": body}


def _metrics_block(item: dict, value) -> dict:
    specs = item.get("items")
    if not isinstance(specs, list) or not specs:
        raise CompositionFailure(ERR_ARGUMENT, "metrics 展示块需要 items: [{label, key}]")
    source = value if isinstance(value, dict) else {}
    items = []
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("label") or not spec.get("key"):
            raise CompositionFailure(ERR_ARGUMENT, "metrics 的每一项需要 label 与 key")
        block = {"label": str(spec["label"]), "value": _cell(source.get(str(spec["key"])))}
        if spec.get("unit"):
            block["unit"] = str(spec["unit"])
        items.append(block)
    return {"type": "metrics", "items": items}


def _cell(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, ensure_ascii=False, default=repr)


def _brief(value) -> str:
    text = json.dumps(value, ensure_ascii=False, default=repr) if not isinstance(value, str) else value
    return text if len(text) <= 80 else text[:77] + "..."


def _brief_value(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _brief(value)


def _run_value(value):
    """运行记录里只保留可读的一部分，完整数据留在 worker / 产物里。"""
    text = json.dumps(value, ensure_ascii=False, default=repr)
    if len(text) <= MAX_RUN_VALUE_CHARS:
        return value
    return {"omitted": True, "bytes": len(text.encode("utf-8")),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _slug(value: str, *, keep_dots: bool = False) -> str:
    text = str(value or "").strip()
    allowed = "-_." if keep_dots else "-_"
    cleaned = "".join(ch if ch.isalnum() or ch in allowed else "-" for ch in text)
    return cleaned.strip("-.") or "item"

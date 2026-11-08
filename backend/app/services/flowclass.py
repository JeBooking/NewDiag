"""流程类：把 doc 里的声明编译成一份真实的 Python 类。

设计依据：一个 flow 就是一个类，一次执行就是它的一个实例。
作者只声明「导入哪些工程、构造时做什么、每个方法按序调哪些接口、返回哪几个字段」，
发布时这里把它翻成 Python 源码，运行时由 `diagnosis_sdk.DiagnosisRuntime` 直接执行——
平台不解释表达式、不维护对象句柄，语义全部交给 Python。

生成的类满足 `diagnosis/v1`，因此人工等待、回退、并行、运行记录全部沿用原调度器。
"""

from __future__ import annotations

import json
import re

PROJECT = "@flow"
CLASS = "Flow"
VERSION = "1.0.0"
PROTOCOL = "diagnosis/v1"

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESERVED = {"initialize", "close", "__init__", "version", "artifact_root"}

# 声明里的平台类型 → Python 注解；没有映射的类型（json/object/array）保持裸参数
ANNOTATIONS = {"text": "str", "datetime": "str", "number": "float", "boolean": "bool"}
RETURN_KEYS = {"result", "reason", "figure", "series"}


class FlowClassError(ValueError):
    """声明结构不合法；消息可直接展示给作者。"""


# ---------------------------------------------------------------- 读取


def definition(doc: dict) -> dict | None:
    """取 doc 里的类声明；没有就返回 None（老流程不受影响）。"""
    value = doc.get("flowClass") if isinstance(doc, dict) else None
    if value is None:
        return None
    if not isinstance(value, dict):
        raise FlowClassError("类定义必须是对象")
    return value


def project_names(doc: dict) -> list[str]:
    """类声明引用到的工程名，按出现顺序去重。"""
    spec = definition(doc) or {}
    names: list[str] = []
    for item in spec.get("imports") or []:
        if isinstance(item, dict) and isinstance(item.get("project"), str):
            if item["project"] not in names:
                names.append(item["project"])
    return names


# ---------------------------------------------------------------- 校验


class _Scope:
    """一次生成/校验的可解析名字集合。"""

    def __init__(self, aliases: dict[str, str], instance: set[str], params: set[str],
                 kind: str = "method") -> None:
        self.aliases = aliases
        self.instance = set(instance)
        self.params = set(params)
        self.kind = kind
        self.locals: set[str] = set()


def validate(doc: dict, start_params: list[dict]) -> list[str]:
    """校验声明结构与名字引用；返回错误消息列表，空表示可用。"""
    try:
        spec = definition(doc)
    except FlowClassError as exc:
        return [str(exc)]
    if spec is None:
        return []

    errors: list[str] = []
    imports = _as_list(spec.get("imports"), "imports", errors)
    initialize = _as_list(spec.get("initialize"), "initialize", errors)
    methods = _as_list(spec.get("methods"), "methods", errors)

    aliases: dict[str, str] = {}
    for index, item in enumerate(imports):
        label = f"导入第 {index + 1} 行"
        if not isinstance(item, dict):
            errors.append(f"{label}必须是对象")
            continue
        alias, project, module = item.get("alias"), item.get("project"), item.get("module")
        if not isinstance(alias, str) or not IDENTIFIER.match(alias):
            errors.append(f"{label}的别名必须是合法标识符")
            continue
        if alias in aliases:
            errors.append(f"导入别名重复：{alias}")
            continue
        if not isinstance(project, str) or not project.strip():
            errors.append(f"{label}缺少工程名")
            continue
        if not isinstance(module, str) or not module.strip():
            errors.append(f"{label}缺少模块名")
            continue
        aliases[alias] = module

    instance: set[str] = set()
    scope = _Scope(aliases, instance, set())
    for index, step in enumerate(initialize):
        _check_step(f"初始化第 {index + 1} 步", step, scope, errors, remember=False)
        if isinstance(step, dict) and isinstance(step.get("name"), str) and step["name"]:
            instance.add(step["name"])
            scope.instance.add(step["name"])

    method_names: set[str] = set()
    for index, item in enumerate(methods):
        label = f"方法第 {index + 1} 个"
        if not isinstance(item, dict):
            errors.append(f"{label}必须是对象")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not IDENTIFIER.match(name) or name in RESERVED:
            errors.append(f"{label}名称不合法或占用保留字")
            continue
        if name in method_names:
            errors.append(f"方法名重复：{name}")
            continue
        method_names.add(name)
        params: dict[str, str] = {}
        for param in item.get("params") or []:
            if not isinstance(param, dict) or not isinstance(param.get("name"), str):
                errors.append(f"方法 {name} 的参数必须是对象且声明 name")
                continue
            pname = param["name"]
            if not IDENTIFIER.match(pname):
                errors.append(f"方法 {name} 的参数名不合法：{pname}")
            elif pname in params:
                errors.append(f"方法 {name} 的参数重复：{pname}")
            else:
                params[pname] = str(param.get("type") or "text")
        body = _as_list(item.get("body"), f"方法 {name} 的 body", errors)
        scope = _Scope(aliases, instance, set(params))
        for step_index, step in enumerate(body):
            _check_step(f"方法 {name} 第 {step_index + 1} 步", step, scope, errors, remember=True)
        _check_returns(name, item.get("returns") or {}, scope, errors)

    if not methods:
        errors.append("类定义至少需要一个方法，否则排查节点没有可调用的入口")
    return errors


def _as_list(value, label: str, errors: list[str]) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{label}必须是数组")
        return []
    return value


def _check_step(label, step, scope: _Scope, errors: list[str], *, remember: bool) -> None:
    if not isinstance(step, dict):
        errors.append(f"{label}必须是对象")
        return
    name = step.get("name")
    if name is not None:
        if not isinstance(name, str) or not IDENTIFIER.match(name):
            errors.append(f"{label}的 name 必须是合法标识符")
            return
        if name in scope.aliases or name in scope.instance or name in scope.locals \
                or name in scope.params:
            errors.append(f"{label}的 name 与已有名字冲突：{name}")
            return
    call = step.get("call")
    _check_call(label, call, scope, errors)
    _check_args(f"{label}的参数", step.get("args") or {}, scope, errors)
    if remember and isinstance(name, str) and name:
        scope.locals.add(name)


def _check_args(label: str, args, scope: _Scope, errors: list[str]) -> None:
    if not isinstance(args, dict):
        errors.append(f"{label}必须是对象")
        return
    for key, value in args.items():
        if not IDENTIFIER.match(str(key)):
            errors.append(f"{label}的参数名不合法：{key}")
        _check_value(f"{label} {key}", value, scope, errors)


def _check_value(label: str, value, scope: _Scope, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} 必须是表达式对象")
        return
    keys = set(value)
    if "lit" in keys:
        return
    if "input" in keys:
        return
    if "param" in keys:
        if value["param"] not in scope.params:
            errors.append(f"{label} 引用了不存在的参数：{value['param']}")
        return
    if "var" in keys:
        if value["var"] not in scope.instance and value["var"] not in scope.locals:
            errors.append(f"{label} 引用了不存在的变量：{value['var']}")
        return
    if "artifactRoot" in keys:
        return
    if "file" in keys:
        return
    if "list" in keys:
        if not isinstance(value["list"], list):
            errors.append(f"{label} 的 list 必须是数组")
            return
        for index, child in enumerate(value["list"]):
            _check_value(f"{label}[{index}]", child, scope, errors)
        return
    if "object" in keys:
        if not isinstance(value["object"], dict):
            errors.append(f"{label} 的 object 必须是对象")
            return
        for key, child in value["object"].items():
            _check_value(f"{label}.{key}", child, scope, errors)
        return
    if "call" in keys:
        _check_call(label, value["call"], scope, errors)
        _check_args(label, value.get("args") or {}, scope, errors)
        return
    errors.append(f"{label} 不是可识别的表达式（lit/input/param/var/file/artifactRoot/list/object/call）")


def _check_call(label: str, call, scope: _Scope, errors: list[str]) -> None:
    if not isinstance(call, str) or not call.strip():
        errors.append(f"{label}缺少 call")
        return
    head = call.split(".", 1)[0]
    if head in scope.aliases or head in scope.instance or head in scope.locals:
        return
    if head in scope.params:
        errors.append(f"{label}不能把参数 {head} 当对象调用")
        return
    errors.append(f"{label}的第一个名字 {head} 既不是导入别名、也不是已构造的对象")


def _check_returns(name: str, returns, scope: _Scope, errors: list[str]) -> None:
    if not isinstance(returns, dict):
        errors.append(f"方法 {name} 的 returns 必须是对象")
        return
    unknown = set(returns) - RETURN_KEYS - {"reasonFallback"}
    if unknown:
        errors.append(f"方法 {name} 的 returns 含未知字段：{', '.join(sorted(unknown))}")
    for key in RETURN_KEYS:
        if key in returns and not isinstance(returns[key], str):
            errors.append(f"方法 {name} 的 returns.{key} 必须是字符串")
            continue
        if key not in returns:
            continue
        base = str(returns[key]).split(".", 1)[0]
        if base not in scope.locals and base not in scope.instance:
            errors.append(f"方法 {name} 的 returns.{key} 引用了不存在的名字：{base}")


# ---------------------------------------------------------------- 元信息


def initialize_params(start_params: list[dict]) -> list[dict]:
    """initialize 的形参 = 开始节点的全局启动参数。"""
    result = []
    for item in start_params:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        if item.get("scope") == "function":
            continue
        entry = {
            "name": item["name"],
            "type": ANNOTATIONS.get(str(item.get("type") or "text"), "any"),
            "required": "default" not in item,
            "defaultTrusted": True,
        }
        if "default" in item:
            entry["default"] = item["default"]
        result.append(entry)
    return result


def meta(doc: dict, start_params: list[dict]) -> dict:
    """与 `diagnosis_sdk.describe_diagnosis` 同构的元信息，供编译期使用。"""
    spec = definition(doc) or {}
    methods = []
    for item in spec.get("methods") or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        methods.append({"name": item["name"], "params": _method_params(item)})
    methods.sort(key=lambda entry: entry["name"])
    return {
        "protocol": PROTOCOL,
        "version": VERSION,
        "params": initialize_params(start_params),
        "configParams": [],
        "methods": methods,
        "resources": [],
    }


def _method_params(item: dict) -> list[dict]:
    params = []
    for param in item.get("params") or []:
        if not isinstance(param, dict) or not isinstance(param.get("name"), str):
            continue
        entry = {
            "name": param["name"],
            "type": ANNOTATIONS.get(str(param.get("type") or "text"), "any"),
            "required": "default" not in param,
            "defaultTrusted": True,
        }
        if "default" in param:
            entry["default"] = param["default"]
        params.append(entry)
    return params


def alias_calls(doc: dict) -> list[str]:
    """收集以导入别名为根的调用名，供发布期真的去 import 一次做存在性校验。"""
    spec = definition(doc) or {}
    aliases = {
        item.get("alias") for item in spec.get("imports") or [] if isinstance(item, dict)
    }
    found: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            call = value.get("call")
            if isinstance(call, str) and call.split(".", 1)[0] in aliases and call not in found:
                found.append(call)
            for key in ("args", "object"):
                child = value.get(key)
                if isinstance(child, dict):
                    for item in child.values():
                        walk(item)
            if isinstance(value.get("list"), list):
                for item in value["list"]:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for step in spec.get("initialize") or []:
        walk(step)
    for method in spec.get("methods") or []:
        if not isinstance(method, dict):
            continue
        for step in method.get("body") or []:
            walk(step)
    return found


def probe_entries(doc: dict, start_params: list[dict]) -> dict:
    """把类当成一个虚拟工程注入探针表：`@flow:Flow` 与其方法。"""
    value = meta(doc, start_params)
    entries = {
        # 与 probe_doc 的返回口径一致：每个键存的是「参数表」，不是函数描述
        f"{PROJECT}:{CLASS}": value["params"],
        f"{PROJECT}:{CLASS}.__definition__": [
            {"name": "definition", "type": json.dumps(value, ensure_ascii=False, sort_keys=True),
             "required": True, "defaultTrusted": True}
        ],
    }
    for method in value["methods"]:
        entries[f"{PROJECT}:{CLASS}.{method['name']}"] = method["params"]
    return entries


# ---------------------------------------------------------------- 生成


def generate(doc: dict, start_params: list[dict], *, name: str = "") -> str:
    """把声明翻成 Python 源码；确定性输出，同样的 doc 永远得到同样的文本。"""
    errors = validate(doc, start_params)
    if errors:
        raise FlowClassError("；".join(errors))
    spec = definition(doc) or {}
    imports = [item for item in spec.get("imports") or [] if isinstance(item, dict)]
    initialize = [item for item in spec.get("initialize") or [] if isinstance(item, dict)]
    methods = [item for item in spec.get("methods") or [] if isinstance(item, dict)]
    aliases = {item["alias"]: item["module"] for item in imports}

    title = name or str(doc.get("name") or "")
    lines: list[str] = [
        "# -*- coding: utf-8 -*-",
        f'"""由平台根据流程「{title}」的类定义生成。',
        "",
        "发布时随版本冻结。要改行为请改流程的类定义并重新发布，不要直接编辑这个文件。",
        '"""',
        "",
        "import os",
        "",
    ]
    lines.extend(f"import {item['module']} as {item['alias']}" for item in imports)
    lines += [
        "",
        "from diagnosis_sdk import Diagnosis",
        "",
        "",
        "def _at(value, path, default=None):",
        '    """按 a.b.0 取字段；取不到返回 default，避免依据缺字段时整次判断失败。"""',
        '    for part in str(path).split("."):',
        "        try:",
        "            value = value[int(part)] if isinstance(value, (list, tuple)) else value[part]",
        "        except (KeyError, IndexError, TypeError):",
        "            return default",
        "    return value",
        "",
        "",
        f"class {CLASS}(Diagnosis):",
        f'    version = "{VERSION}"',
        "",
        "    def _artifact(self, name):",
        '        """把相对文件名放到本次实例的产物目录。"""',
        "        return os.path.join(self.artifact_root, name)",
        "",
    ]

    params = initialize_params(start_params)
    signature = ", ".join(_signature(param) for param in params)
    lines.append(f"    def initialize(self{', ' if signature else ''}{signature}):")
    lines.extend(f"        self.{param['name']} = {param['name']}" for param in params)
    instance: set[str] = {param["name"] for param in params}
    scope = _Scope(aliases, instance, set(), kind="initialize")
    body = _render_steps(initialize, scope)
    lines.extend(body or ["        pass"])
    lines.append("")
    instance = scope.instance

    for item in methods:
        name_ = item["name"]
        params_ = [param for param in item.get("params") or [] if isinstance(param, dict)]
        signature = ", ".join(_signature(param) for param in params_)
        lines.append(f"    def {name_}(self{', ' if signature else ''}{signature}):")
        scope = _Scope(aliases, instance, {param["name"] for param in params_})
        body = _render_steps(item.get("body") or [], scope)
        lines.extend(body or ["        pass"])
        lines.extend(_render_return(item.get("returns") or {}, scope, instance))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _signature(param: dict) -> str:
    name = str(param.get("name") or "")
    kind = ANNOTATIONS.get(str(param.get("type") or "text"), "")
    text = f"{name}: {kind}" if kind else name
    if "default" in param:
        text += f" = {_literal(param['default'])}"
    return text


def _render_steps(steps, scope: _Scope) -> list[str]:
    lines: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        call = _call_text(step.get("call"), step.get("args") or {}, scope)
        name = step.get("name")
        if isinstance(name, str) and name:
            if scope.kind == "initialize":
                scope.instance.add(name)
                lines.append(f"        self.{name} = {call}")
            else:
                scope.locals.add(name)
                lines.append(f"        {name} = {call}")
        else:
            lines.append(f"        {call}")
    return lines


def _render_return(returns: dict, scope: _Scope, instance: set[str]) -> list[str]:
    if not returns:
        return ['        return {"result": None, "reason": None}']
    lines = ["        return {"]
    for key in ("result", "reason", "figure", "series"):
        if key not in returns:
            continue
        path = str(returns[key])
        base, _, rest = path.partition(".")
        target = f"self.{base}" if base in instance else base
        if key == "reason":
            fallback = str(returns.get("reasonFallback") or "")
            expr = (f"_at({target}, {rest!r}, {fallback!r})" if rest
                    else f"({target} or {fallback!r})")
        else:
            expr = f"_at({target}, {rest!r})" if rest else target
        lines.append(f"            {key!r}: {expr},")
    lines.append("        }")
    return lines


def _call_text(call, args, scope: _Scope) -> str:
    if not isinstance(call, str):
        raise FlowClassError("call 必须是字符串")
    head, _, rest = call.partition(".")
    if head in scope.aliases:
        target = call
    elif head in scope.instance:
        target = f"self.{call}"
    elif head in scope.locals:
        target = call
    else:
        raise FlowClassError(f"无法解析调用：{call}")
    parts = [f"{key}={_value_text(value, scope)}" for key, value in (args or {}).items()]
    return f"{target}({', '.join(parts)})"


def _value_text(value, scope: _Scope) -> str:
    if not isinstance(value, dict):
        raise FlowClassError("参数必须是表达式对象")
    if "lit" in value:
        return _literal(value["lit"])
    if "input" in value:
        name = str(value["input"])
        # initialize 里形参就在本地；方法里统一读实例属性，和自写类的习惯一致
        return f"self.{name}" if name in scope.instance and name not in scope.locals else name
    if "param" in value:
        return str(value["param"])
    if "var" in value:
        name = str(value["var"])
        base = f"self.{name}" if name in scope.instance else name
        path = value.get("path")
        return f"_at({base}, {path!r})" if path else base
    if "artifactRoot" in value:
        return "self.artifact_root"
    if "file" in value:
        template = str(value["file"])
        return (f"self._artifact(f{template!r})" if "{" in template
                else f"self._artifact({template!r})")
    if "list" in value:
        return "[" + ", ".join(_value_text(item, scope) for item in value["list"]) + "]"
    if "object" in value:
        return "{" + ", ".join(
            f"{key!r}: {_value_text(item, scope)}" for key, item in value["object"].items()
        ) + "}"
    if "call" in value:
        return _call_text(value["call"], value.get("args") or {}, scope)
    raise FlowClassError("不支持的参数表达式")


def _literal(value) -> str:
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{key!r}: {_literal(item)}" for key, item in value.items()) + "}"
    raise FlowClassError(f"字面量类型不支持：{type(value).__name__}")


def module_name(flow_id: str) -> str:
    """生成模块名；只保留 id 里的安全字符。"""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", str(flow_id))
    return f"flowclass_{safe}" if safe else "flowclass"

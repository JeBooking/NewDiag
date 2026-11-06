"""函数签名指纹：发布时记录，执行前比对，防止代码偷偷改了参数。

编译期还负责「按真实参数名绑定」，所以这里的 probe 结果有两种用途：
`probe_doc` 给出参数表供 compiler 过滤，`collect_signatures` 给出指纹。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

PROBE = Path(__file__).resolve().parent.parent / "worker" / "probe.py"
PROBE_TIMEOUT = 30.0


def probe_doc(doc: dict) -> dict:
    """扫描 doc 里的绑定，按 (工程, 模块) 去重后探测函数签名。

    返回 {工程:函数: [{"name","type","required","default"}...]}；组合配置的键是
    `工程:模块:接口`，与 compiler 的 _composition_key 同一口径。
    """
    result: dict[str, object] = {}
    cache: dict[tuple[str, str], tuple[list[dict] | None, str]] = {}
    started = time.perf_counter()
    for binding in _document_bindings(doc):
        project_name = str(binding.get("project") or "")
        function_name = str(binding.get("function") or "")
        module_name = str(binding.get("module") or "")
        if not project_name or not function_name:
            continue

        cache_key = (project_name, module_name)
        if cache_key not in cache:
            project = _load_project(project_name)
            if project is None:
                cache[cache_key] = (None, "工程未登记")
            else:
                cache[cache_key] = _probe_project(project, module_name)
        functions, error = cache[cache_key]
        if functions is None:
            result.setdefault(project_name, {"error": error or "探测失败"})
            continue
        from app.worker.probe import expanded_functions
        functions = expanded_functions(functions)

        key = (f"{project_name}:{module_name}:{function_name}" if module_name
               else f"{project_name}:{function_name}")
        matched = [f for f in functions if str(f.get("name")) == function_name]
        if not matched:
            result[key] = {"error": f"工程 {project_name} 里找不到接口 {function_name}"}
            continue
        func = matched[0]
        if binding.get("_function_pipeline") and (func.get("error") or func.get("pipelineError")):
            result[key] = {"error": func.get("error") or func["pipelineError"]}
            continue
        if binding.get("_composition"):
            # 组合模式允许接口带 **kwargs：只绑定显式列出的参数，扩展参数另行配置。
            result[key] = _param_list(func)
            continue
        if binding.get("protocol") == "diagnosis/v1":
            try:
                _validate_diagnosis_binding(binding, func)
            except ValueError as exc:
                result[key] = {"error": str(exc)}
                continue
        elif binding.get("protocol") == "investigation/v1":
            try:
                _validate_adapter_binding(binding, func)
            except ValueError as exc:
                result[key] = {"error": str(exc)}
                continue
        elif func.get("protocol") == "investigation/v1":
            result[key] = {"error": "四阶段适配器必须使用 investigation/v1 绑定"}
            continue
        result[key] = _param_list(func)
    failed = [k for k, v in result.items() if isinstance(v, dict) and v.get("error")]
    logger.info(
        "签名探测：%d 个函数，%d 个失败，%d 个工程，%.0fms%s",
        sum(1 for v in result.values() if isinstance(v, list)),
        len(failed),
        len(cache),
        (time.perf_counter() - started) * 1000,
        f"，失败：{failed}" if failed else "",
    )
    return result


def _document_bindings(doc):
    diagnosis = doc.get("diagnosis")
    if isinstance(diagnosis, dict):
        yield diagnosis
        yield {"project": diagnosis.get("project"),
               "function": str(diagnosis.get("function") or "") + ".__definition__"}
        for node in doc.get("nodes") or []:
            if isinstance(node, dict) and node.get("method"):
                yield {"project": diagnosis.get("project"),
                       "function": str(diagnosis.get("function") or "") + "." + str(node["method"])}
    composition = doc.get("composition")
    if isinstance(composition, dict):
        owners = _composition_owners(composition)
        for item in composition.get("objects") or []:
            if isinstance(item, dict):
                yield {"project": item.get("project"), "module": item.get("module"),
                       "function": item.get("entry"), "_composition": True}
        for step in _composition_steps(composition):
            yield _composition_binding(step, owners)
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("pipeline"), dict):
            for stage in ("acquire", "parse", "present", "judge"):
                binding = node["pipeline"].get(stage)
                if isinstance(binding, dict):
                    yield {**binding, "_function_pipeline": True}
        elif isinstance(node.get("binding"), dict):
            yield node["binding"]


def _composition_steps(composition: dict):
    groups = []
    groups.extend(item for item in composition.get("resources") or [] if isinstance(item, dict))
    operations = composition.get("operations")
    groups.extend(item for item in (operations.values() if isinstance(operations, dict) else [])
                  if isinstance(item, dict))
    for group in groups:
        for step in group.get("steps") or []:
            if isinstance(step, dict):
                yield step


def _composition_owners(composition: dict) -> dict[str, str]:
    """对象名 → 类名：声明过的对象取构造器，saveAs 产出的对象按调用形式推断。"""
    owners: dict[str, str] = {}
    for item in composition.get("objects") or []:
        if isinstance(item, dict) and item.get("id"):
            owners[str(item["id"])] = str(item.get("entry") or "")
    for step in _composition_steps(composition):
        name = str(step.get("saveAs") or "")
        if not name:
            continue
        explicit = str(step.get("owner") or "")
        if explicit:
            owners[name] = explicit
            continue
        kind = str(step.get("kind") or "function")
        function = str(step.get("function") or "")
        if kind == "method":
            owners[name] = owners.get(str(step.get("target") or ""), "")
        elif kind == "constructor":
            owners[name] = function
        else:
            owners[name] = function.rsplit(".", 1)[0] if "." in function else ""
    return owners


def _composition_binding(step: dict, owners: dict[str, str]) -> dict:
    kind = str(step.get("kind") or "function")
    function = str(step.get("function") or "")
    if kind == "method":
        owner = str(step.get("owner") or "") or owners.get(str(step.get("target") or ""))
        if owner:
            function = f"{owner}.{function}"
    return {"project": step.get("project"), "module": step.get("module"),
            "function": function, "_composition": True}


def _validate_diagnosis_binding(binding, function):
    if function.get("protocol") != "diagnosis/v1":
        raise ValueError("流程入口不是诊断业务类")
    if binding.get("version") != function.get("version"):
        raise ValueError("诊断类版本不一致，请重新选择并发布")
    config = binding.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError("诊断类配置必须为对象")
    params = function.get("configParams") or []
    names = {item["name"] for item in params}
    missing = {item["name"] for item in params if item.get("required") and item["name"] not in config}
    if set(config) - names or missing:
        raise ValueError(f"诊断类配置错误：缺少 {sorted(missing)}，未声明 {sorted(set(config) - names)}")


def _validate_adapter_binding(binding: dict, func: dict) -> None:
    if func.get("protocol") != "investigation/v1":
        raise ValueError("所选入口不是四阶段排查适配器")
    if binding.get("version") != func.get("version"):
        raise ValueError("适配器版本不一致，请重新选择适配器后发布")
    config = binding.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError("适配器初始化配置必须为对象")
    params = func.get("configParams") or []
    names = {p["name"] for p in params}
    unknown = set(config) - names
    missing = {p["name"] for p in params if p.get("required") and p["name"] not in config}
    if unknown or missing:
        raise ValueError(f"初始化配置错误：缺少 {sorted(missing)}，未声明 {sorted(unknown)}")


def collect_signatures(
    plan: dict, probed: dict | None = None, *, cancelled: Callable[[], bool] | None = None
) -> dict[str, str]:
    """对 plan 里出现的每个 project:function 取当前签名哈希。

    probed 传 probe_doc 的结果时复用参数表，不再起子进程。
    取不到的项目记空字符串，不抛异常——发布/启动不应被工程环境问题整死。
    """
    result: dict[str, str] = {}
    projects: dict[tuple[str, str], object] = {}
    for entry in _plan_entries(plan):
        if cancelled is not None and cancelled():
            break
        key = entry["key"]
        project_name, module_name, function_name = entry["project"], entry["module"], entry["function"]

        if probed is not None:
            value = probed.get(key)
            if isinstance(value, list):
                result[key] = _hash_params(function_name, value)
            else:
                result[key] = ""
            continue

        cache_key = (project_name, module_name)
        if cache_key not in projects:
            projects[cache_key] = _load_project(project_name)
        project = projects[cache_key]
        if project is None:
            result[key] = ""
            continue
        functions, _ = _probe_project(project, module_name)
        if functions is None:
            result[key] = ""
            continue
        result[key] = _hash_function(functions, function_name)
    return result


def compare_signatures(plan: dict, current: dict[str, str] | None = None) -> list[dict]:
    """返回差异列表；空列表表示可以启动。空哈希视为未知，不阻断。"""
    published = _published_signatures(plan)
    if not published:
        return []  # 发布时没留指纹，无从比对
    if current is None:
        current = collect_signatures(plan)
    diffs: list[dict] = []
    for key, expected in sorted(published.items()):
        actual = current.get(key)
        if actual is None:
            diffs.append({"key": key, "status": "removed", "published": expected, "current": None})
            continue
        if not expected or not actual:
            continue
        if expected != actual:
            diffs.append(
                {"key": key, "status": "changed", "published": expected, "current": actual}
            )
    for key, actual in sorted(current.items()):
        if key not in published and actual:
            diffs.append({"key": key, "status": "added", "published": None, "current": actual})
    return diffs


def canonical_signature(functions: list[dict]) -> str:
    """规范化文本：name(param:type=default, ...)，按函数名排序。

    哈希不含返回注解：probe_doc 只保留参数表，两条路径必须算出同一个值。
    """
    parts = []
    for func in sorted(functions, key=lambda item: str(item.get("name", ""))):
        parts.append(_canonical_params(str(func.get("name", "")), _param_list(func)))
    return ";".join(parts)


# ---------------------------------------------------------------- 内部


def _canonical_params(name: str, params: list[dict]) -> str:
    parts = []
    for param in params:
        text = f"{param.get('name')}:{param.get('type') or 'any'}"
        if not param.get("required", True):
            text += f"={_format_default(param.get('default'))}"
        parts.append(text)
    return f"{name}({', '.join(parts)})"


def _param_list(func: dict) -> list[dict]:
    params = []
    for param in func.get("params") or []:
        if not isinstance(param, dict):
            continue
        params.append(
            {
                "name": str(param.get("name") or ""),
                "type": str(param.get("type") or "any"),
                "required": bool(param.get("required", True)),
                "default": param.get("default"),
                # 探针用 repr 兜底的默认值不可信，发布侧不拿它填启动上下文
                "defaultTrusted": bool(param.get("defaultTrusted", True)),
            }
        )
    return params


def _hash_params(name: str, params: list[dict]) -> str:
    text = _canonical_params(name, params)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_function(discovered: list[dict], function_name: str) -> str:
    from app.worker.probe import expanded_functions
    discovered = expanded_functions(discovered)
    matched = [f for f in discovered if str(f.get("name")) == function_name]
    if not matched:
        return ""
    return _hash_params(function_name, _param_list(matched[0]))


def _format_default(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)


def _plan_entries(plan: dict) -> list[dict]:
    """计划里所有需要指纹的接口。

    普通绑定沿用 `工程:函数`；组合配置用 `工程:模块:接口`，因为同一个工程可以有多个模块。
    """
    entries: list[dict] = []
    seen: set[str] = set()

    def add(project, function, module: str = "") -> None:
        project, function = str(project or ""), str(function or "")
        if not project or not function:
            return
        key = f"{project}:{module}:{function}" if module else f"{project}:{function}"
        if key in seen:
            return
        seen.add(key)
        entries.append({"key": key, "project": project, "module": module, "function": function})

    diagnosis = plan.get("diagnosis")
    if diagnosis:
        add(diagnosis.get("project"), diagnosis.get("function"))
        add(diagnosis.get("project"), str(diagnosis.get("function") or "") + ".__definition__")
    composition = plan.get("composition")
    if isinstance(composition, dict):
        for item in (composition.get("objects") or {}).values():
            add(item.get("project"), item.get("entry"), str(item.get("module") or ""))
        groups = list((composition.get("resources") or {}).values())
        groups += list((composition.get("operations") or {}).values())
        for group in groups:
            for step in group.get("steps") or []:
                add(step.get("project"), step.get("interface") or step.get("function"),
                    str(step.get("module") or ""))
    for step in (plan.get("steps") or {}).values():
        if not isinstance(step, dict):
            continue
        if step.get("diagnosisMethod") and diagnosis:
            add(diagnosis.get("project"),
                f"{diagnosis.get('function')}.{step['diagnosisMethod']['name']}")
        calls = [step.get("call")]
        pipeline = step.get("pipeline") or {}
        calls.extend(pipeline.get(stage) for stage in ("acquire", "parse", "present", "judge"))
        for call in calls:
            if call:
                add(call.get("project"), call.get("function"))
    return entries


def _published_signatures(plan: dict) -> dict[str, str]:
    raw = plan.get("signature") or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _load_project(name: str):
    """按 id 或名称找登记工程；模型/数据库不可用时返回 None。"""
    try:
        from app import models
        from app.db import SessionLocal
    except Exception:  # noqa: BLE001 - 探针可选能力，缺依赖就退化为未知
        return None
    db = None
    try:
        db = SessionLocal()
        project = db.get(models.Project, name)
        if project is None:
            project = db.query(models.Project).filter(models.Project.name == name).first()
        return project
    except Exception:  # noqa: BLE001
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


def _probe_project(project, module: str = "") -> tuple[list[dict] | None, str]:
    """起子进程探测一个工程，返回 (函数表, 错误描述)。module 为空时用登记模块。"""
    config = {
        "path": getattr(project, "path", "") or "",
        "module": module or getattr(project, "module", "") or "",
        "factory": getattr(project, "factory", "") or "",
        # 指纹要覆盖组合调用用到的构造器与方法，必须走完整目录。
        "catalog": True,
    }
    if not config["module"]:
        return None, "工程没有登记模块名"
    python = getattr(project, "python", None) or sys.executable
    cwd = config["path"] or None
    # stdio 编码在解释器启动时就固定了，只能启动前指定；子进程里改环境变量不起作用。
    # 只动 stdio：PYTHONUTF8=1 会连 open() 的默认编码一起改成 UTF-8，打挂读 GBK 文件的业务脚本
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    fd, config_path = tempfile.mkstemp(prefix="diag-probe-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False)
        completed = subprocess.run(
            [python, str(PROBE), config_path],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=PROBE_TIMEOUT,
        )
    except UnicodeDecodeError as exc:
        return None, f"探针输出不是 UTF-8：{exc}"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"探针启动失败：{exc}"
    finally:
        try:
            os.remove(config_path)
        except OSError:
            pass

    payload = None
    for line in reversed((completed.stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payload = parsed
            break
    if payload is None:
        return None, (completed.stderr or "探针没有输出可解析结果").strip()[:500]

    functions = payload.get("functions")
    if not isinstance(functions, list):
        return None, str(payload.get("error") or "探针输出缺少 functions")[:500]
    error = str(payload.get("error") or "").strip()
    if error and not functions:
        return None, error[:500]
    return functions, ""

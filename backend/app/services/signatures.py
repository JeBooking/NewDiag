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
    """扫描 doc 里的 binding，按工程去重后探测函数签名。

    返回 {工程:函数: [{"name","type","required","default"}...]}；
    单个工程探测失败时该工程记 {"error": "..."}，函数不存在时该函数记 {"error": "..."}。
    """
    result: dict[str, object] = {}
    cache: dict[str, tuple[list[dict] | None, str]] = {}
    started = time.perf_counter()
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        binding = node.get("binding")
        if not isinstance(binding, dict):
            continue
        project_name = str(binding.get("project") or "")
        function_name = str(binding.get("function") or "")
        if not project_name or not function_name:
            continue

        if project_name not in cache:
            project = _load_project(project_name)
            if project is None:
                cache[project_name] = (None, "工程未登记")
            else:
                cache[project_name] = _probe_project(project)
        functions, error = cache[project_name]
        if functions is None:
            result.setdefault(project_name, {"error": error or "探测失败"})
            continue

        key = f"{project_name}:{function_name}"
        matched = [f for f in functions if str(f.get("name")) == function_name]
        if not matched:
            result[key] = {"error": f"工程 {project_name} 里找不到函数 {function_name}"}
            continue
        result[key] = _param_list(matched[0])
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


def collect_signatures(
    plan: dict, probed: dict | None = None, *, cancelled: Callable[[], bool] | None = None
) -> dict[str, str]:
    """对 plan 里出现的每个 project:function 取当前签名哈希。

    probed 传 probe_doc 的结果时复用参数表，不再起子进程。
    取不到的项目记空字符串，不抛异常——发布/启动不应被工程环境问题整死。
    """
    result: dict[str, str] = {}
    projects: dict[str, object] = {}
    for key in _plan_keys(plan):
        if cancelled is not None and cancelled():
            break
        project_name, function_name = _split_key(key)

        if probed is not None:
            entry = probed.get(key)
            if isinstance(entry, list):
                result[key] = _hash_params(function_name, entry)
            else:
                result[key] = ""
            continue

        if project_name not in projects:
            projects[project_name] = _load_project(project_name)
        project = projects[project_name]
        if project is None:
            result[key] = ""
            continue
        functions, _ = _probe_project(project)
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
    matched = [f for f in discovered if str(f.get("name")) == function_name]
    if not matched:
        return ""
    return _hash_params(function_name, _param_list(matched[0]))


def _format_default(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)


def _plan_keys(plan: dict) -> list[str]:
    keys: list[str] = []
    for step in (plan.get("steps") or {}).values():
        call = step.get("call") if isinstance(step, dict) else None
        if not call:
            continue
        key = f"{call.get('project', '')}:{call.get('function', '')}"
        if key not in keys:
            keys.append(key)
    return keys


def _split_key(key: str) -> tuple[str, str]:
    project, _, function = key.partition(":")
    return project, function


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


def _probe_project(project) -> tuple[list[dict] | None, str]:
    """起子进程探测一个工程，返回 (函数表, 错误描述)。"""
    config = {
        "path": getattr(project, "path", "") or "",
        "module": getattr(project, "module", "") or "",
        "factory": getattr(project, "factory", "") or "",
    }
    if not config["module"]:
        return None, "工程没有登记模块名"
    python = getattr(project, "python", None) or sys.executable
    cwd = config["path"] or None
    fd, config_path = tempfile.mkstemp(prefix="diag-probe-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False)
        completed = subprocess.run(
            [python, str(PROBE), config_path],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT,
        )
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

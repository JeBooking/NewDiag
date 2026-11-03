"""工程接口发现：起子进程跑 app/worker/probe.py，读回函数签名。

probe 脚本由引擎层提供，缺失或崩溃都不应让 API 冒 500，只在返回值里带 error。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.db import BACKEND_DIR

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 60


def probe_script() -> Path:
    return BACKEND_DIR / "app" / "worker" / "probe.py"


def _field(project: Any, name: str) -> Any:
    if isinstance(project, dict):
        return project.get(name)
    return getattr(project, name, None)


def resolve_python(project: Any) -> str:
    raw = _field(project, "python")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    # 未登记解释器时用当前进程的解释器，即 backend/.venv 里的那个
    return sys.executable


def _normalize(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"functions": [], "error": "probe 输出不是 JSON 对象"}
    raw = payload.get("functions")
    functions: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            params = []
            for param in item.get("params") or []:
                if isinstance(param, dict):
                    params.append(
                        {
                            "name": param.get("name", ""),
                            "type": param.get("type"),
                            "default": param.get("default"),
                            "required": bool(param.get("required", False)),
                        }
                    )
                elif isinstance(param, str):
                    params.append({"name": param, "type": None, "default": None, "required": True})
            functions.append(
                {
                    "name": item["name"],
                    "params": params,
                    "returns": item.get("returns"),
                }
            )
            for key in ("protocol", "version", "configParams", "manualFields",
                        "automaticAcquisition", "automaticJudgment"):
                if key in item:
                    functions[-1][key] = item[key]
    error = payload.get("error")
    return {"functions": functions, "error": str(error) if error else None}


def _extract_json(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        raise ValueError("probe 无输出")
    try:
        return json.loads(text)
    except ValueError:
        pass
    # 工程里可能有 print 噪声，从后往前找第一行可解析的 JSON
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith(("{", "[")):
            continue
        try:
            return json.loads(line)
        except ValueError:
            continue
    raise ValueError("probe 输出无法解析为 JSON")


def probe(project: Any) -> dict[str, Any]:
    script = probe_script()
    if not script.is_file():
        logger.warning("缺少接口发现脚本：%s", script)
        return {"functions": [], "error": "app/worker/probe.py 尚未就绪"}

    project_path = _field(project, "path") or ""
    payload = {
        "name": _field(project, "name") or "",
        "path": project_path,
        "module": _field(project, "module") or "",
        "factory": _field(project, "factory"),
    }

    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="newdiag-probe-", delete=False, encoding="utf-8"
    )
    try:
        json.dump(payload, handle, ensure_ascii=False)
        handle.close()
        request_path = handle.name
    except OSError as exc:
        handle.close()
        logger.warning("写 probe 请求文件失败：%s", exc)
        return {"functions": [], "error": f"准备请求失败：{exc}"}

    cwd = Path(project_path) if project_path and Path(project_path).is_dir() else BACKEND_DIR
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    parts = [str(BACKEND_DIR)]
    if project_path:
        parts.append(str(Path(project_path)))
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)

    try:
        completed = subprocess.run(
            [resolve_python(project), str(script), request_path],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning("接口发现超时：%s", project_path)
        return {"functions": [], "error": f"接口发现超时（{PROBE_TIMEOUT_SECONDS}s）"}
    except OSError as exc:
        logger.warning("接口发现启动失败：%s", exc)
        return {"functions": [], "error": f"解释器启动失败：{exc}"}
    finally:
        try:
            os.unlink(request_path)
        except OSError:
            pass

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-8:]
        message = " / ".join(tail) or f"probe 退出码 {completed.returncode}"
        logger.warning("接口发现失败（%s）：%s", project_path, message)
        return {"functions": [], "error": message}

    try:
        return _normalize(_extract_json(completed.stdout))
    except ValueError as exc:
        logger.warning("接口发现输出异常（%s）：%s", project_path, exc)
        return {"functions": [], "error": str(exc)}

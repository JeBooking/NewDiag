"""把已绑定参数的业务流程生成可阅读的 Python 节点和分支。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

from sqlalchemy import select

from app import models
from app.db import data_dir
from app.services.flow_format import expand
from app.services.signatures import _document_bindings, _probe_project, _param_list
from app.services.compiler import compile_doc
from app.worker.probe import expanded_functions

logger = logging.getLogger(__name__)


def generate(plan):
    steps = plan["steps"]
    names = {key: "node_" + hashlib.sha256(key.encode()).hexdigest()[:16] for key in steps}
    lines = [
        '"""生成的诊断流程；命令行运行 python flow.py --help。"""',
        "from runtime.generated_runtime import main", "",
    ]
    for node_id, step in steps.items():
        lines += [f"def {names[node_id]}(ctx):", f"    branch = ctx.execute({node_id!r}, {step!r})"]
        for branch, targets in (step.get("next") or {}).items():
            lines += [f"    if branch == {branch!r}:", f"        return {targets!r}"]
        lines += ["    return []", ""]
    lines += ["NODES = {"]
    lines += [f"    {key!r}: {name}," for key, name in names.items()]
    lines += ["}", f"ENTRY = {plan['entry']!r}", "", 'if __name__ == "__main__":',
              "    raise SystemExit(main(NODES, ENTRY))", ""]
    return "\n".join(lines)


def prepare(db, instance, release, runtime):
    """启动时固定工程源码，之后的进程和回退均使用此目录。"""
    directory = data_dir() / "executions" / instance.id
    directory.mkdir(parents=True, exist_ok=True)
    revision = db.get(models.Revision, release.revision_id)
    if revision is None:
        raise ValueError("发布流程定义不存在")
    doc = expand(json.loads(revision.json))
    projects, signatures = {}, {}
    for binding in _document_bindings(doc):
        name = binding["project"]
        if name in projects:
            continue
        project = db.scalar(select(models.Project).where(models.Project.name == name))
        if project is None:
            raise ValueError(f"接入工程不存在：{name}")
        key = hashlib.sha256(name.encode()).hexdigest()[:16]
        source = Path(project.path).resolve()
        target = directory / "modules" / key
        ignored = shutil.ignore_patterns(
            ".git", ".venv", "venv", "node_modules", "__pycache__", ".codex", ".pytest_cache",
        )
        storage = data_dir().resolve()
        if source == storage or source.is_relative_to(storage):
            raise ValueError("接入工程不能位于平台运行数据目录中")

        def ignore(path, names):
            return set(ignored(path, names)) | {
                name for name in names if (Path(path) / name).resolve() == storage
            }

        shutil.copytree(source, target, ignore=ignore)
        project_copy = {"name": name, "path": str(target), "module": project.module,
                        "factory": project.factory, "python": project.python}
        logger.info("实例 %s 接口探测：工程=%s 源=%s 副本=%s module=%s python=%s",
                    instance.id, name, source, target, project.module, project.python or "后端默认")
        from types import SimpleNamespace
        if doc.get('composition') is not None:
            projects[name] = {**project_copy, 'factory': ''}
            continue
        discovered, error = _probe_project(SimpleNamespace(**project_copy))
        if discovered is None:
            raise ValueError(
                f"模块 {name} 探测失败（副本目录={target}，module={project.module}，"
                f"python={project.python or '后端默认'}）：{error}"
            )
        for item in expanded_functions(discovered):
            signatures[f"{name}:{item['name']}"] = _param_list(item)
            for candidate in _document_bindings(doc):
                if candidate["project"] == name and candidate["function"] == item["name"]:
                    if candidate.get("protocol"):
                        candidate["version"] = item.get("version", candidate.get("version"))
        projects[name] = project_copy
    if doc.get('composition') is not None:
        from .composition import probe_configuration
        from types import SimpleNamespace
        signatures = probe_configuration(doc['composition'], {
            name: SimpleNamespace(**spec) for name, spec in projects.items()
        })
        errors = [f"{name}: {entry['error']}" for name, entry in signatures.items() if isinstance(entry, dict)]
        if errors:
            raise ValueError('组合接口探测失败：' + '；'.join(errors))
    plan = compile_doc(doc, instance.flow_id, release.version_no, functions=signatures)
    # 发布时的 plan 是派生产物，不能让旧编译器的内部格式成为 flow 升级的前提。
    plan["mode"] = runtime["mode"]
    package = Path(__file__).resolve().parents[1] / "worker"
    shutil.copytree(package, directory / "runtime", ignore=shutil.ignore_patterns("__pycache__"))
    (directory / "flow.py").write_text(generate(plan), encoding="utf-8")
    snapshot = db.get(models.Snapshot, instance.snapshot_id)
    configuration = {
        "projects": projects, "diagnosis": plan.get("diagnosis"), "composition": plan.get("composition"), "runtime": runtime,
        "input": json.loads(snapshot.payload_json) if snapshot else {},
        "artifactRoot": str(data_dir() / "artifacts" / hashlib.sha256(instance.id.encode()).hexdigest()),
        "fileRoot": str(data_dir() / "files"),
        "meta": {node["id"]: node for node in doc["nodes"]},
    }
    (directory / "configuration.json").write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
    return plan, directory

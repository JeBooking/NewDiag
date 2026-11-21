"""把已绑定参数的业务流程生成可阅读的 Python 节点和分支。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path

from sqlalchemy import select

from app import models
from app.db import data_dir
from app.services.flow_format import expand
from app.services.signatures import projects_in
from app.services.compiler import compile_doc

logger = logging.getLogger(__name__)


def penetrate(plan):
    """自动模式下分类节点等于不存在：把它的后继并回前驱的出线。

    保留前驱原有的分支键与顺序位置；分类节点本身不进 steps，因此也不进 NODES，
    运行期不会为它建 token 或 run。手动模式不调用这里，直接使用带分类的 plan。
    """
    steps = plan.get("steps") or {}
    categories = {
        node_id: list((step.get("next") or {}).get("default") or [])
        for node_id, step in steps.items()
        if step.get("kind") == "category"
    }
    if not categories:
        return plan

    def expand(target):
        # 分类节点只能连排查，不会嵌套；seen 只为防御性去重
        seen, queue, found = set(), [target], []
        while queue:
            node_id = queue.pop(0)
            if node_id in seen:
                continue
            seen.add(node_id)
            if node_id in categories:
                queue.extend(categories[node_id])
            else:
                found.append(node_id)
        return found

    penetrated = {}
    for node_id, step in steps.items():
        if step.get("kind") == "category":
            continue
        next_map = {}
        for branch, targets in (step.get("next") or {}).items():
            flattened = []
            for target in targets:
                for item in expand(target):
                    if item not in flattened:
                        flattened.append(item)
            next_map[branch] = flattened
        penetrated[node_id] = {**step, "next": next_map}
    return {**plan, "steps": penetrated}


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


def prepare(db, instance, release, runtime, resume=None):
    """启动时固定工程源码，之后的进程和回退均使用此目录。

    resume 供手动转自动使用：给定时把开始节点的出线改成这些节点，
    先跑完开始节点的构造槽与拉取槽，再跳回手动停住的位置继续。
    """
    directory = data_dir() / "executions" / instance.id
    directory.mkdir(parents=True, exist_ok=True)
    revision = db.get(models.Revision, release.revision_id)
    if revision is None:
        raise ValueError("发布流程定义不存在")
    doc = expand(json.loads(revision.json))
    projects = {}
    storage = data_dir().resolve()
    snapshot = _release_modules(db, release, doc, storage)
    for name in sorted(projects_in(doc)):
        project = db.scalar(select(models.Project).where(models.Project.name == name))
        if project is None:
            raise ValueError(f"接入工程不存在：{name}")
        key = hashlib.sha256(name.encode()).hexdigest()[:16]
        target = snapshot / key
        # 组合模式在流程里配置构造器或工厂，登记行的 factory 不参与生成
        projects[name] = {"name": name, "path": str(target), "module": project.module,
                          "factory": "", "python": project.python}
        logger.info("实例 %s 引用发布快照：工程=%s 副本=%s module=%s python=%s",
                    instance.id, name, target, project.module, project.python or "后端默认")
    from types import SimpleNamespace
    from .slots import probe_slots
    signatures = probe_slots(doc, {
        name: SimpleNamespace(**spec) for name, spec in projects.items()
    }) if projects else {}
    errors = [f"{name}: {entry['error']}" for name, entry in signatures.items() if isinstance(entry, dict)]
    if errors:
        raise ValueError("槽位接口探测失败：" + "；".join(errors))
    plan = compile_doc(doc, instance.flow_id, release.version_no, functions=signatures)
    if resume:
        entry = plan["entry"]
        plan["steps"][entry]["next"] = {"default": list(dict.fromkeys(resume))}
    plan = penetrate(plan)
    package = Path(__file__).resolve().parents[1] / "worker"
    shutil.copytree(package, directory / "runtime", ignore=shutil.ignore_patterns("__pycache__"))
    (directory / "flow.py").write_text(generate(plan), encoding="utf-8")
    snapshot = db.get(models.Snapshot, instance.snapshot_id)
    configuration = {
        "projects": projects, "runtime": runtime,
        "input": json.loads(snapshot.payload_json) if snapshot else {},
        "artifactRoot": str(data_dir() / "artifacts" / hashlib.sha256(instance.id.encode()).hexdigest()),
        "fileRoot": str(data_dir() / "files"),
        "meta": {node["id"]: node for node in doc["nodes"]},
    }
    (directory / "configuration.json").write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
    return plan, directory


_IGNORED = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".codex", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
)


def _extra_ignores(source: Path) -> list[str]:
    """项目根放 .newdiagignore 可以排除大目录（数据集、缓存），每行一个通配符。"""
    spec = source / ".newdiagignore"
    if not spec.is_file():
        return []
    try:
        lines = spec.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _release_modules(db, release, doc, storage: Path) -> Path:
    """一个发布版本只留一份工程快照，所有实例共用，避免每次执行都整目录复制。"""
    root = data_dir() / "modules" / release.id
    root.mkdir(parents=True, exist_ok=True)
    for name in sorted(projects_in(doc)):
        project = db.scalar(select(models.Project).where(models.Project.name == name))
        if project is None:
            raise ValueError(f"接入工程不存在：{name}")
        key = hashlib.sha256(name.encode()).hexdigest()[:16]
        target = root / key
        if target.is_dir():
            continue
        source = Path(project.path).resolve()
        if source == storage or source.is_relative_to(storage):
            raise ValueError("接入工程不能位于平台运行数据目录中")

        patterns = _IGNORED
        extra = _extra_ignores(source)
        if extra:
            patterns = lambda path, names, extra=extra: set(_IGNORED(path, names)) | set(
                shutil.ignore_patterns(*extra)(path, names))

        def ignore(path, names, source=source, patterns=patterns):
            return set(patterns(path, names)) | {
                item for item in names if (Path(path) / item).resolve() == storage
            }

        # 两个实例同时启动时只让一个复制，另一个等它复制完再复用
        lock = root / f".{key}.lock"
        deadline = time.monotonic() + 120
        while True:
            try:
                os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                if target.is_dir():
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(f"等待工程快照超时：{name}")
                time.sleep(0.1)
        try:
            if not target.is_dir():
                shutil.copytree(source, target, ignore=ignore)
        finally:
            lock.unlink(missing_ok=True)
    return root

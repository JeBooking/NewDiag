"""只导出实例启动时固定的代码，不携带运行数据和进程现场。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

from app.db import data_dir

MAX_CODE_BYTES = 200 * 1024 * 1024
SOURCE_SUFFIXES = {'.py', '.pyi'}

README = """# 生成的诊断代码

此包来自实例启动时固定的代码副本，不重新读取当前登记工程，也不启动新诊断。

目录：flow.py（流程）、runtime/（运行库）、modules/（业务 Python 源码）、
configuration.json（运行配置模板）、inputs.json（启动参数说明）。

本包不含 Python、第三方依赖、上传文件、业务数据文件、实际启动输入、运行结果或 pickle 现场。
仅收集 .py/.pyi 源码；原模块需要的模型、模板、原生库或其他资源请自行准备。

运行前：
1. 准备 Python 及原业务工程依赖。默认各工程使用当前 Python；需要不同解释器时，
   修改 configuration.json 的 projects.<工程名>.python。
2. 按 inputs.json 填写 configuration.json 的 input。null 是待填占位，不是原实例输入。
   全局输入在 input.global，函数级输入在 input.scope.<函数名>。
3. 如果需要文件，请另行准备。使用上传引用的接口需要在 files/<摘要前两位>/<摘要>
   放入文件，并填写对应 {"hash":"sha256:<SHA256摘要>"} 引用。
4. 在解压目录运行：python flow.py --mode serial
   并行运行：python flow.py --mode parallel --max-parallel 2

这是一次重新执行，人工输入由终端接收。它不接管平台活动实例，也不恢复原实例现场。
输出写入 local-artifacts；不要使用内部 --worker 参数。输入及源码中的业务常量仍由使用者维护。
"""


def build_code_export(instance_id: str, inputs: list[dict]):
    directory = data_dir() / 'executions' / instance_id
    configuration_file = directory / 'configuration.json'
    flow = directory / 'flow.py'
    if not configuration_file.is_file() or not flow.is_file():
        raise ValueError('该实例尚未生成执行代码，或生成目录已不存在')
    try:
        configuration = json.loads(configuration_file.read_text(encoding='utf-8'))
        projects = configuration['projects']
        if not isinstance(projects, dict):
            raise ValueError('工程配置无效')
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError('生成代码的配置缺失或损坏，无法导出') from exc

    sources = [(flow, 'flow.py')]
    roots = [(directory / 'runtime', 'runtime')]
    normalized = {}
    for name, spec in projects.items():
        key = hashlib.sha256(name.encode()).hexdigest()[:16]
        roots.append((directory / 'modules' / key, f'modules/{key}'))
        normalized[name] = {**spec, 'path': f'modules/{key}', 'python': None}
    for root, prefix in roots:
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f'代码目录缺失：{prefix}')
        count = 0
        for path in sorted(root.rglob('*')):
            if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
                continue
            relative = path.relative_to(root)
            if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                    or any(part in {'.git', '.venv', 'venv', '__pycache__', 'node_modules'}
                           for part in relative.parts)):
                continue
            sources.append((path, f'{prefix}/{relative.as_posix()}'))
            count += 1
        if count == 0:
            raise ValueError(f'代码目录没有 Python 源码：{prefix}')

    # 重新构造配置，防止把 snapshot 输入或未来新增的现场字段带入代码包。
    context = {'global': {}, 'scope': {}}
    fields = []
    for field in inputs:
        fields.append({key: field[key] for key in ('name', 'scope', 'function', 'type', 'required')
                       if key in field})
        bucket = context['scope'].setdefault(field['function'], {}) if field.get('scope') == 'function' else context['global']
        bucket[field['name']] = None
    template = {key: configuration.get(key) for key in ('diagnosis', 'composition', 'meta')}
    template.update(projects=normalized, input=context, runtime={'mode': 'serial', 'maxParallel': 4},
                    artifactRoot='local-artifacts', fileRoot='files')
    archive = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b')
    try:
        total = 0
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
            for path, name in sources:
                if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                    raise ValueError('代码文件指向生成目录之外，无法导出')
                total += path.stat().st_size
                if total > MAX_CODE_BYTES:
                    raise ValueError('代码总量超过 200MB，无法导出')
                package.write(path, name)
            package.writestr('configuration.json', json.dumps(template, ensure_ascii=False, indent=2))
            package.writestr('inputs.json', json.dumps(fields, ensure_ascii=False, indent=2))
            package.writestr('README.md', README)
        archive.seek(0)
        return archive
    except Exception:
        archive.close()
        raise

"""校验 examples/desktop-mvc：探测真实接口、编译，并跑一遍生成代码。

用法：backend/.venv/Scripts/python scripts/check_desktop_example.py [--keep]
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))

from app.services.codegen import generate  # noqa: E402
from app.services.compiler import compile_doc, validate_doc  # noqa: E402
from app.services.slots import probe_slots  # noqa: E402

EXAMPLE = ROOT / 'examples' / 'desktop-mvc'
PROJECTS = {
    '数据拉取': ('app', 'desktop_app.source'),
    '桌面应用': ('app', 'desktop_app.model'),
    '判定规则': ('rules', 'desktop_rules'),
}
# n2 人工判定 yes → 结论 → 处置完成
ANSWERS = ['yes', 'done']


def specs():
    return {
        name: SimpleNamespace(name=name, path=str(EXAMPLE / folder), module=module,
                              python=sys.executable, factory='')
        for name, (folder, module) in PROJECTS.items()
    }


def local_check() -> None:
    """同一份 Model / View 不依赖平台也能跑，证明它们是应用自己的模块。"""
    sys.path.insert(0, str(EXAMPLE / 'app'))
    sys.path.insert(0, str(EXAMPLE / 'rules'))
    from desktop_app.model import Model  # noqa: E402
    from desktop_app.view import View  # noqa: E402
    from desktop_rules import Rules  # noqa: E402
    from desktop_app.source import Sensor  # noqa: E402

    raw = Sensor().read(40)
    series = Model().smooth(raw, 3)
    summary = Model().summary(series)
    png = View(320, 120).render_png(series)
    verdict = Rules(75).overheating(series)
    assert png.startswith(b'\x89PNG'), 'View 没有输出 PNG'
    assert verdict['result'] is True, verdict
    print('OK 曲线', len(series), '峰值', summary['peak'], '判定', verdict['reason'])


def run_generated(doc, plan) -> int:
    root = Path(tempfile.mkdtemp(prefix='desktop-mvc-'))
    projects = {}
    for name, (folder, module) in PROJECTS.items():
        key = f'p{len(projects) + 1}'
        shutil.copytree(EXAMPLE / folder, root / 'modules' / key)
        projects[name] = {'name': name, 'path': str(root / 'modules' / key), 'module': module,
                          'factory': '', 'python': sys.executable}
    shutil.copytree(BACKEND / 'app' / 'worker', root / 'runtime',
                    ignore=shutil.ignore_patterns('__pycache__'))
    (root / 'flow.py').write_text(generate(plan), encoding='utf-8')
    configuration = {
        'projects': projects,
        'runtime': {'mode': 'serial', 'maxParallel': 4},
        'input': {'global': {'points': 40}},
        'artifactRoot': str(root / 'artifacts'),
        'fileRoot': str(root / 'files'),
        'meta': {node['id']: node for node in doc['nodes']},
    }
    (root / 'configuration.json').write_text(json.dumps(configuration, ensure_ascii=False), encoding='utf-8')

    completed = subprocess.run([sys.executable, 'flow.py', '--mode', 'serial'], cwd=root,
                               input='\n'.join(ANSWERS) + '\n', capture_output=True,
                               text=True, encoding='utf-8', timeout=180)
    print('exit', completed.returncode, '目录', root)
    if completed.returncode != 0:
        print((completed.stderr or '')[-3000:])
        return completed.returncode
    output = completed.stderr or ''
    missing = [kind for kind in ('image', 'metrics') if f'"type": "{kind}"' not in output]
    if missing:
        print('展示块缺失', missing)
        print(output[-3000:])
        return 1
    print('OK 展示块 image / metrics')
    return 0


def main() -> int:
    doc = json.loads((EXAMPLE / 'workflow.json').read_text(encoding='utf-8'))
    probed = probe_slots(doc, specs())
    failed = {key: value for key, value in probed.items() if isinstance(value, dict)}
    if failed:
        print('探测失败', failed)
        return 1
    issues = [item for item in validate_doc(doc, probed) if item['level'] == 'error']
    for item in issues:
        print('ERROR', item.get('nodeId'), item['message'])
    if issues:
        return 1

    plan = compile_doc(doc, 'desktop-mvc', 1, functions=probed)
    print('OK 节点', len(plan['steps']))
    local_check()
    return run_generated(doc, plan)


if __name__ == '__main__':
    raise SystemExit(main())

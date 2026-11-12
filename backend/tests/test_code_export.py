"""代码导出的边界：使用固定源码，不携带实例数据。"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import code_export


def test_code_export_uses_snapshot_and_omits_runtime_data(tmp_path, monkeypatch):
    monkeypatch.setattr(code_export, 'data_dir', lambda: tmp_path)
    directory = tmp_path / 'executions' / 'instance'
    key = hashlib.sha256('工程'.encode()).hexdigest()[:16]
    module = directory / 'modules' / key
    runtime = directory / 'runtime'
    module.mkdir(parents=True)
    runtime.mkdir()
    (directory / 'flow.py').write_text('from runtime.generated_runtime import main\n')
    (runtime / 'generated_runtime.py').write_text('def main(): pass\n')
    (module / 'business.py').write_text('VERSION = "snapshot"\n')
    (module / 'types.pyi').write_text('class Result: ...\n')
    (module / 'data.json').write_text('PRIVATE_DATA')
    (module / 'snapshot.pkl').write_bytes(b'PRIVATE_STATE')
    cache = module / '__pycache__'
    cache.mkdir()
    (cache / 'hidden.py').write_text('PRIVATE_CACHE')
    (directory / 'branch.json').write_text('PRIVATE_BRANCH')
    configuration = {'projects': {'工程': {'path': str(tmp_path / 'current-project'),
        'python': 'C:/original/python.exe', 'module': 'business'}},
        'input': {'global': {'device': 'PRIVATE_INPUT'}}, 'states': 'PRIVATE_STATE',
        'composition': {'objects': [], 'resources': [], 'operations': []}, 'meta': {}}
    (directory / 'configuration.json').write_text(json.dumps(configuration), encoding='utf-8')
    fields = [{'name': 'device', 'scope': 'global', 'type': 'text', 'default': 'PRIVATE_DEFAULT'},
              {'name': 'limit', 'scope': 'function', 'function': 'check', 'type': 'number'}]

    with code_export.build_code_export('instance', fields) as stream, zipfile.ZipFile(stream) as archive:
        names = set(archive.namelist())
        assert names == {'flow.py', 'runtime/generated_runtime.py', f'modules/{key}/business.py',
                         f'modules/{key}/types.pyi', 'configuration.json', 'inputs.json', 'README.md'}
        assert b'snapshot' in archive.read(f'modules/{key}/business.py')
        template = json.loads(archive.read('configuration.json'))
        assert template['projects']['工程']['path'] == f'modules/{key}'
        assert template['projects']['工程']['python'] is None
        assert template['input'] == {'global': {'device': None}, 'scope': {'check': {'limit': None}}}
        for name in names:
            assert b'PRIVATE_' not in archive.read(name)
    assert json.loads((directory / 'configuration.json').read_text()) == configuration


def test_code_export_reports_missing_or_incomplete_code(tmp_path, monkeypatch):
    monkeypatch.setattr(code_export, 'data_dir', lambda: tmp_path)
    with pytest.raises(ValueError, match='尚未生成'):
        code_export.build_code_export('missing', [])
    directory = tmp_path / 'executions' / 'partial'
    directory.mkdir(parents=True)
    (directory / 'flow.py').write_text('pass\n')
    (directory / 'configuration.json').write_text('{"projects":{}}')
    with pytest.raises(ValueError, match='代码目录缺失'):
        code_export.build_code_export('partial', [])

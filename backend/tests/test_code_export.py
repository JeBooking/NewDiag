"""代码导出的边界：使用固定源码，不携带实例数据。"""

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
    # 目录名故意与工程名无关：导出必须按生成时记录的 path 找，不能拿名字重算哈希
    module = directory / 'modules' / 'snapshot-code'
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
    configuration = {'projects': {'工程': {'path': str(module),
        'python': 'C:/original/python.exe', 'module': 'business'}},
        'input': {'global': {'device': 'PRIVATE_INPUT'}}, 'states': 'PRIVATE_STATE', 'meta': {}}
    (directory / 'configuration.json').write_text(json.dumps(configuration), encoding='utf-8')
    fields = [{'name': 'device', 'scope': 'global', 'type': 'text', 'default': 'PRIVATE_DEFAULT'},
              {'name': 'limit', 'scope': 'function', 'function': 'check', 'type': 'number'}]

    with code_export.build_code_export('instance', fields) as stream, zipfile.ZipFile(stream) as archive:
        names = set(archive.namelist())
        assert names == {'flow.py', 'runtime/generated_runtime.py',
                         'modules/snapshot-code/business.py', 'modules/snapshot-code/types.pyi',
                         'configuration.json', 'inputs.json', 'README.md'}
        assert b'snapshot' in archive.read('modules/snapshot-code/business.py')
        template = json.loads(archive.read('configuration.json'))
        assert template['projects']['工程']['path'] == 'modules/snapshot-code'
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


def test_code_export_rejects_source_outside_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(code_export, 'data_dir', lambda: tmp_path)
    directory = tmp_path / 'executions' / 'escape'
    (directory / 'runtime').mkdir(parents=True)
    (directory / 'flow.py').write_text('pass\n')
    (directory / 'runtime' / 'generated_runtime.py').write_text('pass\n')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'business.py').write_text('pass\n')
    configuration = {'projects': {'工程': {'path': str(outside), 'module': 'business'}}}
    (directory / 'configuration.json').write_text(json.dumps(configuration), encoding='utf-8')

    with pytest.raises(ValueError, match='不在实例生成目录内'):
        code_export.build_code_export('escape', [])


def test_code_export_accepts_relative_source_path(tmp_path, monkeypatch):
    monkeypatch.setattr(code_export, 'data_dir', lambda: tmp_path)
    directory = tmp_path / 'executions' / 'relative'
    module = directory / 'modules' / 'copy'
    (directory / 'runtime').mkdir(parents=True)
    module.mkdir(parents=True)
    (directory / 'flow.py').write_text('pass\n')
    (directory / 'runtime' / 'generated_runtime.py').write_text('pass\n')
    (module / 'business.py').write_text('pass\n')
    configuration = {'projects': {'工程': {'path': 'modules/copy', 'module': 'business'}}}
    (directory / 'configuration.json').write_text(json.dumps(configuration), encoding='utf-8')

    with code_export.build_code_export('relative', []) as stream, zipfile.ZipFile(stream) as archive:
        assert 'modules/copy/business.py' in archive.namelist()

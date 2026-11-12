"""组合实例的工程内对象管理；产物与快照沿用平台现有存储。"""

from __future__ import annotations

import importlib
import inspect
import hashlib
import json
import logging
import shutil
from pathlib import Path

if __package__:
    from . import artifacts, node_sdk
else:
    import artifacts
    import node_sdk

logger = logging.getLogger(__name__)


class CompositionRuntime:
    def __init__(self):
        self.objects = {}
        self.reference = None
        self.fixed = {}
        self.fixed_references = {}

    def save(self, root):
        snapshot = artifacts.save_python(root, self.objects, fixed=self.fixed)
        self.reference = artifacts.save(root, {
            'format': 'composition-state/v1', 'objects': snapshot, 'fixed': self.fixed_references,
        })
        return self.reference

    def execute(self, target, request):
        root = request['artifactRoot']
        reference = request.get('state')
        if reference != self.reference:
            self.fixed, self.fixed_references, self.objects = {}, {}, {}
            if reference:
                manifest = artifacts.load(root, reference)
                cache = {}
                self.fixed_references = manifest['fixed']
                for identifier, saved in self.fixed_references.items():
                    if saved['$artifact'] not in cache:
                        cache[saved['$artifact']] = artifacts.load_python(root, saved, cache=cache)
                    value = cache[saved['$artifact']]
                    self.fixed[id(value)] = (value, saved)
                self.objects = artifacts.load_python(root, manifest['objects'], cache=cache) if manifest['objects'] else {}
                for identifier, saved in self.fixed_references.items():
                    self.objects[identifier] = cache[saved['$artifact']]
            self.reference = reference
            logger.info('组合工程状态恢复 objects=%s saved=%s', len(self.objects), bool(reference))
        if request.get('op') == 'restore':
            return {'state': self.reference}
        if request.get('op') == 'read':
            value = self.objects[request['objectId']]
            for part in request['path'].split('.'):
                if part.startswith('_'):
                    raise ValueError('只允许读取公开字段')
                value = value[int(part)] if isinstance(value, (list, tuple)) else (value[part] if isinstance(value, dict) else getattr(value, part))
            return {'state': self.save(root), 'value': node_sdk.json_value(value)}
        if request.get('op') == 'pin':
            identifier = request['objectId']
            value = self.objects[identifier]
            saved = self.fixed.get(id(value))
            reference = saved[1] if saved is not None and saved[0] is value else artifacts.save_python(root, value, fixed=self.fixed)
            self.fixed[id(value)] = (value, reference)
            self.fixed_references[identifier] = reference
            return {'state': self.save(root), 'value': {'$object': identifier}}
        call = request['call']

        def decode(value):
            if isinstance(value, dict):
                if '$object' in value and set(value) <= {'$object', 'project'}:
                    if value.get('project', request.get('project')) != request.get('project'):
                        raise ValueError('业务对象不能跨工程传递')
                    return self.objects[value['$object']]
                if artifacts.is_reference(value):
                    return decode(artifacts.load(root, value))
                return {key: decode(item) for key, item in value.items()}
            if isinstance(value, list):
                return [decode(item) for item in value]
            return value

        if call.get('target'):
            owner = decode(request['target'])
            name = call['function']
            if name.startswith('_') or '.' in name:
                raise ValueError('对象调用只能选择公开方法')
            function = getattr(owner, name)
        else:
            function = importlib.import_module(call['module']) if call.get('module') else target
            for name in call['function'].split('.'):
                if name.startswith('_'):
                    raise ValueError('只能调用公开接口')
                function = getattr(function, name)
        if not callable(function) or inspect.iscoroutinefunction(function):
            raise ValueError('组合调用需要同步的公开函数、构造器或方法')
        if call.get('fingerprint'):
            try:
                current = hashlib.sha256(inspect.getsource(function).encode('utf-8')).hexdigest()
            except (OSError, TypeError) as exc:
                raise ValueError('无法核对已发布接口的代码版本') from exc
            if current != call['fingerprint']:
                raise ValueError('接口实现已变化，请重新发布流程')
        arguments = decode(request.get('arguments') or {})
        inspect.signature(function).bind(**arguments)
        value = function(**arguments)
        kind = call.get('output', 'value')
        if kind == 'object':
            identifier = request['objectId']
            self.objects[identifier] = value
            result = {'$object': identifier}
        elif kind in ('file', 'image'):
            path = None if isinstance(value, bytes) else Path(value).resolve()
            if path is not None and not path.is_file():
                raise ValueError('返回的文件不存在')
            if kind == 'image':
                result = {'image': node_sdk.save_file(value if path is None else path.read_bytes())}
            else:
                destination = Path(request['callDirectory']) / 'immutable' / (path.name if path else 'output.bin')
                destination.parent.mkdir(parents=True, exist_ok=True)
                if path is None:
                    destination.write_bytes(value)
                else:
                    shutil.copyfile(path, destination)
                result = {'file': str(destination)}

        else:
            result = node_sdk.json_value(value)
            if len(json.dumps(result, ensure_ascii=False)) > 4096:
                result = artifacts.save(root, result)
        # 原业务对象的成员变化与新返回对象一起保存，恢复时不重跑构造器。
        self.save(root)
        return {'value': result, 'state': self.reference}

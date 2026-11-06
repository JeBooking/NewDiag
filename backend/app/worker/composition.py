"""组合实例的工程内对象管理；产物与快照沿用平台现有存储。"""

from __future__ import annotations

import importlib
import inspect
import hashlib
import json
import logging
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

    def execute(self, target, request):
        root = request['artifactRoot']
        reference = request.get('state')
        if reference != self.reference:
            self.objects = artifacts.load_python(root, reference) if reference else {}
            self.reference = reference
            logger.info('组合工程状态恢复 objects=%s saved=%s', len(self.objects), bool(reference))
        if request.get('op') == 'restore':
            return {'state': self.reference}
        call = request['call']

        def decode(value):
            if isinstance(value, dict):
                if set(value) == {'$object'}:
                    return self.objects[value['$object']]
                if artifacts.is_reference(value):
                    return artifacts.load(root, value)
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
            path = Path(value).resolve()
            workspace = Path(request['outputRoot']).resolve()
            if not path.is_relative_to(workspace) or not path.is_file():
                raise ValueError('文件输出必须位于平台为本次调用分配的目录内')
            if kind == 'image':
                result = {'image': node_sdk.save_file(path.read_bytes())}
            else:
                destination = Path(request['callDirectory']) / 'immutable' / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if path != destination:
                    destination.write_bytes(path.read_bytes())
                result = {'file': str(destination)}
        else:
            result = node_sdk.json_value(value)
            if len(json.dumps(result, ensure_ascii=False)) > 4096:
                result = artifacts.save(root, result)
        # 原业务对象的成员变化与新返回对象一起保存，恢复时不重跑构造器。
        self.reference = artifacts.save_python(root, self.objects)
        return {'value': result, 'state': self.reference}

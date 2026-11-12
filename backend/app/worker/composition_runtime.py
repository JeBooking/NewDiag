"""组合配置运行时；固定资源引用复用，人工等待交给生成流程。"""
from __future__ import annotations
import copy
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from . import artifacts
from .node_sdk import presentation
from .function_pipeline import _summary
logger = logging.getLogger(__name__)

class CompositionRuntime:
    def __init__(self, configuration, root, invoke, reference=None):
        self.configuration = configuration
        self.root = Path(root)
        self.invoke = invoke
        self.state = artifacts.load(root, reference) if reference else {
            'identity': configuration, 'workers': {}, 'objects': {}, 'resources': {},
        }
        if self.state['identity'] != configuration:
            raise ValueError('组合实例配置与保存状态不一致')
        self.loading = set()
        self.inputs = {}
        self.stages = []
        self.history = {}
        self.file_root = None

    def save(self):
        return artifacts.save(self.root, self.state)

    def resolve(self, expression, values, parameters=None, project=None, output=None, materialize=True):
        if not isinstance(expression, dict):
            raise ValueError('参数来源必须为对象')
        if 'lit' in expression:
            return copy.deepcopy(expression['lit'])
        if 'items' in expression:
            return [self.resolve(item, values, parameters, project, output) for item in expression['items']]
        if 'fields' in expression:
            return {name: self.resolve(item, values, parameters, project, output)
                    for name, item in expression['fields'].items()}
        if 'system' in expression:
            if output is None:
                raise ValueError('输出路径只在调用参数中有效')
            return str(output if expression['system'] == 'directory' else output / 'output.png')
        source, name = expression.get('source'), expression.get('name')
        if source == 'input':
            value = self.inputs[name]
        elif source == 'parameter':
            value = (parameters or {})[name]
        elif source == 'node':
            value = artifacts.load(self.root, self.history[name])['output']
        elif source == 'value':
            value = values[name]
        elif source == 'object':
            value = self.object(name)
        elif source == 'resource':
            resource_parameters = {key: self.resolve(item, values, parameters, output=output)
                                   for key, item in expression.get('arguments', {}).items()}
            value = self.resource(name, resource_parameters)
        else:
            raise ValueError(f'未知参数来源 {source}')
        return self.select(value, expression.get('path', ''), project, materialize, expression.get('file', False))

    def select(self, value, path='', project=None, materialize=True, file=False):
        if isinstance(value, dict) and '$object' in value:
            owner = value['project']
            if path:
                response = self.invoke(owner, {'op': 'read', 'objectId': value['$object'],
                    'path': path, 'artifactRoot': str(self.root), 'state': self.state['workers'].get(owner)})
                self.state['workers'][owner] = response['state']
                return self.select(response['value'], project=project, materialize=materialize, file=file)
            if project and owner != project:
                raise ValueError('业务对象只能在其所属工程中使用，请选择公开数据字段或输出数据')
            if file:
                raise ValueError('文件参数不能使用业务对象')
            return {'$object': value['$object']} if project else value
        if artifacts.is_reference(value):
            # 资源可能返回含对象引用的结构，因此先在运行侧解引用，归属校验不能绕过。
            return self.select(artifacts.load(self.root, value), path, project, materialize, file) if path or file else (
                value if project or not materialize else artifacts.load(self.root, value))
        if path:
            part, _, remaining = path.partition('.')
            selected = value[int(part)] if isinstance(value, list) else value[part]
            return self.select(selected, remaining, project, materialize, file)
        return self.file_path(value) if file else value

    def file_path(self, value):
        import re
        if isinstance(value, str) and self.file_root is not None:
            path = Path(value).resolve()
            if path.is_relative_to(self.file_root.resolve()) and path.is_file():
                return str(path)
        if not isinstance(value, dict) or not isinstance(value.get('hash'), str):
            raise ValueError('文件参数需要平台上传引用')
        digest = value['hash'].removeprefix('sha256:')
        if not re.fullmatch(r'[0-9a-f]{64}', digest) or self.file_root is None:
            raise ValueError('上传文件引用无效')
        path = self.file_root / digest[:2] / digest
        if not path.is_file():
            raise ValueError('上传文件不存在')
        return str(path.resolve())

    def object(self, name):
        if name in self.state['objects']:
            return self.state['objects'][name]
        identity = ('object', name)
        if identity in self.loading:
            raise ValueError(f'对象依赖成环：{name}')
        self.loading.add(identity)
        try:
            definition = next(item for item in self.configuration.get('objects', []) if item['id'] == name)
            value = self.call({**definition, 'output': 'object'}, {})
            self.state['objects'][name] = value
            logger.info('组合实例对象创建 object=%s project=%s', name, definition['project'])
            return value
        finally:
            self.loading.remove(identity)

    def resource(self, name, parameters):
        definition = next(item for item in self.configuration.get('resources', []) if item['id'] == name)
        parameters = {**definition.get('defaults', {}), **parameters}
        def check_parameter(value):
            if isinstance(value, dict):
                if '$object' in value:
                    raise ValueError('资源请求参数必须为业务数据，不能使用进程对象句柄')
                for child in value.values():
                    check_parameter(child)
            elif isinstance(value, list):
                for child in value:
                    check_parameter(child)
        check_parameter(parameters)
        key = hashlib.sha256(json.dumps([name, parameters], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key in self.state['resources']:
            logger.info('组合实例资源缓存命中 resource=%s key=%s', name, key)
            return self.state['resources'][key]
        identity = ('resource', name)
        if identity in self.loading:
            raise ValueError(f'资源依赖成环：{name}')
        self.loading.add(identity)
        try:
            logger.info('组合实例资源首次加载 resource=%s key=%s', name, key)
            values = self.run_calls(definition.get('calls', []), parameters)
            result = self.resolve(definition['result'], values, parameters, materialize=False)
            self.pin_resource(result)
            if not artifacts.is_reference(result) and not (isinstance(result, dict) and '$object' in result):
                result = artifacts.save(self.root, result)
            self.state['resources'][key] = result
            return result
        finally:
            self.loading.remove(identity)

    def pin_resource(self, value):
        if artifacts.is_reference(value):
            self.pin_resource(artifacts.load(self.root, value))
        elif isinstance(value, dict) and '$object' in value:
            project = value['project']
            response = self.invoke(project, {
                'op': 'pin', 'objectId': value['$object'], 'artifactRoot': str(self.root),
                'state': self.state['workers'].get(project),
            })
            self.state['workers'][project] = response['state']
        elif isinstance(value, dict):
            for child in value.values():
                self.pin_resource(child)
        elif isinstance(value, list):
            for child in value:
                self.pin_resource(child)

    def call(self, call, values, parameters=None):
        project = call['project']
        output = self.root / 'outputs' / uuid.uuid4().hex
        output.mkdir(parents=True)
        arguments = {name: self.resolve(value, values, parameters, project, output)
                     for name, value in call.get('arguments', {}).items()}
        target = self.resolve(call['target'], values, parameters, project, output) if call.get('target') else None
        started = time.perf_counter()
        try:
            response = self.invoke(project, {
                'call': call, 'project': project, 'target': target, 'arguments': arguments,
                'artifactRoot': str(self.root), 'outputRoot': str(self.root / 'outputs'),
                'callDirectory': str(output),
                'state': self.state['workers'].get(project), 'objectId': uuid.uuid4().hex,
            })
            self.state['workers'][project] = response['state']
            value = response['value']
            if isinstance(value, dict) and '$object' in value:
                value = {**value, 'project': project}
            return value
        except Exception as exc:
            raise ValueError(f"{project} / {call['id']} / {call['function']}: {exc}") from exc
        finally:
            self.stages.append({'project': project, 'function': call['function'],
                                'arguments': {name: _summary(value) for name, value in arguments.items()},
                                'call': call['id'], 'durationMs': round((time.perf_counter() - started) * 1000)})

    def run_calls(self, calls, parameters=None):
        values = {}
        for call in calls:
            if call['id'] in values:
                raise ValueError(f"调用名称重复：{call['id']}")
            values[call['id']] = self.call(call, values, parameters)
        return values

    def execute(self, inputs, operation=None, parameters=None):
        self.inputs = inputs
        if operation is None:
            for resource in self.configuration.get('resources', []):
                if resource.get('trigger') == 'startup':
                    self.resource(resource['id'], {})
            return None
        definition = next(item for item in self.configuration.get('operations', []) if item['id'] == operation)
        values = self.run_calls(definition.get('calls', []), parameters)
        blocks = []
        for block in definition.get('presentation', []):
            if block['type'] == 'image':
                value = self.resolve(block['value'], values, parameters)
                blocks.append({'type': 'image', 'src': value['image'] if isinstance(value, dict) else value})
            else:
                value = self.resolve(block['value'], values, parameters)
                kind = block['type']
                if kind == 'line':
                    blocks.append({'type': kind, 'values': value})
                elif kind == 'metrics':
                    blocks.append({'type': kind, 'items': value})
                elif kind == 'table':
                    blocks.append({**value, 'type': kind})
                else:
                    blocks.append({'type': 'text', 'text': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)})
        decision = definition.get('decision')
        result, reason = None, None
        if decision:
            result = self.resolve(decision['result'], values, parameters)
            if isinstance(result, bool):
                result = 'yes' if result else 'no'
            reason = self.resolve(decision['reason'], values, parameters)
            if isinstance(reason, list):
                reason = '；'.join(str(item) for item in reason)
                if not reason:
                    reason = decision.get('normalReason', '')
            if result not in ('yes', 'no') or not isinstance(reason, str) or not reason.strip():
                raise ValueError('判断输出必须为 yes/no 或布尔值，并提供非空依据')
        return {'presentation': presentation({'blocks': blocks}), 'result': result, 'reason': reason, 'data': values}

"""配置驱动的诊断实例；各工程持有真实对象，平台管理资源和状态清单。"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

from app.worker import artifacts
from app.worker.node_sdk import presentation

logger = logging.getLogger(__name__)


def calls_in(configuration):
    for item in configuration.get('objects', []):
        yield item
    for resource in configuration.get('resources', []):
        yield from resource.get('calls', [])
    for operation in configuration.get('operations', []):
        yield from operation.get('calls', [])


def walk_expression(value):
    if not isinstance(value, dict):
        return
    yield value
    if 'lit' in value:
        return
    for item in value.get('items', []):
        yield from walk_expression(item)
    for field in ('fields', 'arguments'):
        for item in value.get(field, {}).values():
            yield from walk_expression(item)


def resource_expressions(resource):
    yield resource['result']
    for call in resource['calls']:
        yield from call.get('arguments', {}).values()
        if call.get('target'):
            yield call['target']


def configuration_expressions(configuration):
    for call in calls_in(configuration):
        yield from call.get('arguments', {}).values()
        if call.get('target'):
            yield call['target']
    for resource in configuration.get('resources', []):
        yield resource['result']
    for operation in configuration.get('operations', []):
        for block in operation.get('presentation', []):
            yield block['value']
        if operation.get('decision'):
            yield operation['decision']['result']
            yield operation['decision']['reason']


def probe_configuration(configuration):
    from .projects import probe
    from .signatures import _load_project
    errors = validate(configuration, [])
    if errors:
        return {'composition': {'error': '；'.join(errors)}}
    result, catalogues = {}, {}
    for call in calls_in(configuration):
        project_name = call.get('project', '')
        module = call.get('module', '')
        interface = call.get('interface') or call.get('function', '')
        key = f'{project_name}:{module}:{interface}'
        cache_key = (project_name, module)
        if cache_key not in catalogues:
            project = _load_project(project_name)
            catalogues[cache_key] = probe({
                'name': project_name, 'path': project.path, 'python': project.python,
                'module': module or project.module, 'catalog': True,
            }) if project else {'error': '工程未登记'}
        catalog = catalogues[cache_key]
        entry = next((item for item in catalog.get('functions', []) if item['name'] == interface), None)
        if entry and entry.get('compositionError'):
            result[key] = {'error': entry['compositionError']}
        elif not entry:
            result[key] = {'error': catalog.get('error') or f'公开接口 {interface} 不存在'}
        else:
            result[key] = [*entry['params'],
                           {'name': '$implementation', 'type': entry.get('fingerprint', ''),
                            'required': False, 'default': None},
                           {'name': '$keywords', 'type': 'yes' if entry.get('variadicKeywords') else 'no',
                            'required': False, 'default': None}]
    return result


def _shape_errors(configuration, nodes):
    errors = []
    if not isinstance(configuration, dict):
        return ['组合实例配置必须为对象']

    def name(value):
        return isinstance(value, str) and bool(value.strip())

    def expressions(values, location):
        if not isinstance(values, dict) or any(not name(key) for key in values):
            errors.append(f'{location} 必须为参数名到来源的对象')
            return
        for value in values.values():
            expression(value, location)

    def expression(value, location):
        if not isinstance(value, dict):
            errors.append(f'{location} 的参数来源必须为对象')
            return
        tags = set(value) & {'lit', 'source', 'system', 'items', 'fields'}
        if len(tags) != 1:
            errors.append(f'{location} 的来源必须且只能有一种')
            return
        if 'source' in value:
            if (value['source'] not in ('input', 'parameter', 'value', 'object', 'resource')
                    or not name(value.get('name')) or not isinstance(value.get('path', ''), str)):
                errors.append(f'{location} 的来源类型、名称或字段路径无效')
            if 'arguments' in value:
                expressions(value['arguments'], location)
        elif 'items' in value:
            if not isinstance(value['items'], list):
                errors.append(f'{location} 的组合列表无效')
            else:
                for child in value['items']:
                    expression(child, location)
        elif 'fields' in value:
            expressions(value['fields'], location)
        elif 'system' in value and value['system'] not in ('directory', 'file'):
            errors.append(f'{location} 的系统路径类型无效')

    def calls(values, location):
        if not isinstance(values, list):
            errors.append(f'{location} 的调用步骤必须为列表')
            return
        for call in values:
            if not isinstance(call, dict):
                errors.append(f'{location} 的调用必须为对象')
                continue
            if any(not name(call.get(key)) for key in ('id', 'project', 'function')):
                errors.append(f'{location} 的调用名称、工程和接口不能为空')
            for key in ('module', 'interface'):
                if key in call and not isinstance(call[key], str):
                    errors.append(f'{location} 的 {key} 必须为字符串')
            if call.get('output', 'value') not in ('value', 'object', 'file', 'image'):
                errors.append(f'{location} 的返回类型无效')
            expressions(call.get('arguments', {}), location)
            if 'target' in call:
                expression(call['target'], location)

    for collection in ('objects', 'resources', 'operations'):
        values = configuration.get(collection)
        if not isinstance(values, list):
            errors.append(f'{collection} 必须为列表')
            continue
        for item in values:
            if not isinstance(item, dict) or not name(item.get('id')):
                errors.append(f'{collection} 每项必须为有名称的对象')
                continue
            location = f"{collection}/{item['id']}"
            if collection == 'objects':
                calls([item], location)
            else:
                calls(item.get('calls'), location)
            if collection == 'resources':
                expression(item.get('result'), location)
                if not isinstance(item.get('defaults', {}), dict):
                    errors.append(f'{location} 的请求默认值必须为对象')
            if collection == 'operations':
                parameters = item.get('parameters', [])
                if not isinstance(parameters, list) or any(not name(key) for key in parameters):
                    errors.append(f'{location} 的参数名必须为字符串列表')
                elif len(parameters) != len(set(parameters)):
                    errors.append(f'{location} 的参数名不能重复')
                blocks = item.get('presentation', [])
                if not isinstance(blocks, list):
                    errors.append(f'{location} 的展示配置必须为列表')
                else:
                    for block in blocks:
                        if not isinstance(block, dict) or block.get('type') not in ('image', 'text'):
                            errors.append(f'{location} 的展示类型无效')
                        else:
                            expression(block.get('value'), location)
                if 'decision' in item:
                    decision = item['decision']
                    if not isinstance(decision, dict):
                        errors.append(f'{location} 的判断配置必须为对象')
                    else:
                        for key in ('result', 'reason'):
                            expression(decision.get(key), location)
                        if not isinstance(decision.get('normalReason', ''), str):
                            errors.append(f'{location} 的正常依据必须为字符串')
    for node in nodes:
        if 'operation' in node and not name(node['operation']):
            errors.append('排查操作名称必须为非空字符串')
        expressions(node.get('operationArguments', {}), f"节点 {node.get('id')}")
    return errors


def validate(configuration, nodes, functions=None):
    errors = _shape_errors(configuration, nodes)
    if errors:
        return errors
    errors = []
    if not isinstance(configuration, dict):
        return ['组合实例配置必须为对象']
    for collection in ('objects', 'resources', 'operations'):
        values = configuration.get(collection, [])
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            errors.append(f'{collection} 必须为对象列表')
            continue
        names = [item.get('id') for item in values]
        if any(not isinstance(name, str) or not name.strip() for name in names):
            errors.append(f'{collection} 的名称不能为空')
        elif len(set(names)) != len(names):
            errors.append(f'{collection} 的名称不能为空或重复')
    if errors:
        return errors
    for item in [*configuration.get('resources', []), *configuration.get('operations', [])]:
        if not isinstance(item.get('calls'), list) or any(not isinstance(call, dict) for call in item['calls']):
            errors.append(f"{item['id']} 的调用步骤必须为对象列表")
    if errors:
        return errors
    for call in calls_in(configuration):
        if (not isinstance(call, dict) or not call.get('id') or not call.get('project')
                or not call.get('function') or not isinstance(call.get('arguments', {}), dict)):
            errors.append('每个调用必须填写名称、工程、接口和合法参数映射')
        elif call.get('output', 'value') not in ('value', 'object', 'file', 'image'):
            errors.append(f"调用 {call['id']} 的返回类型无效")
        elif functions is not None:
            key = f"{call['project']}:{call.get('module', '')}:{call.get('interface') or call['function']}"
            params = functions.get(key)
            if not isinstance(params, list):
                errors.append(f"接口 {key} 尚未成功探测")
            else:
                for param in params:
                    if param.get('required') and param['name'] not in call.get('arguments', {}):
                        errors.append(f"调用 {call['id']} 缺少必填参数 {param['name']}")
                keywords = any(param['name'] == '$keywords' and param['type'] == 'yes' for param in params)
                known = {param['name'] for param in params if not param['name'].startswith('$')}
                if not keywords and set(call.get('arguments', {})) - known:
                    errors.append(f"调用 {call['id']} 包含接口未声明的参数")
        if isinstance(call, dict) and call.get('interface'):
            expected = call['interface'].split('.')[-1] if call.get('target') else call['interface']
            if call.get('function') != expected:
                errors.append(f"调用 {call.get('id')} 的目标方法与选择的接口不一致")
    operations = {item['id'] for item in configuration.get('operations', [])}
    for operation in configuration.get('operations', []):
        names = operation.get('parameters', [])
        if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
            errors.append(f"操作 {operation['id']} 的参数名称必须为非空字符串列表")
    for node in nodes:
        if node.get('operation') and (node.get('kind') != 'investigation' or node['operation'] not in operations):
            errors.append(f"节点 {node.get('id')} 引用的排查操作不存在")
        operation = next((item for item in configuration.get('operations', []) if item['id'] == node.get('operation')), None)
        if operation:
            for name in operation.get('parameters', []):
                if name not in node.get('operationArguments', {}):
                    errors.append(f"节点 {node['id']} 缺少操作参数 {name}")
        if node.get('binding') or node.get('pipeline') or node.get('method') or node.get('kind') == 'dataPreparation':
            errors.append('组合实例的节点统一选择排查操作')
    for resource in configuration.get('resources', []):
        if resource.get('trigger', 'lazy') not in ('startup', 'lazy'):
            errors.append(f"资源 {resource['id']} 的加载时机无效")
        if not isinstance(resource.get('result'), dict):
            errors.append(f"资源 {resource['id']} 必须指定返回结果")
    object_names = {item['id'] for item in configuration.get('objects', [])}
    resource_names = {item['id'] for item in configuration.get('resources', [])}
    dependencies = {}
    objects_by_name = {item['id']: item for item in configuration.get('objects', [])}
    resources_by_name = {item['id']: item for item in configuration.get('resources', [])}

    resource_parameters = {
        item['id']: {value['name'] for expression in resource_expressions(item)
                     for value in walk_expression(expression) if value.get('source') == 'parameter'}
        for item in configuration.get('resources', [])
    }
    scopes = {'object:' + name: set() for name in object_names}
    scopes.update({'resource:' + name: names for name, names in resource_parameters.items()})
    scopes.update({'operation:' + item['id']: set(item.get('parameters', []))
                   for item in configuration.get('operations', [])})
    for item in configuration.get('resources', []):
        if item.get('trigger') == 'startup':
            missing = resource_parameters[item['id']] - set(item.get('defaults', {}))
            if missing:
                errors.append(f"启动资源 {item['id']} 缺少请求参数默认值：{', '.join(sorted(missing))}")

    def object_owner(value, previous, seen=None):
        seen = set() if seen is None else seen
        if not isinstance(value, dict):
            return None
        source, name = value.get('source'), value.get('name')
        if source == 'object':
            return objects_by_name.get(name, {}).get('project')
        if source == 'value':
            call = previous.get(name, {})
            return call.get('project') if call.get('output') == 'object' else None
        if source == 'resource' and name in resources_by_name:
            if name in seen:
                return None
            resource = resources_by_name[name]
            return object_owner(resource.get('result'), {call['id']: call for call in resource['calls']}, seen | {name})
        return None

    def expression(value, previous, owner):
        if not isinstance(value, dict):
            errors.append(f'{owner} 的参数来源必须为对象')
            return
        if len(set(value) & {'lit', 'items', 'fields', 'system', 'source'}) != 1:
            errors.append(f'{owner} 的参数来源必须且只能指定一种')
            return
        if 'lit' in value:
            return
        if 'system' in value:
            if value['system'] not in ('directory', 'file'):
                errors.append(f'{owner} 的系统路径类型无效')
            return
        source, name = value.get('source'), value.get('name')
        if source == 'value' and name not in previous:
            errors.append(f'{owner} 引用了未在此前执行的调用 {name}')
        elif source == 'object':
            if name not in object_names:
                errors.append(f'{owner} 引用了不存在的对象 {name}')
            else:
                dependencies.setdefault(owner, set()).add('object:' + name)
        elif source == 'resource':
            if name not in resource_names:
                errors.append(f'{owner} 引用了不存在的资源 {name}')
            else:
                dependencies.setdefault(owner, set()).add('resource:' + name)
                supplied = set(value.get('arguments', {}))
                defaults = set(resources_by_name[name].get('defaults', {}))
                missing = resource_parameters[name] - supplied - defaults
                unknown = supplied - resource_parameters[name] - defaults
                if missing or unknown:
                    errors.append(f'{owner} 对资源 {name} 的请求参数缺失或包含未知参数')
        elif source == 'parameter' and name not in scopes.get(owner, set()):
            errors.append(f'{owner} 引用了未声明的操作／资源参数 {name}')
        elif source not in ('input', 'parameter', 'value', None):
            errors.append(f'{owner} 的来源类型无效')
        for child in value.get('arguments', {}).values():
            expression(child, previous, owner)
        for child in value.get('items', []):
            expression(child, previous, owner)
        for child in value.get('fields', {}).values():
            expression(child, previous, owner)

    groups = [('object:' + item['id'], [item], []) for item in configuration.get('objects', [])]
    groups += [('resource:' + item['id'], item['calls'], [item.get('result')]) for item in configuration.get('resources', [])]
    groups += [('operation:' + item['id'], item['calls'], [block.get('value') for block in item.get('presentation', [])]
                + list((item.get('decision') or {}).get(key) for key in ('result', 'reason') if item.get('decision')))
               for item in configuration.get('operations', [])]
    for owner, calls, outputs in groups:
        previous = set()
        prior_calls = {}
        for call in calls:
            if call.get('id') in previous:
                errors.append(f'{owner} 中调用名称重复')
            for argument in call.get('arguments', {}).values():
                expression(argument, previous, owner)
            if call.get('target'):
                expression(call['target'], previous, owner)
            for value in [*call.get('arguments', {}).values(), call.get('target')]:
                producer = object_owner(value, prior_calls)
                if producer and producer != call.get('project'):
                    errors.append(f"调用 {call.get('id')} 不能跨工程使用 {producer} 的业务对象，请先输出数据")
            previous.add(call.get('id'))
            prior_calls[call.get('id')] = call
        for value in outputs:
            expression(value, previous, owner)
    for node in nodes:
        for argument in node.get('operationArguments', {}).values():
            expression(argument, set(), 'node:' + node['id'])
    visited, visiting = set(), set()

    def visit(name):
        if name in visiting:
            errors.append(f'对象／资源依赖成环：{name}')
            return
        if name in visited:
            return
        visiting.add(name)
        for child in dependencies.get(name, set()):
            visit(child)
        visiting.remove(name)
        visited.add(name)
    for name in list(dependencies):
        visit(name)
    return errors


def freeze(configuration, functions):
    result = copy.deepcopy(configuration)
    for call in calls_in(result):
        key = f"{call['project']}:{call.get('module', '')}:{call.get('interface') or call['function']}"
        call['fingerprint'] = next((item['type'] for item in (functions or {}).get(key, [])
                                    if item['name'] == '$implementation'), '')
    return result


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
        if isinstance(value, dict) and '$object' in value:
            if expression.get('path'):
                raise ValueError('对象内部字段不能跨进程读取，请使用公开方法返回数据')
            if project and value.get('project') != project:
                raise ValueError('业务对象只能在其所属工程中使用，请通过公开方法输出数据')
            return {'$object': value['$object']} if project else value
        if artifacts.is_reference(value):
            reference = artifacts.select(value, expression.get('path', ''))
            return reference if project or not materialize else artifacts.load(self.root, reference)
        for part in expression.get('path', '').split('.'):
            if part:
                value = value[int(part)] if isinstance(value, list) else value[part]
        return value

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
            self.state['resources'][key] = result
            return result
        finally:
            self.loading.remove(identity)

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
                'call': call, 'target': target, 'arguments': arguments,
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
                                'arguments': arguments,
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
                blocks.append({'type': 'image', 'src': self.resolve(block['value'], values, parameters)['image']})
            else:
                value = self.resolve(block['value'], values, parameters)
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
        return {'presentation': presentation({'blocks': blocks}), 'result': result, 'reason': reason}

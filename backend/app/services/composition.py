"""配置驱动的诊断实例；各工程持有真实对象，平台管理资源和状态清单。"""

from __future__ import annotations

import copy

from app.worker import artifacts



def rollback_state(root, selected, latest):
    """对象恢复到分岔口；该独立分支已取得的固定资源继续复用。"""
    if not latest:
        return selected
    current = artifacts.load(root, latest)
    previous = artifacts.load(root, selected) if selected else {
        'identity': current['identity'], 'workers': {}, 'objects': {}, 'resources': {},
    }
    workers, fixed_objects = {}, set()
    for project in set(previous['workers']) | set(current['workers']):
        base = artifacts.load(root, previous['workers'][project]) if project in previous['workers'] else {}
        newest = artifacts.load(root, current['workers'][project]) if project in current['workers'] else base
        fixed = newest.get('fixed', {})
        fixed_objects.update((project, identifier) for identifier in fixed)
        workers[project] = artifacts.save(root, {
            'format': 'composition-state/v1', 'objects': base.get('objects'), 'fixed': fixed,
        })
    objects = {**previous['objects'], **{
        name: value for name, value in current['objects'].items()
        if (value['project'], value['$object']) in fixed_objects
    }}
    return artifacts.save(root, {**previous, 'workers': workers, 'objects': objects,
                                 'resources': current['resources']})


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


def probe_configuration(configuration, projects=None):
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
            project = projects.get(project_name) if projects is not None else _load_project(project_name)
            catalogues[cache_key] = probe({
                'name': project_name, 'path': project.path, 'python': project.python,
                'module': module or project.module, 'catalog': True,
            }) if project else {'error': '工程未登记'}
        catalog = catalogues[cache_key]
        entry = next((item for item in catalog.get('functions', []) if item['name'] == interface), None)
        if isinstance(result.get(key), dict):
            continue
        if entry and entry.get('kind') == 'instance_method' and not call.get('target'):
            result[key] = {'error': f'实例方法 {interface} 必须配置调用目标对象'}
        elif entry and entry.get('compositionError'):
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
        if 'file' in value and not isinstance(value['file'], bool):
            errors.append(f'{location} 的文件路径选项必须是布尔值')
        if 'source' in value:
            if (value['source'] not in ('input', 'parameter', 'value', 'object', 'resource', 'node')
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
                        if not isinstance(block, dict) or block.get('type') not in ('image', 'text', 'line', 'metrics', 'table'):
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
            from app.worker.input_types import INPUT_TYPES
            fields = node.get('runtimeInputs', [])
            if not isinstance(fields, list):
                errors.append(f"节点 {node['id']} 的现场参数必须为列表")
                fields = []
            seen = set()
            for field in fields:
                if (not isinstance(field, dict) or field.get('name') not in operation.get('parameters', [])
                        or field.get('name') in seen or field.get('type', 'json') not in INPUT_TYPES):
                    errors.append(f"节点 {node['id']} 的现场参数名称或类型无效")
                    continue
                seen.add(field['name'])
                if field['name'] in node.get('operationArguments', {}):
                    errors.append(f"节点 {node['id']} 参数 {field['name']} 不能同时配置来源和现场输入")
            if set(node.get('operationArguments', {})) - set(operation.get('parameters', [])):
                errors.append(f"节点 {node['id']} 包含操作未声明的参数")
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
        elif source == 'node' and not owner.startswith(('node:', 'operation:')):
            errors.append(f'{owner} 不能依赖节点结果，请将依赖配置到操作参数')
        elif source == 'parameter' and name not in scopes.get(owner, set()):
            errors.append(f'{owner} 引用了未声明的操作／资源参数 {name}')
        elif source not in ('input', 'parameter', 'value', 'node', None):
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
                for nested in walk_expression(value):
                    producer = object_owner(nested, prior_calls)
                    if producer and producer != call.get('project') and not nested.get('path'):
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



"""真实 API 覆盖启动上传、结构化参数、节点现场补参及等待恢复。"""

import json
import sys
import time
from pathlib import Path

import pytest

from test_api import client
from app.services.scheduler import scheduler
from app.services.storage import resolve
from app.worker import artifacts

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "interactive-inputs"


@pytest.fixture()
def prepared(client):
    project = client.post('/api/projects', json={
        'name': '现场参数示例', 'path': str(EXAMPLE), 'module': 'diagnosis',
        'python': sys.executable, 'factory': '',
    })
    project.raise_for_status()
    instances = []

    def upload(name):
        response = client.post('/api/files', files={'file': (name, (EXAMPLE / name).read_bytes(), 'application/json')})
        response.raise_for_status()
        return response.json()

    def publish(mutate=None):
        doc = json.loads((EXAMPLE / 'workflow.json').read_text(encoding='utf-8'))
        if mutate:
            mutate(doc)
        flow = client.post('/api/flows/import', json={'doc': doc})
        flow.raise_for_status()
        release = client.post(f"/api/flows/{flow.json()['id']}/releases", json={})
        release.raise_for_status()
        return flow.json()['id'], client.get(f"/api/releases/{release.json()['id']}").json()

    def start(flow, release, **overrides):
        report = upload('report.json')
        values = {'report_path': {'hash': report['hash'], 'name': report['name']},
                  'channels': ['A', 'B'], 'options': {'mode': 'offline', 'nested': [1, {'enabled': True}]},
                  **overrides}
        response = client.post(f'/api/flows/{flow}/instances', json={
            'releaseId': release['id'], 'input': {'global': values, 'scope': {}},
        })
        if response.status_code == 200:
            instances.append(response.json()['id'])
        return response

    yield client, publish, start, upload
    for instance in instances:
        thread = scheduler._threads.get(instance)
        if thread:
            thread.join(10)
        scheduler._sessions.close(instance)


def detail_when(client, instance, predicate):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        detail = client.get(f'/api/instances/{instance}').json()
        if predicate(detail):
            return detail
        time.sleep(0.02)
    raise AssertionError(detail)


def pending(detail, kind):
    return next((review for review in detail['reviews']
                 if review['status'] == 'pending' and review['request'].get('type') == kind), None)


def test_start_upload_and_node_runtime_structures(prepared):
    client, publish, start, upload = prepared
    flow, release = publish()
    assert {field['name']: field['type'] for field in release['inputs']} == {
        'report_path': 'file', 'channels': 'array', 'options': 'object',
    }
    assert release['plan']['diagnosis']['args']['report_path']['file'] is True
    response = start(flow, release)
    response.raise_for_status()
    instance = response.json()['id']
    detail = detail_when(client, instance, lambda item: pending(item, 'parameters'))
    review = pending(detail, 'parameters')
    assert not any(run['nodeId'] == 'inspect' for run in detail['runs'])
    init = next(run for run in detail['runs'] if run['nodeId'] == 'start')
    assert isinstance(init['input']['report_path'], str)
    assert Path(init['input']['report_path']).is_file()
    assert init['input']['channels'] == ['A', 'B']
    assert init['input']['options']['nested'] == [1, {'enabled': True}]

    evidence = upload('evidence.json')
    data = {'samples': [1, 2, 6], 'thresholds': {'max': 3},
            'evidence_path': {'hash': evidence['hash'], 'path': 'C:/forged/client/path'}}
    for invalid in ({**data, 'samples': '[1,2,6]'}, {**data, 'thresholds': []},
                    {**data, 'evidence_path': {'hash': '0' * 64}}, {**data, 'samples': []}):
        bad = client.post(f"/api/instances/{instance}/reviews/{review['id']}", json={'result': 'done', 'data': invalid})
        assert bad.status_code == 409, bad.text
        assert pending(client.get(f'/api/instances/{instance}').json(), 'parameters')['id'] == review['id']

    accepted = client.post(f"/api/instances/{instance}/reviews/{review['id']}", json={'result': 'done', 'data': data})
    accepted.raise_for_status()
    judgement = pending(accepted.json(), 'judgment')
    assert judgement
    assert Path(judgement['request']['pipeline']['inputs']['evidence_path']) == resolve(evidence['hash'])
    assert judgement['request']['pipeline']['inputs']['samples'] == [1, 2, 6]
    assert client.post(f"/api/instances/{instance}/reviews/{review['id']}", json={'result': 'done', 'data': data}).status_code == 409
    client.post(f"/api/instances/{instance}/reviews/{judgement['id']}", json={'result': 'yes', 'reason': '确认曲线'}).raise_for_status()
    final = detail_when(client, instance, lambda item: item['status'] == 'escalated')
    output = next(run['output']['data'] for run in final['runs'] if run['nodeId'] == 'confirm')
    saved = artifacts.load(scheduler._artifact_root(instance), output)
    assert saved['samples'] == [1, 2, 6]
    assert saved['channels'] == ['A', 'B']


def test_missing_required_method_parameters_are_deferred(prepared):
    client, publish, start, upload = prepared
    def mutate(doc):
        doc['nodes'][1]['runtimeInputs'] = [{'name': 'evidence_path', 'type': 'file'}]
    flow, release = publish(mutate)
    fields = release['plan']['steps']['inspect']['diagnosisMethod']['inputs']
    assert {field['name']: field['type'] for field in fields} == {
        'samples': 'array', 'thresholds': 'object', 'evidence_path': 'file',
    }
    assert 'samples' not in {item['name'] for item in release['inputs']}
    response = start(flow, release)
    response.raise_for_status()
    detail = detail_when(client, response.json()['id'], lambda item: pending(item, 'parameters'))
    assert pending(detail, 'parameters')


def test_runtime_selection_removes_obsolete_start_fields(prepared):
    client, publish, start, upload = prepared
    def mutate(doc):
        doc['nodes'][0]['inputs'].append({
            'name': 'samples', 'scope': 'function', 'function': 'FileDiagnosis.inspect', 'type': 'array',
        })
    flow, release = publish(mutate)
    assert 'samples' not in {field['name'] for field in release['inputs']}
    response = start(flow, release)
    response.raise_for_status()
    assert pending(detail_when(client, response.json()['id'], lambda item: pending(item, 'parameters')), 'parameters')


@pytest.mark.parametrize('values', [
    {'report_path': 'C:/client/report.json'},
    {'report_path': {'hash': '0' * 64}},
    {'channels': 'A,B'},
    {'options': []},
])
def test_invalid_start_file_or_structure_is_rejected(prepared, values):
    client, publish, start, upload = prepared
    flow, release = publish()
    assert start(flow, release, **values).status_code == 400


def test_empty_array_object_and_json_null_remain_values(prepared):
    client, publish, start, upload = prepared
    def mutate(doc):
        doc['nodes'][0]['inputs'][2]['type'] = 'json'
    flow, release = publish(mutate)
    response = start(flow, release, channels=[], options=None)
    response.raise_for_status()
    detail = detail_when(client, response.json()['id'], lambda item: pending(item, 'parameters'))
    init = next(run for run in detail['runs'] if run['nodeId'] == 'start')
    assert init['input']['channels'] == []
    assert init['input']['options'] is None

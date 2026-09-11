"""Isolated provider protocol boundaries; subprocess responses here are fixtures."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.optional_runtimes import OptionalRuntime
from evidence_lane_plugin.provider_operations import verify_asset

from .test_optional_runtimes import installation as installation  # noqa: PLC0414


@pytest.fixture
def operational_runtime(installation):
    environment, contracts, path, record = installation
    manifest = json.loads(path.read_text())
    manifest.update(operations=['code_embed_text'], packages=[{'name': name, 'version': 'fixture-only'}
        for name in ('sentence-transformers', 'torch', 'numpy')])
    path.write_text(json.dumps(manifest))
    record['manifest_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (environment / 'environment.json').write_text(json.dumps(record))
    return OptionalRuntime.from_installation(environment, contracts)


@pytest.mark.parametrize('fault', [None, 'nonce', 'operation', 'provider', 'device', 'runtime', 'manifest', 'lock', 'failed', 'process', 'oversize'])
def test_provider_output_is_bound_to_one_exact_operation(operational_runtime, monkeypatch, fault):
    runtime = operational_runtime
    invoked = []
    def child(argv, **kwargs):
        invoked.append(argv)
        assert argv == [str(runtime.python), '-I', str(Path(__file__).resolve().parents[1] /
            'plugins/evidence-lane-plugin/src/evidence_lane_plugin/provider_operations.py')]
        assert kwargs['env']['HF_HUB_OFFLINE'] == '1'
        assert 'CUDA_VISIBLE_DEVICES' not in kwargs['env']
        request = json.loads(kwargs['input'])
        value = {'request_id': request['request_id'], 'operation': 'code_embed_text', 'status': 'ok',
            'result': {'compute': {'selected_provider': 'CPU', 'runtime_id': 'cpu', 'device_id': 'cpu', 'device_index': 0,
                'environment_digest': runtime.lock_sha256, 'runtime_manifest_sha256': runtime.manifest_sha256,
                'execution_state': 'executed'}}}
        fields = {'nonce': 'request_id', 'operation': 'operation', 'failed': 'status'}
        proof = {'provider': 'selected_provider', 'device': 'device_id', 'runtime': 'runtime_id',
            'manifest': 'runtime_manifest_sha256', 'lock': 'environment_digest'}
        if fault in fields:
            value[fields[fault]] = 'wrong'
        if fault in proof:
            value['result']['compute'][proof[fault]] = 'wrong'
        kwargs['stdout'].write(b'x' * 4_194_305 if fault == 'oversize' else json.dumps(value).encode())
        return SimpleNamespace(returncode=1 if fault == 'process' else 0)
    monkeypatch.setattr('evidence_lane_plugin.optional_runtimes.subprocess.run', child)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0')
    if fault is None:
        result = runtime.execute('code_embed_text', {'texts': ['fixture']}, device_id='cpu', device_index=0, required_vram_mib=1024)
        assert result['compute']['selected_provider'] == 'CPU'
    else:
        with pytest.raises(LaneError) as error:
            runtime.execute('code_embed_text', {'texts': ['fixture']}, device_id='cpu', device_index=0, required_vram_mib=1024)
        assert error.value.code == 'PROVIDER_RESULT_INVALID'
    assert len(invoked) == 1


def test_self_test_only_environment_cannot_execute_lane_work(installation, monkeypatch):
    environment, contracts, _, _ = installation
    runtime = OptionalRuntime.from_installation(environment, contracts)
    monkeypatch.setattr('evidence_lane_plugin.optional_runtimes.subprocess.run', lambda *a, **k: pytest.fail('No invocation is permitted'))
    assert not runtime.supports_operation('code_embed_text')
    with pytest.raises(LaneError) as error:
        runtime.execute('code_embed_text', {}, device_id='cpu', device_index=0, required_vram_mib=1024)
    assert error.value.code == 'PROVIDER_OPERATION_UNAVAILABLE'


def test_worker_cannot_switch_runtime_executable_or_lock(operational_runtime):
    runtime = operational_runtime
    value = runtime.worker_binding()
    assert OptionalRuntime.from_worker_binding(value) == runtime
    with pytest.raises(LaneError):
        OptionalRuntime.from_worker_binding(value | {'python': str(runtime.python.with_name('injected.exe'))})
    (runtime.manifest_path.parent / 'cpu.lock.txt').write_text('changed')
    with pytest.raises(LaneError) as error:
        OptionalRuntime.from_worker_binding(value)
    assert error.value.code == 'RUNTIME_LOCK_CHANGED'


@pytest.mark.parametrize('conflict', [None, 'onnxruntime', 'opencv-python'])
def test_directml_ocr_requires_separate_noncolliding_model_runtime(installation, conflict):
    environment, contracts, path, record = installation
    manifest = json.loads(path.read_text())
    names = ['rapidocr', 'onnxruntime-directml', 'onnx', 'numpy', 'opencv-python-headless', 'pillow']
    manifest.update(runtime_id='directml', operations=['rapidocr_lines'],
        packages=[{'name': name, 'version': 'fixture-only'} for name in names + ([conflict] if conflict else [])])
    target = contracts / 'directml.json'
    target.write_text(json.dumps(manifest))
    record.update(runtime_id='directml', manifest_path=str(target), manifest_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    (environment / 'environment.json').write_text(json.dumps(record))
    runtime = OptionalRuntime.from_installation(environment, contracts)
    assert runtime.supports_operation('rapidocr_lines') is (conflict is None)
    assert not runtime.supports_operation('code_embed_text')


@pytest.mark.parametrize('fault', ['bytes', 'added', 'escape', 'duplicate'])
def test_child_model_file_verification_rejects_changed_inputs(tmp_path, fault):
    folder = tmp_path / 'model'
    folder.mkdir()
    model = folder / 'config.json'
    model.write_text('{}')
    request = {'model_path': str(folder), 'model_files': [{'path': model.name, 'bytes': 2,
        'sha256': hashlib.sha256(model.read_bytes()).hexdigest()}]}
    assert verify_asset(request) == folder
    if fault == 'bytes':
        model.write_text('[]')
    elif fault == 'added':
        (folder / 'modules.json').write_text('{}')
    elif fault == 'escape':
        request['model_files'][0]['path'] = '../config.json'
    else:
        request['model_files'].append(dict(request['model_files'][0]))
    with pytest.raises(ValueError):
        verify_asset(request)

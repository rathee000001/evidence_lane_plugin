import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hashing import canonical_json_bytes
from evidence_lane_plugin.shared_native_tools import (
    POINTER_SCHEMA,
    NativeInvocationRequest,
    resolve_native_tool,
    run_native_tool,
)


@pytest.fixture
def bundle(tmp_path):
    if os.name != 'nt':
        pytest.skip('The retained portable binary fixture targets Windows only.')
    original = Path(os.environ['EVI_NATIVE_QUALIFICATION_ASSETS']) / 'native/ripgrep/bin/rg.exe'
    expected = '14231169855ec5205cf5a1b6f1db358ff4aed4247c86b69ce8aae647c77f6680'
    assert hashlib.sha256(original.read_bytes()).hexdigest() == expected
    root = tmp_path / 'isolated-studio'
    executable = root / 'toolchains/bin/ripgrep/rg.exe'
    executable.parent.mkdir(parents=True)
    shutil.copyfile(original, executable)
    body = {'schema': POINTER_SCHEMA, 'status': 'verified', 'tools': [{
        'tool_id': 'ripgrep', 'status': 'verified', 'executable': executable.relative_to(root).as_posix(),
        'executable_sha256': expected, 'version': '15.2.0', 'license_receipt_sha256': 'fixture-license'}]}
    write_pointer(root, body)
    return root, executable, body


def write_pointer(root, body):
    pointer = {**body, 'receipt_sha256': hashlib.sha256(canonical_json_bytes(body)).hexdigest()}
    (root / 'toolchains/native-installation.v4.json').write_text(json.dumps(pointer), encoding='utf-8')


def test_real_verified_shared_binary_consumes_stdin_and_returns_attributed_output(bundle):
    root, _, _ = bundle
    result = run_native_tool(NativeInvocationRequest(tool_id='ripgrep', arguments=['--no-config', '-F', 'needle', '-'],
        input_bytes=b'needle\nother\nneedle two\n', host_profile='CODEX_CLI'), runtime_root=root)
    assert result['status'] == 'PASS'
    assert result['stdout'] == 'needle\nneedle two\n'
    assert result['host_profile_basis'] == 'configured_not_attested'
    assert not result['shell_used']
    assert result['installed_dependency_scope'] == 'shared_studio_bundle'


def test_changed_binary_is_rejected_before_invocation(bundle):
    root, executable, _ = bundle
    executable.write_bytes(b'changed')
    with pytest.raises(LaneError) as failure:
        resolve_native_tool('ripgrep', runtime_root=root)
    assert failure.value.code == 'NATIVE_TOOL_HASH_MISMATCH'


@pytest.mark.parametrize('path', ['../../other.exe', 'C:/other.exe', 'toolchains/bin/rg.exe:stream'])
def test_self_sealed_pointer_cannot_escape_installation(bundle, path):
    root, _, body = bundle
    body['tools'][0]['executable'] = path
    write_pointer(root, body)
    with pytest.raises(LaneError) as failure:
        resolve_native_tool('ripgrep', runtime_root=root)
    assert failure.value.code == 'NATIVE_TOOL_PATH_INVALID'


def test_v3_pass_pointer_is_not_v4_installation_proof(bundle):
    root, _, body = bundle
    body.update(schema='evidence-lane.installed-native-toolchain.v1', status='PASS')
    write_pointer(root, body)
    with pytest.raises(LaneError) as failure:
        resolve_native_tool('ripgrep', runtime_root=root)
    assert failure.value.code == 'NATIVE_INSTALLATION_INVALID'


def test_process_output_is_stopped_at_its_byte_budget(bundle):
    root, _, _ = bundle
    with pytest.raises(LaneError) as failure:
        run_native_tool(NativeInvocationRequest(tool_id='ripgrep', arguments=['--no-config', '-F', 'x', '-'],
            input_bytes=b'x\n' * 6000, host_profile='CODEX_CLI', max_output_bytes=1024), runtime_root=root)
    assert failure.value.code == 'NATIVE_TOOL_OUTPUT_BOUND_EXCEEDED'

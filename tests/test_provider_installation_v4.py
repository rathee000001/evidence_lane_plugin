"""Shared installation binding reads; fixture interpreters are never executed."""
import hashlib
import json

import pytest
from evidence_lane_plugin.installation_layout import StudioInstallation
from evidence_lane_plugin.installed_providers import load_installed_providers


@pytest.mark.parametrize('fault', [None, 'root', 'outside', 'manifest', 'lock', 'duplicate', 'missing_index'])
def test_shared_provider_index_binds_the_exact_canonical_environment(tmp_path, fault):
    installation = StudioInstallation(tmp_path / 'shared')
    contracts = tmp_path / 'contracts'
    contracts.mkdir()
    lock = contracts / 'cuda.lock.txt'
    lock.write_text('fixture wheels')
    lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest()
    path = contracts / 'cuda.json'
    path.write_text(json.dumps({'runtime_id': 'cuda', 'lock_file': lock.name,
        'lock_sha256': lock_sha, 'packages': [], 'operations': []}))
    manifest_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    directory = installation.python_environments / 'providers' / ('cuda-' + lock_sha[:16])
    python = directory / 'Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'fixture only')
    (directory / 'environment.json').write_text(json.dumps({'runtime_id': 'cuda', 'python': str(python),
        'manifest_path': str(path), 'manifest_sha256': manifest_sha, 'lock_sha256': lock_sha,
        'installation_state': 'installed_from_locked_wheels'}))
    row = {'runtime_id': 'cuda', 'environment': directory.relative_to(installation.root).as_posix(),
        'manifest_sha256': manifest_sha, 'lock_sha256': lock_sha}
    record = {'schema': 'evidence-lane.provider-installation.v4', 'installation_root': str(installation.root), 'providers': [row]}
    if fault == 'root':
        record['installation_root'] = str(tmp_path / 'other')
    elif fault == 'outside':
        row['environment'] = '../outside'
    elif fault == 'manifest':
        row['manifest_sha256'] = '0' * 64
    elif fault == 'lock':
        lock.write_text('changed')
    elif fault == 'duplicate':
        record['providers'].append(dict(row))
    index = installation.toolchains / 'provider-installation.v4.json'
    if fault != 'missing_index':
        index.write_text(json.dumps(record))
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    runtimes, status = load_installed_providers(installation=installation, contracts=contracts)
    assert len(runtimes) == (0 if fault else 1)
    assert not status['full_bundle_ready']
    assert status['execution_state'] == 'not_probed_by_installation_read'
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


def test_service_loads_shared_provider_bindings_by_default(tmp_path, monkeypatch):
    from evidence_lane_plugin.runtime_health import CapabilityMonitor
    from evidence_lane_plugin.service import Service
    monitor = CapabilityMonitor(device_probe=list, installation_status={'state': 'fixture_shared_installation'})
    monkeypatch.setattr(CapabilityMonitor, 'from_shared_installation', classmethod(lambda cls: monitor))
    service = Service(tmp_path / 'runtime', studio_launcher=lambda url: False)
    assert service.engine.capabilities is monitor
    assert monitor.snapshot()['provider_installation']['state'] == 'fixture_shared_installation'
    assert service.engine.phase == 'created'
    service.close()

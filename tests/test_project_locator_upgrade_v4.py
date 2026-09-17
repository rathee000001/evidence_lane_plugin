from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/scripts/first_detection.py'
SPEC = importlib.util.spec_from_file_location('locator_upgrade_first_detection', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
IDENTITY = '902b95a9-bcab-45fd-990b-2159a94da0a7'


def installer(tmp_path):
    plugin = tmp_path / 'plugin'
    plugin.mkdir()
    return MODULE.FirstDetectionInstaller(plugin, tmp_path / 'installation')


def document(tmp_path):
    return {'version': 1, 'projects': {IDENTITY: {
        'project_id': IDENTITY, 'state_root': str((tmp_path / 'external-state').resolve()),
        'source_root': str((tmp_path / 'source').resolve()), 'read_only': False,
    }}}


def write_directory(owner, value):
    path = owner.root / 'engine/projects.json'
    path.parent.mkdir(parents=True)
    payload = (json.dumps(value) + '\n').encode()
    path.write_bytes(payload)
    return payload


def test_project_locators_are_preserved_without_opening_external_project_data(tmp_path):
    owner = installer(tmp_path)
    value = document(tmp_path)
    payload = write_directory(owner, value)
    external = Path(value['projects'][IDENTITY]['state_root'])
    assert not external.exists()
    assert owner._read_project_directory() == (payload, 1)
    assert not external.exists()


def test_absent_registry_is_legitimate_and_creates_no_project_data(tmp_path):
    owner = installer(tmp_path)
    assert owner._read_project_directory() == (None, 0)
    assert not owner.root.exists()


@pytest.mark.parametrize('failure', ['selection', 'identity', 'relative', 'credential', 'internal-state', 'version-type', 'metadata-type'])
def test_malformed_or_connection_shaped_registry_is_rejected_unchanged(tmp_path, failure):
    owner = installer(tmp_path)
    value = document(tmp_path)
    record = value['projects'][IDENTITY]
    if failure == 'selection':
        value['selected_project'] = IDENTITY
    elif failure == 'identity':
        record['project_id'] = '00000000-0000-0000-0000-000000000000'
    elif failure == 'relative':
        record['state_root'] = 'relative-state'
    elif failure == 'credential':
        record['client_token'] = 'must-not-migrate'
    elif failure == 'internal-state':
        record['state_root'] = str(owner.root / 'engine/user-data')
    elif failure == 'version-type':
        value['version'] = True
    elif failure == 'metadata-type':
        record['display_name'] = {'session': 'must-not-migrate'}
    payload = write_directory(owner, value)
    with pytest.raises(MODULE.FirstDetectionError) as invalid:
        owner._read_project_directory()
    assert invalid.value.code == 'INSTALLATION_PROJECT_DIRECTORY_INVALID'
    assert (owner.root / 'engine/projects.json').read_bytes() == payload

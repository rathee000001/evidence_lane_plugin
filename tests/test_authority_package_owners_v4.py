from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from evidence_lane_plugin.authority_support import (
    AUTHORITY_SOURCE_OWNERS,
    authority_actions,
    authority_package_folder,
)
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.lanes import AUTHORITY_LANE_IDS, CANONICAL_LANE_IDS, get_lane
from evidence_lane_plugin.plugin_architecture import build_universal_plugin_architecture
from evidence_lane_plugin.sdk import EvidenceLaneClient
from evidence_lane_plugin.storage import ProjectStore

from tests.test_plan_authority import definition
from tests.test_session_v4 import selected

__all__ = ['selected']

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'


def module(folder, filename):
    path = PLUGIN / folder / filename
    spec = importlib.util.spec_from_file_location(folder.replace('/', '_') + '_' + path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def client_for(engine, session):
    sdk = PublicActionSDKDispatcher(engine)
    return EvidenceLaneClient(SimpleNamespace(send=lambda request: sdk.execute(request, session)))


def test_owner_templates_preserve_separate_lane_identity_and_actual_schema():
    registry = json.loads((PLUGIN / 'authorities/authority-surface-registry.v4.json').read_bytes())
    assert registry['authority_count'] == 9
    assert {row['authority_id'] for row in registry['authorities']} == set(AUTHORITY_LANE_IDS)
    assert registry['root_pv'] == 'authorities/project_authority/manifest.v4.json'
    for row in registry['authorities']:
        lane = row['authority_id']
        folder = PLUGIN / row['path']
        assert row['path'] == 'authorities/' + AUTHORITY_SOURCE_OWNERS[lane]
        manifest = json.loads((folder / 'manifest.v4.json').read_bytes())
        jsonschema.Draft202012Validator(json.loads((folder / 'manifest.schema.json').read_bytes())).validate(manifest)
        assert manifest['project_state_folder'] == get_lane(lane).folder
        assert manifest['runtime_database_packaged'] is False
        schema = json.loads((folder / 'sqlite-schema.v4.json').read_bytes())
        templates = [PLUGIN / item['path'] for item in manifest['members'] if item['path'].endswith('.sqlite')]
        assert len(templates) == 1
        raw = templates[0].read_bytes()
        assert hashlib.sha256(raw).hexdigest() == schema['sqlite_sha256']
        with sqlite3.connect(':memory:') as connection:
            connection.deserialize(raw)
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            assert connection.execute('SELECT count(*) FROM lane_identity').fetchone()[0] == 0
            seeds = schema.get('static_schema_metadata', {})
            for table in schema['tables']:
                quoted = '"' + table['name'].replace('"', '""') + '"'
                assert [list(item) for item in connection.execute('SELECT * FROM ' + quoted)] == seeds.get(table['name'], [])
        assert schema['business_rows'] == schema['project_identity_rows'] == 0
        for member in manifest['members']:
            target = PLUGIN / member['path']
            assert target.resolve().is_relative_to(folder.resolve())
            assert hashlib.sha256(target.read_bytes()).hexdigest() == member['sha256']


def test_owner_read_schema_rejects_mutation_cross_owner_and_wrong_arguments():
    schema = json.loads((PLUGIN / 'authorities/plan/reader-contract.schema.json').read_bytes())
    validator = jsonschema.Draft202012Validator(schema)
    request = {'action': 'plan_read', 'project_id': '11111111-1111-4111-8111-111111111111', 'arguments': {'limit': 25}}
    validator.validate(request)
    for wrong in (request | {'action': 'plan_create'}, request | {'action': 'memory_read'},
                  request | {'arguments': {'limit': 100000}}, request | {'project_id': None}):
        assert list(validator.iter_errors(wrong))


def test_original_plan_and_session_builders_use_real_sdk_and_preserve_read_boundaries(selected):
    engine, project, _, session = selected
    client = client_for(engine, session)
    session_builder = module('authorities/session_authority', 'builder.py')
    boot = session_builder.build_authority(client, action='session_boot', project_id=project.project_id,
        arguments={'reported_session_id': 'original-owner-test', 'expected_root_pv_digest': project.pv_head()['head_digest']})
    assert boot.status == 'ok', boot
    assert boot.result['native_task_attestation'] == 'not_provided'
    builder = module('authorities/plan', 'builder.py')
    created = builder.build_authority(client, action='plan_create', project_id=project.project_id,
        arguments={'title': 'Owning Plan', 'tasks': [definition('first').model_dump(mode='json')]})
    assert created.status == 'ok', created
    before = hashes(project.root)
    reader = module('authorities/plan', 'reader.py')
    read = reader.read_authority(client, action='plan_read', project_id=project.project_id)
    assert read.status == 'ok' and read.result['revision'] == 1, read
    assert len(read.result['tasks']) == 1
    for action in ('plan_create', 'memory_ingest'):
        with pytest.raises(LaneError) as caught:
            reader.read_authority(client, action=action, project_id=project.project_id)
        assert caught.value.code == 'AUTHORITY_ACTION_MISMATCH'
    with pytest.raises(LaneError) as caught:
        reader.read_authority(client, action='plan_read')
    assert caught.value.code == 'PROJECT_REQUIRED'
    assert hashes(project.root) == before
    assert {path.name for path in project.root.iterdir() if path.is_dir() and path.name in AUTHORITY_LANE_IDS} == set(AUTHORITY_LANE_IDS)
    assert not (project.root / 'authorities').exists()


def test_source_builder_cannot_skip_planned_admission_and_sdk_grants_still_apply(selected):
    engine, project, _, session = selected
    client = client_for(engine, session)
    before = hashes(project.root)
    with pytest.raises(LaneError) as caught:
        module('authorities/source_authority', 'builder.py').build_authority(client,
            action='source_snapshot_retire', project_id=project.project_id, arguments={})
    assert caught.value.code == 'AUTHORITY_ACTION_MISMATCH'
    assert hashes(project.root) == before
    _, read_only = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
        project_id=project.project_id, permissions=['read'])]))
    response = module('authorities/plan', 'builder.py').build_authority(client_for(engine, read_only),
        action='plan_create', project_id=project.project_id,
        arguments={'title': 'Denied', 'tasks': [definition('denied').model_dump(mode='json')]})
    assert response.status == 'error', response
    assert response.error.code == 'PROJECT_NOT_SELECTED'
    assert hashes(project.root) == before


def test_root_session_instruction_and_canon_graph_owners_bind_existing_engine(selected):
    engine, project, _, session = selected
    root = module('authorities/project_authority', 'runtime.py')
    assert root.ProjectStore is ProjectStore
    from evidence_lane_plugin.canon_consequence_graph import canon_pointer, canon_view
    from evidence_lane_plugin.canon_task_graph import CanonStore
    from evidence_lane_plugin.session_authority import SessionAuthority
    assert module('authorities/session_authority', 'runtime.py').SessionAuthority is SessionAuthority
    consequence = module('authorities/canon_input/consequence_graph', 'runtime.py')
    assert consequence.CanonStore is CanonStore
    assert consequence.canon_view is canon_view and consequence.canon_pointer is canon_pointer
    layout = json.loads((PLUGIN / 'authorities/project_authority/live-root-layout.v4.json').read_bytes())
    assert {row['lane_id'] for row in layout['lanes']} == set(CANONICAL_LANE_IDS)
    before = hashes(project.root)
    response = module('authorities/instructions', 'reader.py').read_authority(client_for(engine, session),
        action='instructions_inspect', project_id=project.project_id)
    assert response.status == 'ok', response
    assert hashes(project.root) == before
    architecture = build_universal_plugin_architecture(PLUGIN, registry=engine.registry)
    assert architecture['root_pv']['folder'] == 'authorities/project_authority'
    assert len(architecture['surfaces']) == 22
    for owner in architecture['workflow_owners']:
        assert owner['actions'] == authority_actions(engine.registry, owner['owner_id'])
        assert owner['logical_authority_added'] is False
    assert {row['source_package_folder'] for row in architecture['surfaces'] if row['surface_kind'] == 'authority'} == {
        authority_package_folder(lane) for lane in AUTHORITY_LANE_IDS}


def test_authority_compiler_check_is_readonly_and_sector_outputs_are_unchanged(selected):
    engine, _, _, _ = selected
    sys.path.insert(0, str(PLUGIN / 'scripts'))
    try:
        from regenerate_authority_packages import generate
        from regenerate_sector_packages import generate as sectors
        before = hashes(PLUGIN / 'authorities')
        assert generate(engine.registry, check=True)['changed'] == []
        assert sectors(engine.registry, check=True)['changed'] == []
        assert hashes(PLUGIN / 'authorities') == before
    finally:
        sys.path.remove(str(PLUGIN / 'scripts'))


def test_retired_source_packages_have_no_members_and_retained_owners_are_closed():
    # Empty filesystem directories are not Git/package members. Their removal
    # can be independently blocked by host policy without reviving a route.
    for relative in ('root_pv', 'authorities/canon', 'authorities/memory', 'authorities/learning',
                     'authorities/sources', 'authorities/receipts', 'authorities/universe',
                     'authorities/project_sectors/plan', 'authorities/project_sectors/chat_lineage'):
        assert not [path for path in (PLUGIN / relative).rglob('*') if path.is_file()]
    assert not (PLUGIN / 'authorities/authority-surface-registry.v1.json').exists()
    assert not (PLUGIN / 'contracts/plan.workflow.v4.json').exists()
    assert not (PLUGIN / 'contracts/chatlineage.workflow.v4.json').exists()
    from evidence_lane_plugin.build import PACKAGE_DIRECTORIES
    assert 'root_pv' not in PACKAGE_DIRECTORIES
    assert 'tunnel' not in PACKAGE_DIRECTORIES
    assert not (PLUGIN / 'tunnel').exists()
    connection = json.loads(
        (PLUGIN / 'manifests/engine-connection.v4.json').read_bytes()
    )
    assert connection['schema'] == 'evidence-lane.engine-connection-contract.v4'
    assert connection['superseded_hidden_bearer_route_retired'] is True
    runtime_status = json.loads(
        (PLUGIN / 'schemas/actions/runtime_status.v4.schema.json').read_bytes()
    )
    assert runtime_status['outputSchema']['title'] == 'RuntimeStatus'
    for folder in [*(authority_package_folder(lane) for lane in AUTHORITY_LANE_IDS),
                   'authorities/project_authority', 'authorities/instructions']:
        manifest = json.loads((PLUGIN / folder / 'manifest.v4.json').read_bytes())
        declared = {row['path'] for row in manifest['members']} | {folder + '/manifest.v4.json'}
        actual = {path.relative_to(PLUGIN).as_posix() for path in (PLUGIN / folder).rglob('*')
                  if path.is_file() and '__pycache__' not in path.parts}
        assert declared == actual

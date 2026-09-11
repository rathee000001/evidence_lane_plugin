from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from evidence_lane_plugin import authority_support
from evidence_lane_plugin.authority_support import (
    AUTHORITY_SUPPORT_PROFILES,
    authority_migrations,
    authority_package_contract,
    authority_package_folder,
    refresh_authority_support,
    validate_authority_support,
)
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lanes import AUTHORITY_LANE_IDS, get_lane
from evidence_lane_plugin.migrations import Migration, apply_migrations
from evidence_lane_plugin.storage import LANE_SCHEMA, ProjectStore
from evidence_lane_plugin.writers import WriterLease

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'


def files(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


@pytest.fixture
def project(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    return ProjectStore.create(tmp_path / 'state', source)


@pytest.mark.parametrize('lane_id', AUTHORITY_LANE_IDS)
def test_authority_initializes_complete_owning_schemas_and_packaged_sql(project, lane_id):
    runtime_path = PLUGIN / authority_package_folder(lane_id) / 'runtime.py'
    spec = importlib.util.spec_from_file_location('authority_' + lane_id, runtime_path)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    unrelated = {name: files(project.lane(name).folder) for name in AUTHORITY_LANE_IDS
                 if name not in {lane_id, 'receipts'}}
    with WriterLease(project, 'authority-bindings-fixture') as writer:
        refreshed = runtime.initialize(project, writer=writer)
        lane = project.lane(lane_id)
        expected = authority_migrations(lane_id)
        expected_history = {(item.owner, item.version, item.digest) for item in expected}
        with lane.connection(read_only=True) as connection:
            assert {tuple(row) for row in connection.execute(
                'SELECT owner,version,digest FROM schema_migrations')} == expected_history
            assert {row[0] for row in connection.execute('SELECT DISTINCT owner FROM schema_ownership')} == {
                item.owner for item in expected}
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            histories = [dict(row) for row in connection.execute('SELECT * FROM schema_history_files')]
            actual_schema = {tuple(row) for row in connection.execute(
                "SELECT name,type FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'")}
        assert len(histories) == len(expected_history)
        for row in histories:
            path = lane.schema_history / row['filename']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row['digest']
            content = json.loads(path.read_bytes())
            assert (content['project_id'], content['lane_id']) == (project.project_id, lane_id)
        assert Path(refreshed['database']) == project.root / get_lane(lane_id).database_relative_path
        assert refreshed['automatic_graph_exports'] is False
        assert refreshed['separate_tool_installation'] is False
        assert {row['lane_id'] for row in project.lane_catalog()} == set(AUTHORITY_LANE_IDS)
        assert all(files(project.lane(name).folder) == before for name, before in unrelated.items())
        before_history_files = files(lane.schema_history)
        again = refresh_authority_support(lane, lane_id, writer=writer)
        assert again['migrations_applied'] == []
        assert files(lane.schema_history) == before_history_files
    before_read = files(project.root)
    inspected = runtime.inspect(ProjectStore(project.root, read_only=True))
    assert inspected['mutation_performed'] is False
    assert {row['owner'] for row in inspected['schema_compatibility']} == {item.owner for item in expected}
    assert all(row['status'] == 'compatible' for row in inspected['schema_compatibility'])
    assert inspected['lane_head']['head_digest']
    assert files(project.root) == before_read

    folder = PLUGIN / authority_package_folder(lane_id)
    manifest = json.loads((folder / 'manifest.v4.json').read_bytes())
    for name, value in authority_package_contract(lane_id).items():
        assert manifest[name] == value
    for member in manifest['members']:
        assert hashlib.sha256((PLUGIN / member['path']).read_bytes()).hexdigest() == member['sha256']
    with sqlite3.connect(':memory:') as packaged:
        packaged.executescript(LANE_SCHEMA)
        packaged.executescript((folder / 'schema.sql').read_text(encoding='utf-8'))
        projected_schema = set(packaged.execute(
            "SELECT name,type FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"))
    assert projected_schema == actual_schema - {('schema_history_files', 'table')}


@pytest.mark.parametrize('lane_id,omitted', [
    ('chat_lineage', ('codex_turn_control', 'TURN_MIGRATIONS')),
    ('sources', ('source_routing', 'ROUTE_MIGRATIONS')),
    ('receipts', ('agent_learning', 'HOST_MEMORY_MIGRATIONS')),
    ('receipts', ('storage_selection', 'STORAGE_MIGRATIONS')),
])
def test_missing_authority_binding_is_rejected_before_project_changes(project, monkeypatch, lane_id, omitted):
    profiles = dict(AUTHORITY_SUPPORT_PROFILES)
    profile = profiles[lane_id]
    profiles[lane_id] = replace(profile, migration_bindings=tuple(
        binding for binding in profile.migration_bindings if binding != omitted))
    monkeypatch.setattr(authority_support, 'AUTHORITY_SUPPORT_PROFILES', profiles)
    before = files(project.root)
    with pytest.raises(LaneError) as caught:
        refresh_authority_support(project, lane_id)
    assert caught.value.code == 'AUTHORITY_SCHEMA_BINDINGS'
    assert files(project.root) == before


@pytest.mark.parametrize('wrong_binding,code', [
    (('project_memory', 'MEMORY_MIGRATIONS'), 'AUTHORITY_SCHEMA_BINDINGS'),
    (('codex_turn_control', 'TURN_MIGRATIONS'), 'AUTHORITY_MIGRATION_SEQUENCE'),
])
def test_cross_lane_or_duplicate_binding_is_rejected_before_writes(project, monkeypatch, wrong_binding, code):
    profiles = dict(AUTHORITY_SUPPORT_PROFILES)
    profile = profiles['chat_lineage']
    profiles['chat_lineage'] = replace(profile, migration_bindings=(*profile.migration_bindings, wrong_binding))
    monkeypatch.setattr(authority_support, 'AUTHORITY_SUPPORT_PROFILES', profiles)
    before = files(project.root)
    with pytest.raises(LaneError) as caught:
        refresh_authority_support(project, 'chat_lineage')
    assert caught.value.code == code
    assert files(project.root) == before


def test_initialization_preserves_unrelated_lane_records_and_files(project):
    docs = project.lane('docs', create=True)
    apply_migrations(docs, (Migration('docs', 1, 'Preserved fixture', (
        'CREATE TABLE doc_fixture(id INTEGER PRIMARY KEY, value TEXT)',
        "INSERT INTO doc_fixture VALUES(1,'preserve this row')",
    )),))
    (docs.files / 'fixture.bin').write_bytes(b'preserve these unrelated bytes')
    before = files(docs.folder)
    with WriterLease(project, 'authority-bindings-fixture') as writer:
        for lane_id in AUTHORITY_LANE_IDS:
            refresh_authority_support(project, lane_id, writer=writer)
    assert files(docs.folder) == before


def test_read_cannot_initialize_or_migrate_an_authority(project):
    before = files(project.root)
    inspected = validate_authority_support(project, 'chat_lineage')
    assert all(row['status'] == 'not_initialized' for row in inspected['schema_compatibility'])
    with pytest.raises(LaneError) as caught:
        refresh_authority_support(ProjectStore(project.root, read_only=True), 'chat_lineage')
    assert caught.value.code == 'READ_ONLY_PROJECT'
    assert files(project.root) == before


def test_existing_partial_authority_reports_missing_owner_without_mutating(project):
    from evidence_lane_plugin.lineage import LINEAGE_MIGRATIONS
    apply_migrations(project, LINEAGE_MIGRATIONS)
    before = files(project.root)
    observed = validate_authority_support(project, 'chat_lineage')
    statuses = {row['owner']: row['status'] for row in observed['schema_compatibility']}
    assert statuses == {'lineage': 'compatible', 'continuation': 'not_initialized',
                        'prompt': 'not_initialized', 'turn': 'not_initialized'}
    assert files(project.root) == before


def test_failed_authority_upgrade_rolls_back_schemas_history_and_receipts(project, monkeypatch):
    from evidence_lane_plugin import codex_turn_control
    refresh_authority_support(project, 'chat_lineage')
    lineage = project.lane('chat_lineage')
    before_database = lineage.database.read_bytes()
    before_receipts = project.lane('receipts').database.read_bytes()
    before_head = project.pv_head()
    monkeypatch.setattr(codex_turn_control, 'TURN_MIGRATIONS', (*codex_turn_control.TURN_MIGRATIONS,
        Migration('turn', 2, 'Failing fixture upgrade', (
            'CREATE TABLE turn_partial(id TEXT)', 'INSERT INTO turn_missing VALUES(1)',
        ))))
    with pytest.raises(LaneError) as caught:
        refresh_authority_support(project, 'chat_lineage')
    assert caught.value.code == 'MIGRATION_FAILED'
    assert lineage.database.read_bytes() == before_database
    assert project.lane('receipts').database.read_bytes() == before_receipts
    assert project.pv_head() == before_head


def test_authority_rejects_store_or_writer_owned_by_another_lane_or_project(project, tmp_path):
    refresh_authority_support(project, 'memory')
    before = files(project.root)
    with pytest.raises(LaneError) as caught:
        refresh_authority_support(project.lane('memory'), 'chat_lineage')
    assert caught.value.code == 'AUTHORITY_STORE_MISMATCH'
    other = ProjectStore.create(tmp_path / 'other', project.source_root)
    with WriterLease(other, 'wrong-owner-fixture') as writer, pytest.raises(LaneError) as caught:
        refresh_authority_support(project, 'chat_lineage', writer=writer)
    assert caught.value.code == 'WRITER_PROJECT_MISMATCH'
    assert files(project.root) == before

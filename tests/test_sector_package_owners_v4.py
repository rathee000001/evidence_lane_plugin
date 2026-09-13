"""Packaged sector entrypoints must call their actual lane owners."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.migrations import apply_migrations
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.sdk import EvidenceLaneClient
from evidence_lane_plugin.sector_support import (
    IMPLEMENTED_SECTORS,
    sector_migrations,
    sector_package_folder,
)
from evidence_lane_plugin.selector_schema import selector_migrations

from tests.test_code_profile_v4 import call, code_system
from tests.test_source_preparation_v4 import prepare
from tests.test_source_routing_v4 import finished, registered

__all__ = ['code_system']

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'


@pytest.mark.parametrize('lane_id', IMPLEMENTED_SECTORS)
def test_sector_package_runtime_uses_its_own_schema_and_read_only_reader(tmp_path, lane_id):
    path = PLUGIN / sector_package_folder(lane_id) / 'runtime.py'
    spec = importlib.util.spec_from_file_location('sector_runtime_' + lane_id, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LANE_ID == lane_id
    assert module.migrations() == sector_migrations(lane_id)
    source = tmp_path / 'source'
    source.mkdir()
    engine = Engine(tmp_path / 'engine')
    entry = engine.directory.register(tmp_path / 'project', source_root=source, create=True, read_only=False)
    project = engine.directory.open(entry['project_id'])
    before = {p.relative_to(project.root): p.read_bytes() for p in project.root.rglob('*') if p.is_file()}
    result = module.inspect(project)
    assert result['initialized'] is False
    assert before == {p.relative_to(project.root): p.read_bytes() for p in project.root.rglob('*') if p.is_file()}


def package_module(lane_id, filename):
    path = PLUGIN / sector_package_folder(lane_id) / filename
    spec = importlib.util.spec_from_file_location(lane_id + '_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('lane_id', IMPLEMENTED_SECTORS)
def test_existing_sector_history_remains_readable_before_optional_retirement_history(tmp_path, lane_id):
    engine = Engine(tmp_path / 'engine')
    source = tmp_path / 'source'
    source.mkdir()
    entry = engine.directory.register(tmp_path / 'project', source_root=source, create=True, read_only=False)
    project = engine.directory.open(entry['project_id'], write=True)
    selector = selector_migrations(lane_id)
    parser_history = tuple(item for item in sector_migrations(lane_id) if item.owner != selector[0].owner)
    with engine.project_work.mutation(project) as lease:
        apply_migrations(project, parser_history, writer=lease)
    module = package_module(lane_id, 'runtime.py')
    def read_and_preserve():
        before = {p.relative_to(project.root): p.read_bytes() for p in project.root.rglob('*') if p.is_file()}
        assert module.inspect(project)['initialized'] is True
        for view in engine.registry.view_schemas():
            if view['lane_id'] == lane_id:
                owner = engine.registry.get_view(view['view_id'])
                if owner.head_reader:
                    owner.head_reader(project)
        assert before == {p.relative_to(project.root): p.read_bytes() for p in project.root.rglob('*') if p.is_file()}
    read_and_preserve()
    with engine.project_work.mutation(project) as lease:
        apply_migrations(project, selector, writer=lease)
    read_and_preserve()


def test_original_sector_packages_preserve_empty_schemas_and_conditional_artifact_roles():
    for lane_id in IMPLEMENTED_SECTORS:
        folder = PLUGIN / sector_package_folder(lane_id)
        manifest = json.loads((folder / 'manifest.v4.json').read_bytes())
        assert manifest['source_package_folder'] == sector_package_folder(lane_id)
        assert manifest['folder'] == lane_id
        assert manifest['folder_role'] == 'external_project_state'
        for member in manifest['members']:
            path = PLUGIN / member['path']
            assert path.resolve().is_relative_to(folder.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest() == member['sha256']
        schema = json.loads((folder / 'schema-template.json').read_bytes())
        raw = (folder / (lane_id + '_sector_v001.sqlite')).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == schema['sqlite_sha256']
        with sqlite3.connect(':memory:') as connection:
            connection.deserialize(raw)
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            assert connection.execute('SELECT count(*) FROM lane_identity').fetchone()[0] == 0
            for table in schema['tables']:
                name = '"' + table['name'].replace('"', '""') + '"'
                assert connection.execute('SELECT count(*) FROM ' + name).fetchone()[0] == 0
        tools = json.loads((folder / 'tools.json').read_bytes())
        jsonschema.Draft202012Validator(json.loads((folder / 'tools.schema.json').read_bytes())).validate(tools)
        assert tools['builder'] == 'builder.py:build_lane_sources'
        assert tools['reader'] == 'reader.py:read_lane_source'
        assert tools['views'] and all(view['lane_id'] == lane_id for view in tools['views'])
        assert all(view['pointer_scope'] in {None, 'immutable_snapshot_navigation'} for view in tools['views'])
        for filename in ('workflow.mmd', 'workflow.dot', lane_id + '.mmd', lane_id + '.dot'):
            assert (folder / filename).stat().st_size > 0


def test_packaged_reader_schema_rejects_wrong_lane_action_and_payload():
    folder = PLUGIN / sector_package_folder('github_code')
    validator = jsonschema.Draft202012Validator(json.loads((folder / 'reader-contract.schema.json').read_bytes()))
    request = {'project_id': '11111111-1111-4111-8111-111111111111', 'action': 'code_query',
               'arguments': {'lane_id': 'github_code', 'snapshot_id': 'a' * 64, 'query': 'source'}}
    validator.validate(request)
    for bad in (request | {'action': 'code_index'}, request | {'arguments': request['arguments'] | {'lane_id': 'local_code'}},
                request | {'arguments': {'lane_id': 'github_code', 'paths': ['.']}}):
        assert list(validator.iter_errors(bad))


def test_original_builder_and_reader_use_normal_plan_sdk_worker_and_verified_exit(code_system):
    engine, store, session = code_system
    route = registered(code_system, ['.'])
    prepared = prepare(code_system, route)
    assert prepared.status == 'ok', prepared
    tasks = prepared.result['result']['tasks']
    assert call(code_system, 'plan_create', {'title': 'Original sector builder', 'tasks': tasks}).status == 'ok'
    view = PlanStore(store).task(tasks[0]['task_id'], expected_revision=1)
    sdk = PublicActionSDKDispatcher(engine)
    client = EvidenceLaneClient(SimpleNamespace(send=lambda request: sdk.execute(request, session)))
    arguments = {'project_id': store.project_id, 'task_id': view.definition.task_id,
                 'plan_revision': 1, 'contract_digest': view.contract_digest}
    with pytest.raises(LaneError, match='sector entrypoint'):
        package_module('docs', 'builder.py').build_lane_sources(client, **arguments)
    assert engine.workers.status()['submitted'] == 0
    admitted = package_module('local_code', 'builder.py').build_lane_sources(client, **arguments)
    output = finished(code_system, admitted)['result']['result']
    assert output['files'] == 3 and PlanStore(store).snapshot().counts == {'completed': 1}
    reader = package_module('local_code', 'reader.py')
    response = reader.read_lane_source(client, project_id=store.project_id, action='code_query',
        arguments={'snapshot_id': output['snapshot_id'], 'query': 'greeting'})
    assert response.status == 'ok', response
    assert {row['path'] for row in response.result['result']['rows']} == {'app.py', 'helper.py'}
    for action, values in [('code_index', {'paths': ['.']}),
                           ('code_query', {'lane_id': 'github_code', 'snapshot_id': output['snapshot_id']})]:
        with pytest.raises(LaneError, match='sector entrypoint'):
            reader.read_lane_source(client, project_id=store.project_id, action=action, arguments=values)

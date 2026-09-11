"""Native source actions: granted paths, owning lanes, attribution and failures."""
import asyncio
import json
import sqlite3

import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.sdk import ActionRequest, dispatch

from .test_native_workflow_bindings import call, native, projects


@pytest.fixture
def selected(tmp_path):
    with Engine(tmp_path / 'runtime') as engine:
        source = tmp_path / 'source'
        source.mkdir()
        (source / 'module.py').write_text('def measure():\n    return 42\n')
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        with engine.project_work.mutation(store) as lease:
            access = ProjectAccess(store)
            access.initialize(writer=lease)
            grant = access.issue('fixture', ['read', 'write'], [source], writer=lease)
        context = ActionContext('fixture', store.project_id, frozenset({'read', 'write'}),
            authorize=lambda permission: ProjectAccess(store).authorize('fixture', permission))
        yield engine, store, context, grant


def invoke(selected, action, **arguments):
    engine, store, context, _ = selected
    return dispatch(engine.registry, ActionRequest(action=action, project_id=store.project_id, arguments=arguments), context)


def register(selected):
    result = invoke(selected, 'source_register', sources=[str(selected[1].source_root / 'module.py')])
    assert result.status == 'ok', result
    return result.result['result']['source_authority']['batch_id']


def test_registered_source_actions_publish_owning_files_and_attributed_receipts(selected):
    engine, store, _, _ = selected
    before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    classified = invoke(selected, 'source_classify', sources=[str(store.source_root / 'module.py')])
    assert classified.status == 'ok', classified
    assert {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before
    batch = register(selected)
    assert invoke(selected, 'source_verify', batch_id=batch).status == 'ok'
    page = invoke(selected, 'source_read', collection='occurrences', batch_id=batch)
    assert page.status == 'ok', page
    assert len(page.result['result']['rows']) == 1
    with store.connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='source_occurrence'").fetchone() is None
    with store.lane('receipts').connection(read_only=True) as connection:
        rows = connection.execute("SELECT body_json FROM receipts WHERE kind='source_sdk_action'").fetchall()
        assert any(json.loads(row[0])['client_id'] == 'fixture' for row in rows)
    sources = {row['name']: row for row in engine.registry.schemas() if row['profile'] == 'sources'}
    assert {'source_classify', 'source_register', 'source_verify', 'source_read'} <= sources.keys()
    # A data profile is not a first-class workflow. Refresh keeps its original
    # owner even when its operations read or replace Sources-owned records.
    refresh = {'source_prepare_refresh', 'source_snapshot_state', 'source_snapshot_retire'}
    assert {name for name, row in sources.items() if row['workflow'] == 'refresh-project-evidence'} == refresh
    assert all(row['workflow'] == 'manage-project-sources' for name, row in sources.items() if name not in refresh)
    (store.source_root / 'module.py').write_text('def measure():\n    return 43\n')
    changed = invoke(selected, 'source_verify', batch_id=batch)
    assert changed.status == 'error' or changed.result['result']['status'] != 'PASS'


def test_native_source_scope_rejects_ungranted_paths_unknown_parameters_and_revocation(selected, tmp_path):
    engine, store, _, grant = selected
    other = tmp_path / 'outside.txt'
    other.write_text('Outside fixture.')
    denied = invoke(selected, 'source_register', sources=[str(other)])
    assert denied.error.code == 'PERMISSION_DENIED'
    unknown = invoke(selected, 'source_classify', sources=[str(other)], authority_registry_path=str(other))
    assert unknown.error.code == 'INVALID_ARGUMENTS'
    credential = invoke(selected, 'source_classify', sources=['https://example.invalid/source?token=fixture'])
    assert credential.error.code == 'SOURCE_CREDENTIAL_FREE_POINTER_REQUIRED'
    with engine.project_work.mutation(store) as lease:
        ProjectAccess(store).revoke(grant, writer=lease)
    assert invoke(selected, 'source_read').error.code == 'PERMISSION_DENIED'


def test_source_result_failure_rolls_back_source_and_receipt_publication(selected, monkeypatch):
    import evidence_lane_plugin.source_intake as intake
    def reject(*args):
        raise LaneError('SOURCE_RESULT_BUDGET', 'Fixture result budget.')
    monkeypatch.setattr(intake, '_source_action_result', reject)
    result = invoke(selected, 'source_register', sources=[str(selected[1].source_root / 'module.py')])
    assert result.error.code == 'SOURCE_RESULT_BUDGET'
    with selected[1].lane('sources').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='intake_batch'").fetchone() is None
        assert connection.execute('SELECT count(*) FROM objects').fetchone()[0] == 0


def test_registered_sqlite_batch_scope_is_rechecked_before_reading_sidecars(selected, monkeypatch):
    engine, store, _, grant = selected
    database = store.source_root / 'granted.sqlite'
    writer = sqlite3.connect(database)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE wal_only(value INTEGER)')
        writer.commit()
        registered = invoke(selected, 'source_register', sources=[str(database), str(database) + '-wal'])
        assert registered.status == 'ok', registered
        batch = registered.result['result']['source_authority']['batch_id']
        measured = invoke(selected, 'source_inspect_sqlite', batch_id=batch)
        assert measured.status == 'ok' and measured.result['result']['canonical_pass_count'] == 1, measured
        unrelated = store.source_root / 'unrelated'
        unrelated.mkdir()
        with engine.project_work.mutation(store) as lease:
            access = ProjectAccess(store)
            access.revoke(grant, writer=lease)
            access.issue('fixture', ['read', 'write'], [unrelated], writer=lease)
        from evidence_lane_plugin import source_sqlite
        def unexpected(*args, **kwargs):
            raise AssertionError('A source image was opened after its directory grant was revoked.')
        monkeypatch.setattr(source_sqlite, '_open_direct', unexpected)
        denied = invoke(selected, 'source_inspect_sqlite', batch_id=batch)
        assert denied.status == 'error' and denied.error.code == 'PERMISSION_DENIED'
    finally:
        writer.close()


def test_native_source_intake_sqlite_and_graph_use_existing_component_bodies(tmp_path):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        store, _ = projects(engine, tmp_path)
        code = store.source_root / 'module.py'
        code.write_text('def first():\n    return second()\ndef second():\n    return 42\n')
        database = store.source_root / 'selected.sqlite'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE measurements(value INTEGER)')
            connection.execute('INSERT INTO measurements VALUES(42)')
        before = database.read_bytes()
        async def run():
            async with native(engine.root, store.project_id, permissions=('read', 'write')) as session:
                tools = await session.list_tools()
                assert {'source_register', 'source_graph', 'source_inspect_sqlite', 'source_read'} <= {t.name for t in tools.tools}
                catalog = await call(session, 'workflow_catalog', workflow='manage-project-sources')
                assert catalog['result']['workflows'][0]['skill'] == 'manage-project-sources'
                assert {row['name'] for row in catalog['result']['workflows'][0]['actions']} == {
                    row['name'] for row in engine.registry.schemas()
                    if row['workflow'] == 'manage-project-sources'
                }
                registered = await call(session, 'source_register', store.project_id, sources=[str(code), str(database)])
                assert registered['status'] == 'ok', registered
                batch = registered['result']['result']['source_authority']['batch_id']
                for action in ['source_inspect_sqlite', 'source_graph', 'source_verify']:
                    value = await call(session, action, store.project_id, batch_id=batch)
                    assert value['status'] == 'ok', value
                page = await call(session, 'source_read', store.project_id, collection='graphs', batch_id=batch)
                assert len(page['result']['result']['rows']) == 1, page
                assert database.read_bytes() == before
        asyncio.run(run())

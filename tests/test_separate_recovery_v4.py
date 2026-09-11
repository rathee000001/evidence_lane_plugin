from __future__ import annotations

# pytest resolves this imported fixture by its public name.
# ruff: noqa: F811
import hashlib
import json
from pathlib import Path

import pytest
from evidence_lane_plugin.artifact_contract import LaneArtifacts, ViewPreview, ViewRefresh
from evidence_lane_plugin.database_recovery import (
    BackupRequest,
    DatabaseRecovery,
    restore_backup_offline,
    verify_backup,
)
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lane_contract import ViewScope
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, SECTOR_LANE_IDS
from evidence_lane_plugin.session_authority import SessionAuthority
from evidence_lane_plugin.storage import ProjectStore

from tests.test_database_recovery_v4 import (  # noqa: F401 - shared real engine fixture
    backup,
    project,
)
from tests.test_session_v4 import boot
from tests.test_universe_federation_v4 import (  # noqa: F401 - real selected federation fixture
    create_and_register,
    federation,
    grant_link,
    hashes,
)


def test_all_lane_databases_and_registered_bytes_survive_fresh_root_recovery(project, tmp_path):
    engine, store, _admin = project
    with engine.project_work.mutation(store) as lease:
        for lane_id in SECTOR_LANE_IDS:
            with lease.transaction(lane_id):
                store.lane(lane_id).put_object(('Evidence owned only by '+lane_id).encode())
    _, saved = backup(project, tmp_path)
    manifest = verify_backup(saved.backup_root, saved.manifest_digest, store.project_id)
    assert saved.lane_count == len(CANONICAL_LANE_IDS) == 21
    assert {item['lane_id'] for item in manifest['lanes']} == set(CANONICAL_LANE_IDS)
    for item in store.lane_catalog():
        relative = item['database_path']
        assert (Path(saved.backup_root)/'payload'/relative).read_bytes() == (store.root/relative).read_bytes() or item['lane_id'] == 'receipts'
    assert engine.stop()
    original = {item['path']:(store.root/item['path']).read_bytes() for item in manifest['files']}
    restored = restore_backup_offline(engine.root, store.project_id, saved.backup_root, saved.manifest_digest, tmp_path/'recovered')
    assert restored['backup_root_pv'] == saved.root_pv and restored['restored_lane_count'] == 21
    recovered = ProjectStore(tmp_path/'recovered', read_only=True)
    for lane_id in SECTOR_LANE_IDS:
        payload = ('Evidence owned only by '+lane_id).encode()
        assert recovered.lane(lane_id).read_object(hashlib.sha256(payload).hexdigest()) == payload
        assert recovered.lane(lane_id).schema_history.is_dir()
    assert all((store.root/path).read_bytes() == value for path,value in original.items())


def test_historical_natural_views_and_schema_files_are_required(project, tmp_path):
    engine, store, admin = project
    views = LaneArtifacts(engine, store)
    first = views._select('plan.dependencies')
    with store.lane(first.lane_id).connection(read_only=True) as connection:
        old = connection.execute('SELECT snapshot_digest FROM views_current').fetchone()[0]
    scope = ViewScope(node_limit=50)
    preview = views.preview(ViewPreview(view_id='plan.dependencies', scope=scope))
    with engine.project_work.mutation(store) as lease:
        views.refresh(ViewRefresh(view_id='plan.dependencies', scope=scope, expected_generation=1,
            contract_digest=preview.contract_digest, source_digest=preview.source_digest,
            formats=[], include_pointer=True), lease, actor_id=admin.client_id)
    _, saved = backup(project, tmp_path)
    manifest = verify_backup(saved.backup_root, saved.manifest_digest, store.project_id)
    assert len([item for item in manifest['files'] if item['path'].endswith('plan-pointer.json')]) == 2
    historical = next(item for item in manifest['files'] if old in item['path'])
    (Path(saved.backup_root)/'payload'/historical['path']).write_bytes(b'Changed historical view')
    with pytest.raises(LaneError, match='recorded digest or size'):
        verify_backup(saved.backup_root, saved.manifest_digest, store.project_id)
    assert any('/schema_history/' in item['path'] or '/schema-history/' in item['path'] for item in manifest['files'])


def test_copy_interruption_preserves_incomplete_output_and_never_seals_it(project, tmp_path, monkeypatch):
    engine, store, admin = project
    import evidence_lane_plugin.database_recovery as module
    actual = module._copy
    copies = []
    def interrupted(*args, **kwargs):
        actual(*args, **kwargs)
        copies.append(str(args[2]))
        if len(copies) == 3:
            raise LaneError('INJECTED_COPY_INTERRUPTION', 'The test stops after three complete copied files.')
    monkeypatch.setattr(module, '_copy', interrupted)
    request = BackupRequest(destination_root=str(tmp_path/'interrupted'))
    with engine.project_work.mutation(store) as lease, pytest.raises(LaneError, match='three complete'):
        DatabaseRecovery(engine, store).backup(request, lease, actor_id=admin.client_id)
    assert len(copies) == 3 and all(Path(path).is_file() for path in copies)
    assert not (tmp_path/'interrupted'/'backup.json').exists()
    with engine.project_work.mutation(store) as lease, pytest.raises(LaneError) as error:
        DatabaseRecovery(engine, store).backup(request, lease, actor_id=admin.client_id)
    assert error.value.code == 'BACKUP_INCOMPLETE' and len(copies) == 3


def test_fresh_root_recovery_closes_saved_session_and_preserves_session_history(project, tmp_path):
    engine, store, admin = project
    started = boot(engine, store, admin)
    assert started.status == 'ok', started.error
    assert SessionAuthority(store).verify_history()['events_verified'] == 1
    _, saved = backup(project, tmp_path)
    assert engine.stop()
    result = restore_backup_offline(engine.root, store.project_id, saved.backup_root, saved.manifest_digest, tmp_path/'recovered')
    assert result['sessions_closed'] == 1
    with Engine(engine.root) as restarted:
        selected = restarted.directory.open(store.project_id)
        session = SessionAuthority(selected)
        with session.store.connection(read_only=True) as connection:
            current = session.current(connection)
            body = json.loads(connection.execute('SELECT body_json FROM sessions_events WHERE digest=?', (current['event_digest'],)).fetchone()[0])
        assert current['state'] == 'closed' and current['generation'] == 2
        assert not body['flash_current'] and not body['capture_bound'] and not body['owner_authenticated']
        assert session.verify_history() == {'events_verified':2, 'sessions_verified':1}
        with pytest.raises(LaneError) as disconnected:
            restarted.clients.session(admin.client_id)
        assert disconnected.value.code == 'CLIENT_SESSION_EXPIRED'


def test_recovered_federation_revokes_old_grants_without_rewriting_members_or_links(federation, tmp_path):
    from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
    from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
    from evidence_lane_plugin.sdk import ActionRequest
    engine, projects, _, call = federation
    records = create_and_register(federation)
    request = grant_link(federation, records)
    linked = call('project_evidence_network_link', request)
    assert linked.status == 'ok', linked.error
    before_members = [hashes(project) for project in projects[1:]]
    _, admin = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
        project_id=projects[0].project_id, permissions=['read', 'write', 'admin'])]))
    saved = call('project_backup', {'destination_root': str(tmp_path / 'federation-backup')}, actor=admin)
    assert saved.status == 'ok', saved.error
    assert [hashes(project) for project in projects[1:]] == before_members
    assert engine.stop()
    # Engine shutdown revokes its original client grants in all selected
    # projects. Recovery preservation starts after that quiescent boundary.
    before_members = [hashes(project) for project in projects[1:]]
    original = hashes(projects[0])
    result = restore_backup_offline(engine.root, projects[0].project_id,
        saved.result['backup_root'], saved.result['manifest_digest'], tmp_path / 'recovered-federation')
    assert result['federation_grants_revoked'] == 1
    assert hashes(projects[0]) == original
    with Engine(engine.root) as restarted:
        _, session = restarted.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=projects[0].project_id, permissions=['read', 'write'])]))
        sdk = PublicActionSDKDispatcher(restarted)
        def read(action, arguments=None):
            return sdk.execute(ActionRequest(action=action, project_id=projects[0].project_id,
                arguments=arguments or {}), session)
        assert read('project_evidence_network_verify').status == 'ok'
        assert read('project_evidence_network_link', request).error.code == 'FEDERATION_GRANT_INACTIVE'
        assert read('project_evidence_network_read', {'view': 'links'}).result['result']['records'] == [linked.result['result']]
    assert [hashes(project) for project in projects[1:]] == before_members


def test_federation_recovery_receipt_failure_rolls_back_all_lane_changes(federation, tmp_path, monkeypatch):
    from evidence_lane_plugin.storage import LaneStore
    engine, projects, session, _call = federation
    records = create_and_register(federation)
    grant_link(federation, records)
    project = projects[0]
    with engine.project_work.mutation(project) as lease:
        saved = DatabaseRecovery(engine, project).backup(BackupRequest(
            destination_root=str(tmp_path / 'backup-with-grant')), lease, actor_id=session.client_id)
    assert engine.stop()
    original = hashes(project)
    directory = engine.directory.path.read_bytes()
    original_append = LaneStore.append_receipt
    injected = []
    def fail(self, kind, *args, **kwargs):
        if kind == 'database_recovered_to_fresh_root':
            injected.append(self.lane_id)
            raise LaneError('INJECTED_RECOVERY_RECEIPT', 'Stop after grant revocation, before publication.')
        return original_append(self, kind, *args, **kwargs)
    monkeypatch.setattr(LaneStore, 'append_receipt', fail)
    destination = tmp_path / 'interrupted-recovery'
    with pytest.raises(LaneError) as failed:
        restore_backup_offline(engine.root, project.project_id, saved.backup_root, saved.manifest_digest, destination)
    assert failed.value.code == 'INJECTED_RECOVERY_RECEIPT' and injected == ['receipts']
    assert hashes(project) == original and engine.directory.path.read_bytes() == directory
    recovered = ProjectStore(destination, read_only=True)
    assert recovered.pv_head() == saved.root_pv
    for lane_id in ('receipts', 'universe', 'plan'):
        lane = recovered.lane(lane_id)
        assert lane.database.read_bytes() == (Path(saved.backup_root) / 'payload' / lane.definition.database_relative_path).read_bytes()
    assert not (destination / 'recovery-result.json').exists()


def test_reconciliation_detects_changed_lane_even_when_root_database_is_unchanged(project, tmp_path, monkeypatch):
    engine, store, _admin = project
    _, saved = backup(project, tmp_path)
    assert engine.stop()
    import evidence_lane_plugin.database_recovery as module
    publish = module.atomic_json
    monkeypatch.setattr(module, 'atomic_json', lambda *a, **k: (_ for _ in ()).throw(LaneError('INJECTED_SELECTION_FAILURE', 'Unselected root retained.')))
    destination = tmp_path/'recovered'
    with pytest.raises(LaneError, match='Unselected root'):
        restore_backup_offline(engine.root, store.project_id, saved.backup_root, saved.manifest_digest, destination)
    root_bytes = (destination/'root-pv.sqlite3').read_bytes()
    selected = ProjectStore(destination)
    selected.lane('chat_lineage').database.write_bytes(b'Corrupt independent lane')
    assert (destination/'root-pv.sqlite3').read_bytes() == root_bytes
    monkeypatch.setattr(module, 'atomic_json', publish)
    with pytest.raises(LaneError):
        restore_backup_offline(engine.root, store.project_id, saved.backup_root, saved.manifest_digest, destination, reconcile=True)
    assert engine.directory.entries()[store.project_id]['state_root'] == str(store.root)


@pytest.mark.parametrize('omitted_kind', ['lane', 'schema'])
def test_manifest_cannot_omit_lane_database_or_schema_history(project, tmp_path, omitted_kind):
    _, store, _ = project
    _, saved = backup(project, tmp_path)
    root = Path(saved.backup_root)
    manifest = json.loads((root/'backup.json').read_text())
    omitted = store.lane('chat_lineage').definition.database_relative_path if omitted_kind == 'lane' else next(
        item['path'] for item in manifest['files'] if '/schema-history/' in item['path'])
    manifest['files'] = [item for item in manifest['files'] if item['path'] != omitted]
    from evidence_lane_plugin.plan_runtime import content_digest
    from evidence_lane_plugin.storage import json_text
    (root/'backup.json').write_text(json_text(manifest), encoding='utf-8')
    with pytest.raises(LaneError) as error:
        verify_backup(root, content_digest(manifest), store.project_id)
    assert error.value.code == 'BACKUP_INVENTORY_MISMATCH'


def test_packaged_mcp_backup_and_offline_cli_restore_real_separate_state(project, tmp_path):
    import asyncio
    import os
    import subprocess
    import sys
    from datetime import timedelta

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    engine, store, _admin = project
    plugin = Path(__file__).resolve().parents[1]/'plugins/evidence-lane-plugin'
    # First-detection release materialization is qualified separately. This
    # storage test exercises the packaged MCP adapter against its already-owned
    # engine so CI never downloads the multi-part production runtime bundle.
    parameters = StdioServerParameters(command=sys.executable, args=['-B', '-m',
        'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
        '--host-profile','codex_desktop', '--project-id',store.project_id,
        '--permission','read','--permission','write','--permission','admin'],
        env={'PYTHONPATH':str(plugin/'src'), 'EVIDENCE_LANE_STUDIO_ROOT':str(tmp_path/'uninstalled-studio')})
    async def exercise():
        async with (stdio_client(parameters) as (read,write),
                    ClientSession(read,write,read_timeout_seconds=timedelta(seconds=30)) as client):
            await client.initialize()
            async def call(action, arguments):
                response = await client.call_tool(action, {'project_id':store.project_id, 'arguments':arguments})
                value = response.structuredContent
                assert value and value['status'] == 'ok', response
                return value['result']
            saved = await call('project_backup', {'destination_root':str(tmp_path/'mcp-backup')})
            verified = await call('project_backup_verify', {key:saved[key] for key in ('backup_root','manifest_digest')})
            assert verified['lane_count'] == 8 and verified['root_pv'] == saved['root_pv']
            inspected = await call('project_recovery_inspect', {})
            assert inspected['unregistered_file_count'] == 0
            return saved
    with LocalEndpoint(engine):
        saved = asyncio.run(exercise())
    assert engine.stop()
    restored = subprocess.run([sys.executable, '-B', str(plugin/'scripts/run_recovery.py'),
        '--runtime-root',str(engine.root),'--project-id',store.project_id,'--backup-root',saved['backup_root'],
        '--manifest-digest',saved['manifest_digest'],'--destination-root',str(tmp_path/'cli-recovered')],
        capture_output=True,text=True,timeout=45,check=False,env={**os.environ,'PYTHONPATH':str(plugin/'src')},
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert restored.returncode == 0, restored.stdout + restored.stderr
    result = json.loads(restored.stdout)['result']
    assert result['original_state_preserved'] and result['restored_lane_count'] == 8
    assert ProjectStore(tmp_path/'cli-recovered',read_only=True).project_id == store.project_id


def test_recovery_inspection_remains_read_only_during_active_plan_work(project, tmp_path):
    from evidence_lane_plugin.database_recovery import RecoveryInspect
    from evidence_lane_plugin.plan_runtime import PlanStore
    engine, store, admin = project
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).transition('inspect', 'active', lease, expected_revision=1, actor_id=admin.client_id)
    before = {item['database_path']:(store.root/item['database_path']).read_bytes() for item in store.lane_catalog()}
    inspected = DatabaseRecovery(engine, store).inspect(RecoveryInspect())
    assert not inspected.repairs_performed
    assert all((store.root/path).read_bytes() == value for path,value in before.items())
    with engine.project_work.mutation(store) as lease, pytest.raises(LaneError) as blocked:
        DatabaseRecovery(engine, store).backup(BackupRequest(destination_root=str(tmp_path/'active-backup')), lease, actor_id=admin.client_id)
    assert blocked.value.code == 'RECOVERY_PLAN_CHECKPOINT_REQUIRED'

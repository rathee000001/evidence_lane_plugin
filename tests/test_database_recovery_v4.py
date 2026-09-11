import json
from pathlib import Path

import pytest
from evidence_lane_plugin.artifact_contract import LaneArtifacts, ViewPreview, ViewRead, ViewRefresh
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.database_recovery import (
    BackupRequest,
    DatabaseRecovery,
    RecoveryInspect,
    restore_backup_offline,
    verify_backup,
)
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanReplace, PlanStore, TaskDefinition
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.steering import Steering, SteerIntent
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease

from tests.storage_fixtures_v4 import declare_local_storage


@pytest.fixture
def project(tmp_path):
    source=tmp_path/'source'
    source.mkdir()
    (source/'source-only.txt').write_text('Source code stays outside database recovery.')
    declare_local_storage(tmp_path/'runtime', tmp_path/'state')
    with Engine(tmp_path/'runtime') as engine:
        record=engine.directory.register(tmp_path/'state',source_root=source,create=True,read_only=False)
        store=engine.directory.open(record['project_id'],write=True)
        _,admin=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read','write','admin'])]))
        with engine.project_work.mutation(store) as lease:
            PlanStore(store).create(PlanCreate(title='Recovery fixture',tasks=[TaskDefinition(task_id=name,title=name,requested_outcome='Verify '+name)
                for name in ('inspect','build')]),lease,actor_id=admin.client_id)
            ChatLineage(store).append(LineageRecord(kind='prompt',payload={'text':'Keep this visible request in project history.'}),lease,client_id=admin.client_id)
            with lease.transaction('sources'):
                store.lane('sources').put_object(b'registered evidence bytes')
            views=LaneArtifacts(engine,store)
            preview=views.preview(ViewPreview(view_id='plan.dependencies'))
            views.refresh(ViewRefresh(view_id='plan.dependencies',contract_digest=preview.contract_digest,
                source_digest=preview.source_digest,expected_generation=0,formats=[],include_pointer=True),lease,actor_id=admin.client_id)
        yield engine,store,admin


def backup(project,tmp_path):
    engine,store,admin=project
    request=BackupRequest(destination_root=str(tmp_path/'backup'))
    with engine.project_work.mutation(store) as lease:
        result=DatabaseRecovery(engine,store).backup(request,lease,actor_id=admin.client_id)
    return request,result


def test_consistent_backup_includes_registered_files_and_reports_orphans_without_copying(project,tmp_path):
    engine,store,admin=project
    orphan=store.root/'authorities'/'plan'/'orphan.txt'
    orphan.parent.mkdir(parents=True,exist_ok=True)
    orphan.write_text('Unselected file from a failed earlier export')
    original_plan=PlanStore(store).snapshot().model_dump()
    request,result=backup(project,tmp_path)
    manifest=verify_backup(result.backup_root,result.manifest_digest,store.project_id)
    assert result.lane_count == 8
    assert len([item for item in manifest['files'] if item['path'].endswith(('.sqlite','.sqlite3'))]) == 9
    assert result.root_pv == manifest['root_pv']
    assert any(item['path']=='root-pv.sqlite3' for item in manifest['files'])
    assert all('source-only' not in item['path'] and 'orphan' not in item['path'] for item in manifest['files'])
    assert any(item['path'].endswith('plan-pointer.json') for item in manifest['files'])
    assert PlanStore(store).snapshot().model_dump()==original_plan
    with engine.project_work.mutation(store) as lease:
        replay=DatabaseRecovery(engine,store).backup(request,lease,actor_id=admin.client_id)
    assert replay==result
    before=store.database.read_bytes()
    inspection=DatabaseRecovery(engine,store).inspect(RecoveryInspect())
    assert inspection.unregistered_file_count==1 and inspection.unregistered_sample==['authorities/plan/orphan.txt']
    assert store.database.read_bytes()==before and orphan.exists()
    with pytest.raises(LaneError) as error:
        ProjectStore(Path(result.backup_root)/'payload').append_receipt('bad',{})
    assert error.value.code=='SEALED_BACKUP_READ_ONLY'


def test_offline_recovery_refuses_running_engine_then_preserves_corrupt_original(project,tmp_path):
    engine,store,admin=project
    _,saved=backup(project,tmp_path)
    destination=tmp_path/'recovered'
    with pytest.raises(LaneError) as error:
        restore_backup_offline(engine.root,store.project_id,saved.backup_root,saved.manifest_digest,destination)
    assert error.value.code=='RUNTIME_IN_USE' and not destination.exists()
    assert engine.stop()
    store.database.write_bytes(b'explicitly corrupted fixture database')
    corrupt=store.database.read_bytes()
    result=restore_backup_offline(engine.root,store.project_id,saved.backup_root,saved.manifest_digest,destination)
    assert store.database.read_bytes()==corrupt
    assert result['original_state_preserved'] and result['clients_must_reconnect']
    assert not result['source_files_restored'] and not result['source_bytes_reverified']
    with Engine(engine.root) as restarted:
        current=restarted.directory.open(store.project_id,write=True)
        assert current.root==destination
        with pytest.raises(LaneError) as denied:
            ProjectAccess(current).authorize(admin.client_id,'write')
        assert denied.value.code=='PERMISSION_DENIED'
        old_plan=PlanStore(current).snapshot()
        with restarted.project_work.mutation(current) as lease:
            with pytest.raises(LaneError) as gated:
                PlanStore(current).transition('inspect','active',lease,expected_revision=1,actor_id='new')
            assert gated.value.code=='DATABASE_RECOVERY_PLAN_REQUIRED'
        assert LaneArtifacts(restarted,current).read(ViewRead(view_id='plan.dependencies')).state in {'fresh','contract_changed'}
        assert ChatLineage(current).verify()['events_verified']==1
        _,new=restarted.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read','write'])]))
        with restarted.project_work.mutation(current) as lease:
            event=ChatLineage(current).append(LineageRecord(kind='prompt',payload={'text':'Continue after verifying this recovered project.'}),lease,client_id=new.client_id)
            steer=SteerIntent(source_event_id=event.event_id,source_cursor=event.cursor,expected_revision=1,intent='semantic',
                              rationale='Reconcile recovered project work',affected_task_ids=['inspect','build'])
            Steering(current).submit(steer,lease,actor_id=new.client_id)
            request=PlanReplace(plan_id=old_plan.plan_id,title='Recovered work',tasks=[item.definition for item in old_plan.tasks],
                expected_revision=1,expected_document_digest=old_plan.document_digest,steer_request_id=steer.request_id,
                database_recovery_digest=result['recovery_digest'])
            assert PlanStore(current).replace(request,lease,actor_id=new.client_id).revision==2
        assert (store.source_root/'source-only.txt').read_text()=='Source code stays outside database recovery.'


@pytest.mark.parametrize('corruption',['content','manifest','missing','project'])
def test_backup_tampering_rejected_before_recovery_root_created(project,tmp_path,corruption):
    engine,store,_admin=project
    _,saved=backup(project,tmp_path)
    root=Path(saved.backup_root)
    document=json.loads((root/'backup.json').read_text())
    if corruption=='content':
        (root/'payload'/document['files'][-1]['path']).write_bytes(b'wrong')
    elif corruption=='missing':
        (root/'payload'/document['files'][-1]['path']).unlink()
    elif corruption=='manifest':
        document['source_root']='wrong root'
        (root/'backup.json').write_text(json.dumps(document))
    else:
        document['project_id']='00000000-0000-0000-0000-000000000000'
        (root/'backup.json').write_text(json.dumps(document))
    assert engine.stop()
    before=engine.directory.path.read_bytes()
    with pytest.raises((LaneError,OSError)):
        restore_backup_offline(engine.root,store.project_id,saved.backup_root,saved.manifest_digest,tmp_path/'bad-recovery')
    assert not (tmp_path/'bad-recovery').exists()
    assert engine.directory.path.read_bytes()==before


def test_failed_backup_receipt_reuses_sealed_snapshot_without_recopy(project,tmp_path,monkeypatch):
    engine,store,admin=project
    original=ProjectStore.append_receipt
    request=BackupRequest(destination_root=str(tmp_path/'backup'))
    def fail(self,kind,*args,**kwargs):
        if kind=='database_backup_verified':
            raise LaneError('FIXTURE_BACKUP_RECEIPT','Simulated receipt failure')
        return original(self,kind,*args,**kwargs)
    monkeypatch.setattr(ProjectStore,'append_receipt',fail)
    with engine.project_work.mutation(store) as lease,pytest.raises(LaneError):
        DatabaseRecovery(engine,store).backup(request,lease,actor_id=admin.client_id)
    payload=(tmp_path/'backup'/'payload'/'root-pv.sqlite3').read_bytes()
    monkeypatch.setattr(ProjectStore,'append_receipt',original)
    import evidence_lane_plugin.database_recovery as module
    monkeypatch.setattr(module,'_copy',lambda *args,**kwargs:pytest.fail('Replay recopied immutable backup'))
    with engine.project_work.mutation(store) as lease:
        result=DatabaseRecovery(engine,store).backup(request,lease,actor_id=admin.client_id)
    assert (tmp_path/'backup'/'payload'/'root-pv.sqlite3').read_bytes()==payload
    assert result.lane_count==8 and result.file_count>9


def test_directory_failure_reconciles_complete_fresh_recovery_without_rewriting_it(project,tmp_path,monkeypatch):
    engine,store,_admin=project
    _,saved=backup(project,tmp_path)
    assert engine.stop()
    destination=tmp_path/'recovered'
    import evidence_lane_plugin.database_recovery as module
    original=module.atomic_json
    def fail(*args,**kwargs):
        raise LaneError('FIXTURE_DIRECTORY_FAILED','Simulated final selection failure')
    monkeypatch.setattr(module,'atomic_json',fail)
    with pytest.raises(LaneError) as error:
        restore_backup_offline(engine.root,store.project_id,saved.backup_root,saved.manifest_digest,destination)
    assert error.value.code=='FIXTURE_DIRECTORY_FAILED'
    before=(destination/'root-pv.sqlite3').read_bytes()
    assert engine.directory.entries()[store.project_id]['state_root']==str(store.root)
    monkeypatch.setattr(module,'atomic_json',original)
    result=restore_backup_offline(engine.root,store.project_id,saved.backup_root,saved.manifest_digest,destination,reconcile=True)
    assert result['state_root']==str(destination)
    assert (destination/'root-pv.sqlite3').read_bytes()==before
    assert engine.directory.entries()[store.project_id]['state_root']==str(destination)


def test_backup_routes_require_administration_and_inspection_stays_read_only(project,tmp_path):
    engine,store,admin=project
    _,reader=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read'])]))
    sdk=PublicActionSDKDispatcher(engine)
    request=ActionRequest(action='project_backup',project_id=store.project_id,arguments=BackupRequest(destination_root=str(tmp_path/'backup')).model_dump())
    assert sdk.execute(request,reader).error.code=='PROJECT_NOT_SELECTED'
    result=sdk.execute(request,admin)
    assert result.status=='ok',result.error
    check=sdk.execute(ActionRequest(action='project_backup_verify',project_id=store.project_id,
        arguments={'backup_root':result.result['backup_root'],'manifest_digest':result.result['manifest_digest']}),admin)
    assert check.status=='ok' and check.result['sqlite_integrity']=='ok'
    assert sdk.execute(ActionRequest(action='project_recovery_inspect',project_id=store.project_id),reader).status=='ok'


def test_backup_detects_corrupt_owner_history_and_keeps_live_plan_unchanged(project,tmp_path):
    engine,store,admin=project
    with store.lane('plan').transaction() as connection:
        connection.execute("UPDATE plan_events SET digest=? WHERE sequence=1",('f'*64,))
    with engine.project_work.mutation(store) as lease,pytest.raises(LaneError) as error:
        DatabaseRecovery(engine,store).backup(BackupRequest(destination_root=str(tmp_path/'invalid')),lease,actor_id=admin.client_id)
    assert error.value.code=='PLAN_HISTORY_INTEGRITY'
    with store.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT digest FROM plan_events WHERE sequence=1').fetchone()[0]=='f'*64


def test_recovery_respects_independent_writer_lock_and_existing_destination(project,tmp_path):
    engine,store,_admin=project
    _,saved=backup(project,tmp_path)
    assert engine.stop()
    with WriterLease(store,'independent-owner'),pytest.raises(LaneError) as error:
        restore_backup_offline(engine.root,store.project_id,saved.backup_root,saved.manifest_digest,tmp_path/'recovered')
    assert error.value.code=='RUNTIME_IN_USE'
    destination=tmp_path/'occupied'
    destination.mkdir()
    (destination/'keep.txt').write_text('Preserve existing bytes')
    with pytest.raises(LaneError) as error:
        restore_backup_offline(engine.root,store.project_id,saved.backup_root,saved.manifest_digest,destination)
    assert error.value.code=='RECOVERY_FRESH_ROOT_REQUIRED'
    assert (destination/'keep.txt').read_text()=='Preserve existing bytes'

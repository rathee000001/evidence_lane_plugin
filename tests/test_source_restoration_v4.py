import json
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.git_adapter import restoration_git, restoration_source_identity
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanReplace, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.steering import Steering, SteerIntent
from evidence_lane_plugin.store import (
    GitRestoreExecute,
    GitRestoreSelection,
    RestoreAbandon,
    SourceRestoration,
)


@pytest.fixture
def system(tmp_path):
    # Restoration must use the production registry and its locked Flash. The
    # former shared fixture injected fixture_hash, invalidating that exact seal
    # before any recovery path could be exercised.
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'already-open').touch()
    with Engine(tmp_path / 'runtime') as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        token, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write'])]))
        yield engine, store, token, session


def plan(system):
    engine, store, _, _ = system
    spec = engine.registry.get('code_index')
    task = TaskDefinition(task_id='first', title='Inspect selected source', requested_outcome='Index the exact restored source',
        profile='code', allowed_actions=['code_index'], permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Python_structural_parser'],
        permitted_paths=['.'], acceptance_checks=list(spec.verification_checks))
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Restoration fixture', tasks=[task]), lease, actor_id='fixture')
    return PlanStore(store).task('first', expected_revision=1)


def request(system, view):
    return ActionRequest(action='delta_enter', project_id=system[1].project_id, expected_revision=1, arguments={
        'task_id': view.definition.task_id, 'plan_revision': 1, 'contract_digest': view.contract_digest,
        'action': 'code_index', 'arguments': {'paths': ['.']}})


@pytest.fixture
def restoration(system, tmp_path):
    engine, store, _, _ = system
    root = store.source_root
    restoration_git(root, ['init', '-b', 'main'])
    (root / 'code.txt').write_text('original\n')
    (root / 'module.py').write_text('def value():\n    return 1\n')
    restoration_git(root, ['add', '--', '.'])
    restoration_git(root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Original'])
    selected = restoration_git(root, ['rev-parse', 'HEAD']).stdout.strip()
    (root / 'code.txt').write_text('second\n')
    restoration_git(root, ['add', '--', 'code.txt'])
    restoration_git(root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Second'])
    (root / 'code.txt').write_text('dirty staged\n')
    restoration_git(root, ['add', '--', 'code.txt'])
    (root / 'code.txt').write_text('dirty unstaged\n')
    (root / 'personal.txt').write_text('preserve untracked\n')
    _, admin = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,
                                                        permissions=['read', 'write', 'admin'])]))
    selection = GitRestoreSelection(branch='main', commit=selected, destination_root=str(tmp_path / 'restored'))
    def invoke(action, payload, session=None):
        return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id,
            arguments=payload.model_dump(mode='json') if hasattr(payload,'model_dump') else payload), session or admin)
    yield system, admin, selection, invoke


def preview_request(restoration):
    _system, _admin, selection, invoke = restoration
    preview = invoke('git_restore_preview',selection)
    assert preview.status == 'ok', preview.error
    return GitRestoreExecute(**selection.model_dump(), expected_preview_digest=preview.result['preview_digest'])


def test_fresh_git_restore_preserves_all_dirty_source_and_requires_exact_new_plan(restoration):
    system, _admin, selection, invoke = restoration
    engine, old_store, _, old_client = system
    view = plan(system)
    old_plan = PlanStore(old_store).snapshot().model_dump()
    identity = restoration_source_identity(old_store.source_root)
    req = preview_request(restoration)
    restored = invoke('git_restore',req)
    assert restored.status == 'ok', restored.error
    assert restoration_source_identity(old_store.source_root) == identity
    destination = Path(selection.destination_root)
    assert (destination / 'code.txt').read_text() == 'original\n'
    assert not (destination / 'personal.txt').exists()
    current = engine.directory.open(old_store.project_id,write=True)
    assert current.source_root == destination
    assert PlanStore(current).snapshot().model_dump() == old_plan
    assert invoke('restoration_read',{},old_client).error.code == 'PERMISSION_DENIED'
    with pytest.raises(LaneError,match='Reopen the project'), engine.project_work.mutation(old_store):
        pytest.fail('stale project object acquired writer')
    _, fresh = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=current.project_id,
                                                                               permissions=['read','write','admin'])]))
    assert invoke('delta_enter', request(system,view).arguments, fresh).error.code == 'SOURCE_RESTORE_PLAN_REQUIRED'
    with engine.project_work.mutation(current) as lease:
        source = ChatLineage(current).append(LineageRecord(kind='prompt',payload={'text':'Continue from the restored commit.'}),lease,client_id=fresh.client_id)
        steer = SteerIntent(source_event_id=source.event_id,source_cursor=source.cursor,expected_revision=1,
                            intent='semantic',rationale='The selected source changed',affected_task_ids=['first'])
        Steering(current).submit(steer,lease,actor_id=fresh.client_id)
        snapshot = PlanStore(current).snapshot()
        replacement = PlanReplace(plan_id=snapshot.plan_id,title='Restored work',tasks=[view.definition],expected_revision=1,
            expected_document_digest=snapshot.document_digest,steer_request_id=steer.request_id)
        with pytest.raises(LaneError) as error:
            PlanStore(current).replace(replacement,lease,actor_id=fresh.client_id)
        assert error.value.code == 'SOURCE_RESTORE_PLAN_REQUIRED'
        with pytest.raises(LaneError) as missing_index:
            PlanStore(current).replace(replacement.model_copy(update={'source_restore_digest':restored.result['restore_digest']}),lease,actor_id=fresh.client_id)
        assert missing_index.value.code == 'SOURCE_RESTORE_REINDEX_REQUIRED'
        from evidence_lane_plugin.store import RestorationReindex
        indexed = SourceRestoration(engine,current).reindex(RestorationReindex(restore_digest=restored.result['restore_digest']),lease,actor_id=fresh.client_id)
        assert indexed.parsed_file_count >= 1 and indexed.file_count >= 2
        updated = PlanStore(current).replace(replacement.model_copy(update={'source_restore_digest':restored.result['restore_digest']}),lease,actor_id=fresh.client_id)
    assert updated.revision == 2
    state = invoke('restoration_read',{},fresh)
    assert state.result['control']['cleared_by_revision'] == 2
    assert state.result['history'][0]['body']['preview']['source_identity']['dirty_path_count'] == 2
    assert PlanStore(current).verify_history()['events_verified'] > 0


def test_preview_is_read_only_and_stale_dirty_bytes_fail_before_clone(restoration):
    system, _admin, selection, invoke = restoration
    store = system[1]
    before = store.database.read_bytes()
    req = preview_request(restoration)
    assert store.database.read_bytes() == before
    (store.source_root / 'personal.txt').write_text('changed after preview')
    result = invoke('git_restore',req)
    assert result.error.code == 'RESTORE_PREVIEW_CHANGED'
    assert not Path(selection.destination_root).exists()
    assert system[0].directory.open(store.project_id).source_root == store.source_root


@pytest.mark.parametrize('which',['source','state','runtime','parent','existing'])
def test_restore_refuses_collisions_and_overlapping_roots(restoration,which):
    system,_admin,selection,invoke = restoration
    roots={'source':system[1].source_root/'new','state':system[1].root/'new',
           'runtime':system[0].root/'new','parent':system[1].root.parent,'existing':system[1].source_root/'code.txt'}
    result=invoke('git_restore_preview',selection.model_copy(update={'destination_root':str(roots[which])}))
    assert result.error.code in {'RESTORE_ROOT_OVERLAP','RESTORE_FRESH_ROOT_REQUIRED'}


def test_receipt_failure_preserves_source_and_reconciles_without_reclone(restoration,monkeypatch):
    system,_admin,selection,invoke=restoration
    engine,store,_,_=system
    req=preview_request(restoration)
    original=store.__class__.append_receipt
    def fail(self,kind,*args,**kwargs):
        if kind=='git_source_restored':
            raise LaneError('FIXTURE_RECEIPT_FAILED','Simulated publication failure')
        return original(self,kind,*args,**kwargs)
    monkeypatch.setattr(store.__class__,'append_receipt',fail)
    assert invoke('git_restore',req).error.code=='FIXTURE_RECEIPT_FAILED'
    assert engine.directory.open(store.project_id).source_root==store.source_root
    assert Path(selection.destination_root).is_dir()
    assert invoke('git_restore',req).error.code=='RESTORE_RECONCILIATION_REQUIRED'
    monkeypatch.setattr(store.__class__,'append_receipt',original)
    import evidence_lane_plugin.store as module
    actual=module.restoration_git
    def no_clone(root,args,**kwargs):
        assert args[0]!='clone'
        return actual(root,args,**kwargs)
    monkeypatch.setattr(module,'restoration_git',no_clone)
    assert invoke('git_restore_reconcile',req).status=='ok'


def test_interrupted_clone_is_not_replayed_and_abandon_keeps_partial_files(restoration,monkeypatch):
    _system,_admin,selection,invoke=restoration
    req=preview_request(restoration)
    import evidence_lane_plugin.store as module
    def interrupted(root,args,**kwargs):
        target=Path(selection.destination_root)
        target.mkdir()
        (target/'partial.txt').write_text('retained partial output')
        raise LaneError('FIXTURE_CLONE_INTERRUPTED','Simulated incomplete clone')
    monkeypatch.setattr(module,'restoration_git',interrupted)
    assert invoke('git_restore',req).error.code=='FIXTURE_CLONE_INTERRUPTED'
    assert invoke('git_restore',req).error.code=='RESTORE_RECONCILIATION_REQUIRED'
    assert invoke('git_restore_abandon',RestoreAbandon(request_id=req.request_id,
        expected_preview_digest=req.expected_preview_digest)).status=='ok'
    assert (Path(selection.destination_root)/'partial.txt').read_text()=='retained partial output'


def test_directory_hint_failure_has_verifiable_recovery_and_new_client_can_read(restoration,monkeypatch):
    system,_admin,selection,invoke=restoration
    engine,store,_,_=system
    req=preview_request(restoration)
    def fail(*args):
        raise LaneError('FIXTURE_LOCATOR_FAILED','Simulated hint refresh failure after commit')
    monkeypatch.setattr(engine.directory,'refresh_source_locator',fail)
    assert invoke('git_restore',req).error.code=='FIXTURE_LOCATOR_FAILED'
    assert engine.directory.entries()[store.project_id]['source_root']==str(store.source_root)
    current=engine.directory.open(store.project_id,write=True)
    assert current.source_root==Path(selection.destination_root)
    _,fresh=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read','admin'])]))
    result=invoke('git_restore_reconcile',req,fresh)
    assert result.status=='ok',result.error
    assert result.result['generation']==1
    with current.lane('sources').transaction() as connection:
        connection.execute("UPDATE restoration_history SET digest=?",('0'*64,))
    with pytest.raises(LaneError) as error:
        engine.directory.open(store.project_id)
    assert error.value.code=='RESTORATION_HISTORY_INTEGRITY'


def test_no_plan_restore_requires_source_bound_initial_plan(restoration):
    system,_admin,_selection,invoke=restoration
    restored=invoke('git_restore',preview_request(restoration))
    assert restored.status=='ok',restored.error
    engine,old,_,_=system
    store=engine.directory.open(old.project_id,write=True)
    proposal=PlanCreate(title='New Plan for restored source',tasks=[TaskDefinition(task_id='inspect',title='Inspect',requested_outcome='Inspect restored source')])
    with engine.project_work.mutation(store) as lease:
        with pytest.raises(LaneError) as error:
            PlanStore(store).create(proposal,lease,actor_id='new-client')
        assert error.value.code=='SOURCE_RESTORE_PLAN_REQUIRED'
        from evidence_lane_plugin.store import RestorationReindex
        SourceRestoration(engine,store).reindex(RestorationReindex(restore_digest=restored.result['restore_digest']),lease,actor_id='new-client')
        assert PlanStore(store).create(proposal.model_copy(update={'source_restore_digest':restored.result['restore_digest']}),
                                      lease,actor_id='new-client').revision==1


def test_restore_does_not_inherit_user_template_hooks_or_global_filters(restoration,tmp_path,monkeypatch):
    _system,_admin,selection,invoke=restoration
    template=tmp_path/'template'
    (template/'hooks').mkdir(parents=True)
    (template/'hooks'/'post-checkout').write_text('#!/bin/sh\nexit 73\n')
    global_config=tmp_path/'gitconfig'
    global_config.write_text('[init]\n templateDir = '+template.as_posix()+'\n[filter "hostile"]\n smudge = exit 74\n required = true\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',str(global_config))
    monkeypatch.setenv('GIT_TEMPLATE_DIR',str(template))
    result=invoke('git_restore',preview_request(restoration))
    assert result.status=='ok',result.error
    assert not (Path(selection.destination_root)/'.git'/'hooks'/'post-checkout').exists()


def test_public_reindex_requires_reconnected_writer_and_keeps_exact_extraction_evidence(restoration):
    from evidence_lane_plugin.lanes import SECTOR_LANE_IDS

    system, _admin, selection, invoke = restoration
    engine, old, _, _ = system
    restored = invoke('git_restore', preview_request(restoration))
    assert restored.status == 'ok', restored.error
    args = {'restore_digest':restored.result['restore_digest']}
    assert invoke('restoration_reindex', args).error.code == 'PERMISSION_DENIED'
    _, fresh = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=old.project_id, permissions=['read','write'])]))
    current = engine.directory.open(old.project_id)
    def sector_databases():
        return {row['lane_id']: (current.root / row['database_path']).read_bytes()
                for row in current.lane_catalog() if row['lane_id'] in SECTOR_LANE_IDS}

    sectors_before = sector_databases()
    state = invoke('restoration_read', {}, fresh)
    assert state.result['source_reindex']['required']
    indexed = invoke('restoration_reindex', args, fresh)
    assert indexed.status == 'ok', indexed.error
    assert indexed.result['parsed_file_count'] >= 1 and indexed.result['plan_refresh_required']
    assert indexed.result['scope'] == 'registered_source_members_and_source_graph_extraction'
    assert not indexed.result['all_language_semantics_verified'] and not indexed.result['source_bytes_mutated']
    assert sector_databases() == sectors_before
    replay = invoke('restoration_reindex', args, fresh)
    assert replay.result == indexed.result
    assert not invoke('restoration_read', {}, fresh).result['source_reindex']['required']
    with current.lane('sources').connection(read_only=True) as connection:
        record = connection.execute('SELECT body_json FROM restoration_reindex').fetchone()
        assert json.loads(record[0])['result']['graph_id'] == indexed.result['graph_id']
    assert (old.source_root/'personal.txt').read_text() == 'preserve untracked\n'
    from evidence_lane_plugin.database_recovery import (
        BackupRequest,
        DatabaseRecovery,
        verify_backup,
    )
    current = engine.directory.open(old.project_id, write=True)
    with engine.project_work.mutation(current) as lease:
        backup = DatabaseRecovery(engine, current).backup(BackupRequest(
            destination_root=str(Path(selection.destination_root).parent/'reindexed-backup')), lease, actor_id=fresh.client_id)
    saved = verify_backup(backup.backup_root, backup.manifest_digest, current.project_id)
    assert saved['source_root'] == str(current.source_root)
    assert all(not item['path'].endswith('module.py') for item in saved['files'])


def test_restoration_disconnect_during_clone_keeps_original_binding_and_pending_attempt(restoration, monkeypatch):
    system, admin, selection, invoke = restoration
    engine, store, _, _ = system
    token, acting = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id, permissions=['read','write','admin'])]))
    request = preview_request(restoration)
    import evidence_lane_plugin.store as module
    real_git = module.restoration_git
    def disconnect_after_clone(root, args, **kwargs):
        result = real_git(root, args, **kwargs)
        if args[0] == 'clone':
            engine.clients.disconnect(token)
        return result
    monkeypatch.setattr(module, 'restoration_git', disconnect_after_clone)
    result = invoke('git_restore', request, acting)
    assert result.error.code == 'CLIENT_SESSION_EXPIRED'
    assert engine.directory.open(store.project_id).source_root == store.source_root
    assert Path(selection.destination_root).is_dir()
    state = invoke('restoration_read', {}, admin)
    assert state.result['requests'][0]['state'] == 'pending'


def test_reindex_rejects_changed_fresh_checkout_without_clearing_plan_gate(restoration):
    system, _admin, selection, invoke = restoration
    restored = invoke('git_restore', preview_request(restoration))
    assert restored.status == 'ok', restored.error
    engine, old, _, _ = system
    _, fresh = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=old.project_id, permissions=['read','write'])]))
    (Path(selection.destination_root)/'module.py').write_text('def changed():\n    return 2\n')
    result = invoke('restoration_reindex', {'restore_digest':restored.result['restore_digest']}, fresh)
    assert result.error.code == 'RESTORATION_REINDEX_SOURCE_CHANGED'
    assert invoke('restoration_read', {}, fresh).result['source_reindex']['required']

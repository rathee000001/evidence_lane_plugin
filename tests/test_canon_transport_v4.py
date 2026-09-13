"""Cross-project Canon stays in its source outbox and receiver-owned inbox."""
# ruff: noqa: F811 -- pytest resolves imported fixtures by parameter name.
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from evidence_lane_plugin.canon_task_graph import CanonPayload, CanonStore
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.plan_runtime import content_digest
from evidence_lane_plugin.recovery_snapshot import inventory
from evidence_lane_plugin.storage import project_snapshot

from tests.test_session_v4 import call
from tests.test_session_v4 import selected as system  # noqa: F401


@pytest.fixture
def projects(system, tmp_path):
    engine, first, _, _ = system
    source = tmp_path/'peer-source'
    source.mkdir()
    (source/'input.txt').write_text('Peer source evidence')
    record = engine.directory.register(tmp_path/'peer-state', source_root=source, create=True, read_only=False)
    second = engine.directory.open(record['project_id'], write=True)
    selections = [ProjectSelection(project_id=p.project_id, permissions=['read','write']) for p in (first,second)]
    _, sender = engine.clients.connect(ConnectRequest(projects=selections))
    _, receiver = engine.clients.connect(ConnectRequest(projects=selections))
    a = call(engine, first, sender, 'task_evidence_participant_register', {'label':'Source participant'}).result['participant_id']
    b = call(engine, second, receiver, 'task_evidence_participant_register', {'label':'Destination participant'}).result['participant_id']
    return engine, first, second, sender, receiver, a, b


def successful(engine, project, client, action, arguments=None):
    response = call(engine, project, client, action, arguments)
    assert response.status == 'ok', response
    return response.result


def packet(projects, **changes):
    engine, first, second, sender, _, a, b = projects
    arguments = {'request_id':str(uuid4()),'sender_id':a,'receiver_id':b,'destination_project_id':second.project_id,
        'kind':'requirements','payload':{'summary':'Inspect the bounded input'}, **changes}
    return successful(engine, first, sender, 'task_evidence_send', arguments), arguments


def received(projects, sealed):
    engine, first, second, _, receiver, _, _ = projects
    arguments = {'request_id':str(uuid4()),'source_project_id':first.project_id,
        'exchange_id':sealed['exchange_id'],'envelope_digest':sealed['envelope_digest']}
    return successful(engine, second, receiver, 'task_evidence_receive', arguments), arguments


def snapshot(project):
    return project.pv_head(), project.lane_catalog()


def test_seal_receive_preserve_separate_projects_files_and_receiver_ownership(projects):
    engine, first, second, sender, receiver, a, b = projects
    destination_before = snapshot(second)
    sealed, sent_args = packet(projects)
    assert sealed['state'] == 'sealed' and sealed['local_role'] == 'outbox'
    assert snapshot(second) == destination_before
    assert successful(engine, first, sender, 'task_evidence_inbox')['packets'] == []
    assert successful(engine, first, sender, 'task_evidence_read')['exchanges'][0]['local_role'] == 'outbox'
    assert successful(engine, first, sender, 'task_evidence_send', sent_args)['duplicate']
    source_before = snapshot(first)
    delivered, args = received(projects, sealed)
    assert delivered['state'] == 'received' and delivered['local_role'] == 'inbox'
    assert snapshot(first) == source_before
    assert (first.root/sealed['artifact_path']).read_bytes() == (second.root/delivered['artifact_path']).read_bytes()
    assert successful(engine, second, receiver, 'task_evidence_receive', args)['duplicate']
    assert snapshot(first) == source_before
    assert call(engine, second, sender, 'task_evidence_receive', args).error.code == 'CANON_OWNER_MISMATCH'
    assert call(engine, first, sender, 'task_evidence_decide', {'exchange_id':sealed['exchange_id'],
        'envelope_digest':sealed['envelope_digest'],'expected_version':1,'decision':'reject','reason':'Wrong project'}).error.code == 'CANON_RECEIVER_PROJECT_REQUIRED'
    with second.lane('canon').connection(read_only=True) as db:
        assert [r[0] for r in db.execute('SELECT participant_id FROM canon_participants')] == [b]
    with first.lane('canon').connection(read_only=True) as db:
        assert [r[0] for r in db.execute('SELECT participant_id FROM canon_participants')] == [a]
    inspected = successful(engine, second, receiver, 'task_evidence_inspect')
    assert inspected['objects_verified']['exchanges'] == 1 and inspected['foreign_key_errors'] == []


def test_cross_project_edge_result_only_closes_after_exact_source_admission(projects):
    engine, first, second, sender, receiver, a, b = projects
    target_contract = successful(engine, second, receiver, 'task_evidence_expect', {'receiver_id':b,'contract_key':'requirements',
        'sender_endpoints':[{'project_id':first.project_id,'participant_id':a}],'kinds':['requirements'],'auto_admit':True})
    result_contract = successful(engine, first, sender, 'task_evidence_expect', {'receiver_id':a,'contract_key':'result',
        'sender_endpoints':[{'project_id':second.project_id,'participant_id':b}],'kinds':['result'],'fields':{'count':'integer'}})
    edge = successful(engine, first, sender, 'task_evidence_edge_register', {'source_id':a,
        'destination':{'project_id':second.project_id,'participant_id':b},'contract_digest':target_contract['contract_digest'],
        'expected_return_contract':result_contract['contract_digest'],'schema_digest':content_digest(CanonPayload.model_json_schema())})
    sealed, _ = packet(projects, expected_contract=target_contract['contract_digest'],return_contract=result_contract['contract_digest'],edge_id=edge['edge_id'])
    locator = {'source_project_id':first.project_id,'exchange_id':sealed['exchange_id'],'envelope_digest':sealed['envelope_digest']}
    # Sealing alone neither copies an edge into the destination nor admits input.
    assert call(engine, second, receiver, 'task_evidence_receive', locator).error.code == 'CANON_EDGE_NOT_FOUND'
    successful(engine, second, receiver, 'task_evidence_edge_bind', {'source_project_id':first.project_id,
        'edge_id':edge['edge_id'],'edge_digest':edge['edge_digest']})
    incoming = successful(engine, second, receiver, 'task_evidence_receive', locator)
    assert incoming['state'] == 'admitted'
    before = snapshot(first)
    result = successful(engine, second, receiver, 'task_evidence_result', {'edge_id':edge['edge_id'],'reply_to':incoming['exchange_id'],
        'payload':{'summary':'Two verified observations','fields':{'count':2}}})
    assert result['state'] == 'sealed' and snapshot(first) == before
    assert len(successful(engine, first, sender, 'task_evidence_graph')['missing_returns']) == 1
    returned = successful(engine, first, sender, 'task_evidence_receive', {'source_project_id':second.project_id,
        'exchange_id':result['exchange_id'],'envelope_digest':result['envelope_digest']})
    assert returned['state'] == 'received'
    assert len(successful(engine, first, sender, 'task_evidence_graph')['missing_returns']) == 1
    peer_before = snapshot(second)
    decision = successful(engine, first, sender, 'task_evidence_decide', {'exchange_id':returned['exchange_id'],
        'envelope_digest':returned['envelope_digest'],'expected_version':1,'decision':'admit','reason':'Verified the exact typed result.'})
    assert decision['state'] == 'admitted' and snapshot(second) == peer_before
    graph = successful(engine, first, sender, 'task_evidence_graph')
    assert graph['missing_returns'] == []
    assert graph['edges'][0]['local_admitted_returns'][0]['exchange_id'] == returned['exchange_id']
    assert successful(engine, second, receiver, 'task_evidence_graph')['missing_returns'][0]['state'] == 'source_return_state_not_read'
    # The original source's outbound state remains a seal, not a copied peer decision.
    with first.lane('canon').connection(read_only=True) as db:
        assert CanonStore(first).exchange(db, sealed['exchange_id'])[0]['state'] == 'sealed'


def test_incompatible_foreign_input_preserves_reasons_and_requires_receiver_accept(projects):
    engine, first, second, sender, receiver, a, b = projects
    contract = successful(engine, second, receiver, 'task_evidence_expect', {'receiver_id':b,'contract_key':'typed',
        'sender_endpoints':[{'project_id':first.project_id,'participant_id':a}],'kinds':['requirements'],
        'fields':{'count':'integer'},'auto_admit':True})
    sealed, _ = packet(projects,expected_contract=contract['contract_digest'],payload={'summary':'An incompatible field','fields':{'count':'two'}})
    incoming, _ = received(projects, sealed)
    assert incoming['state'] == 'received'
    assert incoming['compatibility_reasons'] == ['CANON_PAYLOAD_TYPE_MISMATCH'] and incoming['receiver_decision_required']
    args = {'exchange_id':incoming['exchange_id'],'envelope_digest':incoming['envelope_digest'],
        'expected_version':1,'decision':'admit','reason':'The receiver explicitly accepts this exact incompatible input.'}
    assert call(engine, second, receiver, 'task_evidence_decide', args).error.code == 'CANON_INPUT_ACCEPT_REQUIRED'
    assert call(engine, second, sender, 'task_evidence_decide', {**args,'incompatible_input_decision':'ACCEPT'}).error.code == 'CANON_OWNER_MISMATCH'
    result = successful(engine, second, receiver, 'task_evidence_decide', {**args,'incompatible_input_decision':'ACCEPT'})
    assert result['compatibility_reasons'] == ['CANON_PAYLOAD_TYPE_MISMATCH']
    assert result['incompatible_input_decision'] == 'ACCEPT' and not result['plan_mutated'] and not result['source_write_granted']
    inbox = successful(engine, second, receiver, 'task_evidence_inbox', {'exchange_id':incoming['exchange_id']})
    assert inbox['packets'][0]['events'][-1]['result']['compatibility_reasons'] == ['CANON_PAYLOAD_TYPE_MISMATCH']
    assert inbox['packets'][0]['admission_at_receipt']['compatibility_reasons'] == ['CANON_PAYLOAD_TYPE_MISMATCH']


def test_foreign_preview_is_read_only_and_receiver_rechecks_a_replaced_contract(projects):
    engine, first, second, sender, receiver, a, b = projects
    arguments = {'receiver_id':b,'contract_key':'preview','sender_endpoints':[{'project_id':first.project_id,'participant_id':a}],
        'kinds':['requirements'],'auto_admit':True}
    contract = successful(engine,second,receiver,'task_evidence_expect',arguments)
    sealed,_ = packet(projects,expected_contract=contract['contract_digest'])
    locator = {'source_project_id':first.project_id,'exchange_id':sealed['exchange_id'],'envelope_digest':sealed['envelope_digest']}
    before = snapshot(first),snapshot(second)
    preview = successful(engine,second,receiver,'task_evidence_packet_classify',locator)
    assert preview['classification'] == 'expected' and preview['automatic_admission_permitted']
    assert not preview['admission_performed'] and not preview['project_mutated']
    assert (snapshot(first),snapshot(second)) == before
    successful(engine,second,receiver,'task_evidence_expect',{**arguments,'expected_version':1,'active':False})
    source_before = snapshot(first)
    incoming = successful(engine,second,receiver,'task_evidence_receive',locator)
    assert incoming['state'] == 'received' and incoming['compatibility_reasons'] == ['CANON_CONTRACT_MISMATCH']
    assert snapshot(first) == source_before
    assert successful(engine,first,sender,'task_evidence_read')['exchanges'][0]['state'] == 'sealed'


def test_expired_foreign_packet_is_previewed_as_ineligible_and_cannot_be_received(projects, monkeypatch):
    from evidence_lane_plugin import canon_task_graph as module

    engine, first, second, _, receiver, _, _ = projects
    expiry = datetime.now(UTC)+timedelta(seconds=30)
    sealed,_ = packet(projects,expires_at=expiry.isoformat())
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return expiry+timedelta(seconds=1)
    monkeypatch.setattr(module,'datetime',Later)
    locator = {'source_project_id':first.project_id,'exchange_id':sealed['exchange_id'],'envelope_digest':sealed['envelope_digest']}
    before = snapshot(first),snapshot(second)
    preview = successful(engine,second,receiver,'task_evidence_packet_classify',locator)
    assert preview['classification'] == 'undefined_or_incompatible' and preview['reasons'] == ['CANON_EXCHANGE_EXPIRED']
    assert (snapshot(first),snapshot(second)) == before
    assert call(engine,second,receiver,'task_evidence_receive',locator).error.code == 'CANON_EXCHANGE_EXPIRED'


def test_foreign_reference_is_verified_in_its_source_lane_and_rechecked_before_receive(projects):
    engine, first, second, _, receiver, _, _ = projects
    with engine.project_work.mutation(first) as lease, lease.transaction('local_code'):
        digest = first.lane('local_code').put_object(b'Source-owned registered evidence')
    # The destination has no corresponding object. A receive must resolve this
    # reference against the source lane without inventing a destination record.
    payload = {'summary':'A registered source reference','references':[{'kind':'source_object','key':digest,
        'digest':digest,'lane_id':'local_code','profile':'code'}]}
    sealed,_ = packet(projects,payload=payload)
    incoming,_ = received(projects,sealed)
    assert incoming['state'] == 'received'
    later,_ = packet(projects,payload=payload)
    locator = {'source_project_id':first.project_id,'exchange_id':later['exchange_id'],'envelope_digest':later['envelope_digest']}
    with engine.project_work.mutation(first) as lease, lease.transaction('local_code') as db:
        db.execute('DELETE FROM objects WHERE digest=?',(digest,))
    before = snapshot(first),snapshot(second)
    preview = successful(engine,second,receiver,'task_evidence_packet_classify',locator)
    assert preview['reasons'] == ['MEMORY_SOURCE_MISMATCH'] and (snapshot(first),snapshot(second)) == before
    assert call(engine,second,receiver,'task_evidence_receive',locator).error.code == 'MEMORY_SOURCE_MISMATCH'
    # Explicit incompatible-input acceptance cannot bypass missing source evidence.
    assert call(engine,second,receiver,'task_evidence_decide',{'exchange_id':incoming['exchange_id'],
        'envelope_digest':incoming['envelope_digest'],'expected_version':1,'decision':'admit','reason':'Attempted stale reference',
        'incompatible_input_decision':'ACCEPT'}).error.code == 'MEMORY_SOURCE_MISMATCH'


def test_hash_consistent_event_cannot_attest_a_different_source_envelope(projects):
    from evidence_lane_plugin.storage import json_text

    engine, first, second, _, receiver, _, _ = projects
    sealed,_ = packet(projects)
    with engine.project_work.mutation(first) as lease, lease.transaction('canon') as db:
        row = dict(db.execute("SELECT * FROM canon_events WHERE kind='send' ORDER BY sequence DESC LIMIT 1").fetchone())
        result = json.loads(row['result_json'])
        result['envelope_digest'] = '0'*64
        row['result_json'] = json_text(result)
        row.pop('digest')
        db.execute('UPDATE canon_events SET result_json=?,digest=? WHERE sequence=?',
            (row['result_json'],content_digest(row),row['sequence']))
    assert CanonStore(first).verify_history()['events_verified'] >= 2
    locator = {'source_project_id':first.project_id,'exchange_id':sealed['exchange_id'],'envelope_digest':sealed['envelope_digest']}
    assert call(engine,second,receiver,'task_evidence_packet_classify',locator).error.code == 'CANON_SOURCE_SEAL_INTEGRITY'
    assert call(engine,second,receiver,'task_evidence_receive',locator).error.code == 'CANON_SOURCE_SEAL_INTEGRITY'


def test_deciding_absent_input_reports_the_domain_error_without_initializing_canon(system):
    engine, project, _, client = system
    result = call(engine,project,client,'task_evidence_decide',{'exchange_id':str(uuid4()),'envelope_digest':'0'*64,
        'expected_version':1,'decision':'admit','reason':'No input exists'})
    assert result.error.code == 'CANON_EXCHANGE_NOT_FOUND'
    with project.lane('canon').connection(read_only=True) as db:
        assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='canon_exchanges'").fetchone()


def test_consequence_views_keep_foreign_endpoints_without_claiming_receiver_state(projects):
    from evidence_lane_plugin.canon_consequence_graph import canon_pointer, canon_view
    from evidence_lane_plugin.lane_contract import ViewScope

    _, first, second, _, _, a, b = projects
    sealed,_ = packet(projects)
    received(projects,sealed)
    for project, foreign_id, participant_id in ((first,second.project_id,b),(second,first.project_id,a)):
        before = snapshot(project)
        graph = canon_view(project,ViewScope())
        assert snapshot(project) == before
        foreign = next(node for node in graph['nodes'] if node['kind']=='canon_participant' and node['key']==foreign_id+':'+participant_id)
        assert foreign['locator']['ownership_evidence'] == 'endpoint_in_local_envelope'
        assert any(edge['kind']=='SENT' for edge in graph['edges'])
        pointers = canon_pointer({'project_id':project.project_id},graph,[])['exchanges'][sealed['exchange_id']]
        assert pointers['receiver_state'] == (None if project is first else 'received')
        assert pointers['local_state'] == ('sealed' if project is first else 'received')


def test_foreign_selection_expiry_digest_and_registered_file_are_enforced(projects):
    engine, first, second, _, receiver, _, _ = projects
    sealed, _ = packet(projects)
    locator = {'source_project_id':first.project_id,'exchange_id':sealed['exchange_id'],'envelope_digest':sealed['envelope_digest']}
    _, unselected = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=second.project_id,permissions=['read','write'])]))
    assert call(engine, second, unselected, 'task_evidence_receive', locator).error.code in {'PROJECT_NOT_SELECTED','PROJECT_PERMISSION_DENIED'}
    assert call(engine, second, receiver, 'task_evidence_receive', {**locator,'envelope_digest':'0'*64}).error.code == 'CANON_SOURCE_ENVELOPE_MISMATCH'
    path = first.root/sealed['artifact_path']
    path.write_bytes(path.read_bytes()+b' ')
    assert call(engine, second, receiver, 'task_evidence_receive', locator).error.code == 'CANON_PACKET_FILE_INTEGRITY'
    engine, first, second, sender, _, a, b = projects
    result = call(engine, first, sender, 'task_evidence_send', {'sender_id':a,'receiver_id':b,'destination_project_id':second.project_id,
        'kind':'requirements','payload':{'summary':'Expired'},'expires_at':(datetime.now(UTC)-timedelta(seconds=1)).isoformat()})
    assert result.error.code == 'CANON_EXCHANGE_EXPIRED'


def test_packet_files_are_registered_for_recovery_and_orphans_are_excluded(projects, monkeypatch):
    engine, first, second, sender, _, a, b = projects
    sealed, _ = packet(projects)
    incoming, _ = received(projects, sealed)
    original = CanonStore._event
    def fail(connection, request_id, kind, *args):
        if kind == 'send':
            raise RuntimeError('Injected failure after the durable packet file was written')
        return original(connection, request_id, kind, *args)
    monkeypatch.setattr(CanonStore, '_event', staticmethod(fail))
    request = {'request_id':str(uuid4()),'sender_id':a,'receiver_id':b,'destination_project_id':second.project_id,
        'kind':'evidence','payload':{'summary':'An interrupted seal'}}
    before = CanonStore(first).verify_history()
    assert call(engine, first, sender, 'task_evidence_send', request).status == 'error'
    assert CanonStore(first).verify_history() == before
    monkeypatch.setattr(CanonStore, '_event', staticmethod(original))
    retried = successful(engine, first, sender, 'task_evidence_send', request)
    for project, expected in ((first,{sealed['artifact_path'],retried['artifact_path']}),(second,{incoming['artifact_path']})):
        with project_snapshot(project.root):
            selected = inventory(project,require_quiescent=False)['files']
        assert {
            row['path']
            for row in selected
            if '/objects/outbox/' in row['path'] or '/objects/inbox/' in row['path']
        } == expected
        for row in selected:
            if row['path'] in expected:
                assert content_digest(json.loads((project.root/row['path']).read_bytes())) == row['sha256']
    assert len(list((first.lane('canon').files/'outbox').glob('*/*.json'))) == 3


def test_existing_local_history_migrates_without_rewriting_envelopes_or_events(system, monkeypatch):
    from evidence_lane_plugin import canon_task_graph as module
    from evidence_lane_plugin.canon_task_graph import CanonJoin, CanonSend
    from evidence_lane_plugin.storage import json_text, now

    engine, project, _, client = system
    current = module.CANON_MIGRATIONS
    canon = CanonStore(project)
    with monkeypatch.context() as old:
        old.setattr(module, 'CANON_MIGRATIONS', current[:3])
        with engine.project_work.mutation(project) as lease:
            a = canon.join(CanonJoin(label='Earlier sender'), lease, actor_id=client.client_id).participant_id
            b = canon.join(CanonJoin(label='Earlier receiver'), lease, actor_id=client.client_id).participant_id
            # Three persisted exchanges exercise the old self-reference and
            # external supersession FK while the table is rebuilt in migration 4.
            identities = [str(uuid4()) for _ in range(3)]
            envelopes = []
            with lease.transaction('canon') as db:
                for index, identity in enumerate(identities):
                    message = CanonSend(sender_id=b if index == 1 else a,receiver_id=a if index == 1 else b,
                        kind='result' if index == 1 else ('correction' if index == 2 else 'requirements'),
                        reply_to=identities[0] if index == 1 else None,supersedes=identities[0] if index == 2 else None,
                        payload={'summary':'Historical immutable packet'})
                    state = 'superseded' if index == 0 else 'admitted'
                    version = 2 if index == 0 else 1
                    envelope = {'project_id':project.project_id,'exchange_id':identity,'source_client_id':client.client_id,
                        'message':message.model_dump(exclude={'request_id','destination_project_id'}),
                        'return_depth':int(index == 1),'created_at':now()}
                    digest = content_digest(envelope)
                    envelopes.append(envelope)
                    db.execute('INSERT INTO canon_exchanges VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (index+1,identity,message.sender_id,message.receiver_id,message.kind,digest,json_text(envelope),state,version,
                            canon._state_digest(identity,digest,state,version),message.reply_to,envelope['created_at']))
                    canon._event(db,str(uuid4()),'send',client.client_id,content_digest(message.model_dump()),
                        {'exchange_id':identity,'envelope_digest':digest,'state':state})
                event_id = str(uuid4())
                link = module.CanonSuperseded(exchange_id=identities[0],envelope_digest=content_digest(envelopes[0]),
                    successor_id=identities[2],successor_envelope_digest=content_digest(envelopes[2]),receiver_id=b,version=2,
                    decision_basis='receiver_explicit').model_dump()
                canon._event(db,event_id,'supersede',client.client_id,content_digest(link),link)
                db.execute('INSERT INTO canon_supersessions VALUES(?,?)',(identities[0],event_id))
                events_before = [tuple(r) for r in db.execute('SELECT * FROM canon_events ORDER BY sequence')]
                packets_before = [tuple(r) for r in db.execute('SELECT * FROM canon_exchanges ORDER BY sequence')]
    with engine.project_work.mutation(project) as lease:
        canon.initialize(lease)
    with project.lane('canon').connection(read_only=True) as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM canon_events ORDER BY sequence')] == events_before
        migrated = db.execute('SELECT * FROM canon_exchanges ORDER BY sequence').fetchall()
        assert [tuple(row)[:12] for row in migrated] == packets_before
        assert [row['local_role'] for row in migrated] == ['both']*3
        assert db.execute('SELECT * FROM canon_supersessions').fetchone()[1] == event_id
        assert list(db.execute('PRAGMA foreign_key_check')) == []
        assert [row[0] for row in db.execute("SELECT version FROM schema_migrations WHERE owner='canon' ORDER BY version")] == [1,2,3,4,5]
        for identity, envelope in zip(identities, envelopes, strict=True):
            assert canon.exchange(db,identity)[1] == envelope
        assert canon._supersession(db,migrated[0]).successor_id == identities[2]


def test_packet_and_graph_files_survive_actual_backup_and_fresh_root_restore(projects, tmp_path):
    from evidence_lane_plugin.database_recovery import (
        BackupRequest,
        DatabaseRecovery,
        restore_backup_offline,
        verify_backup,
    )
    from evidence_lane_plugin.storage import ProjectStore

    engine, first, second, sender, receiver, a, b = projects
    edge = successful(engine,first,sender,'task_evidence_edge_register',{'source_id':a,
        'destination':{'project_id':second.project_id,'participant_id':b},'contract_digest':'a'*64,
        'schema_digest':content_digest(CanonPayload.model_json_schema()),'expected_return_contract':'b'*64})
    successful(engine,second,receiver,'task_evidence_edge_bind',{'source_project_id':first.project_id,
        'edge_id':edge['edge_id'],'edge_digest':edge['edge_digest']})
    sealed, _ = packet(projects)
    incoming, _ = received(projects, sealed)
    backups = []
    for project, client in ((first,sender),(second,receiver)):
        with engine.project_work.mutation(project) as lease:
            result = DatabaseRecovery(engine,project).backup(BackupRequest(destination_root=str(tmp_path/('backup-'+project.project_id))),
                lease,actor_id=client.client_id)
        manifest = verify_backup(result.backup_root,result.manifest_digest,project.project_id)
        backups.append((project,result,manifest))
    assert engine.stop()
    for project, result, manifest in backups:
        destination = tmp_path/('restored-'+project.project_id)
        restored = restore_backup_offline(engine.root,project.project_id,result.backup_root,result.manifest_digest,destination)
        assert restored['backup_root_pv'] == result.root_pv
        recovered = ProjectStore(destination,read_only=True)
        relative = sealed['artifact_path'] if project.project_id == first.project_id else incoming['artifact_path']
        assert (recovered.root/relative).read_bytes() == (project.root/relative).read_bytes()
        with recovered.lane('canon').connection(read_only=True) as db:
            row,_ = CanonStore(recovered).exchange(db,sealed['exchange_id'])
            assert row['local_role'] == ('outbox' if project.project_id == first.project_id else 'inbox')
        assert any(item['path'] == relative for item in manifest['files'])
        assert (recovered.root/edge['artifact_path']).read_bytes() == (project.root/edge['artifact_path']).read_bytes()
        assert any(item['path'] == edge['artifact_path'] for item in manifest['files'])


def test_two_mcp_clients_exchange_typed_edge_results_through_persistent_backend(projects):
    import asyncio
    import sys
    from pathlib import Path

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    engine, first, second, _, _, _, _ = projects
    plugin = Path(__file__).resolve().parents[1]/'plugins/evidence-lane-plugin'
    parameters = StdioServerParameters(command=sys.executable,args=['-m','evidence_lane_plugin.mcp_adapter',
        '--runtime-root',str(engine.root),'--project-id',first.project_id,'--project-id',second.project_id,
        '--permission','read','--permission','write'],env={'PYTHONPATH':str(plugin/'src')})

    async def exercise():
        async with (stdio_client(parameters) as (source_read,source_write),
                    ClientSession(source_read,source_write,read_timeout_seconds=timedelta(seconds=30)) as source,
                    stdio_client(parameters) as (target_read,target_write),
                    ClientSession(target_read,target_write,read_timeout_seconds=timedelta(seconds=30)) as target):
            await source.initialize()
            await target.initialize()
            async def invoke(client,project,action,arguments=None):
                result = (await client.call_tool(action,{'project_id':project.project_id,'arguments':arguments or {}})).structuredContent
                assert result and result['status'] == 'ok',json.dumps(result)
                assert result['tool_execution']['env_uop']['action_name'] == action
                assert result['tool_execution']['native_host_tool_attested'] is False
                return result['result']
            a = (await invoke(source,first,'task_evidence_participant_register',{'label':'MCP source'}))['participant_id']
            b = (await invoke(target,second,'task_evidence_participant_register',{'label':'MCP receiver'}))['participant_id']
            expected = await invoke(target,second,'task_evidence_expect',{'receiver_id':b,'contract_key':'mcp_input',
                'sender_endpoints':[{'project_id':first.project_id,'participant_id':a}],'kinds':['requirements'],'auto_admit':True})
            returning = await invoke(source,first,'task_evidence_expect',{'receiver_id':a,'contract_key':'mcp_return',
                'sender_endpoints':[{'project_id':second.project_id,'participant_id':b}],'kinds':['result'],
                'fields':{'count':'integer'},'auto_admit':True})
            edge = await invoke(source,first,'task_evidence_edge_register',{'source_id':a,
                'destination':{'project_id':second.project_id,'participant_id':b},'contract_digest':expected['contract_digest'],
                'expected_return_contract':returning['contract_digest'],'schema_digest':content_digest(CanonPayload.model_json_schema())})
            await invoke(target,second,'task_evidence_edge_bind',{'source_project_id':first.project_id,
                'edge_id':edge['edge_id'],'edge_digest':edge['edge_digest']})
            sealed = await invoke(source,first,'task_evidence_send',{'sender_id':a,'receiver_id':b,'destination_project_id':second.project_id,
                'kind':'requirements','payload':{'summary':'Inspect the exact input'},'edge_id':edge['edge_id'],
                'expected_contract':expected['contract_digest'],'return_contract':returning['contract_digest']})
            locator = {'source_project_id':first.project_id,'exchange_id':sealed['exchange_id'],'envelope_digest':sealed['envelope_digest']}
            before = snapshot(first),snapshot(second)
            preview = await invoke(target,second,'task_evidence_packet_classify',locator)
            assert preview['automatic_admission_permitted'] and (snapshot(first),snapshot(second)) == before
            incoming = await invoke(target,second,'task_evidence_receive',locator)
            assert incoming['state'] == 'admitted'
            result = await invoke(target,second,'task_evidence_result',{'edge_id':edge['edge_id'],'reply_to':incoming['exchange_id'],
                'payload':{'summary':'One observation','fields':{'count':1}}})
            assert result['state'] == 'sealed'
            returned = await invoke(source,first,'task_evidence_receive',{'source_project_id':second.project_id,
                'exchange_id':result['exchange_id'],'envelope_digest':result['envelope_digest']})
            assert returned['state'] == 'admitted'
            graph = await invoke(source,first,'task_evidence_graph')
            assert graph['missing_returns'] == [] and graph['native_task_attestation'] == 'not_provided'
            conditional = await invoke(source,first,'task_evidence_expect',{'receiver_id':a,'contract_key':'mcp_backfire',
                'sender_endpoints':[{'project_id':second.project_id,'participant_id':b}],'kinds':['backfire'],'auto_admit':True})
            correction_return = await invoke(target,second,'task_evidence_expect',{'receiver_id':b,'contract_key':'mcp_backfire_return',
                'sender_endpoints':[{'project_id':first.project_id,'participant_id':a}],'kinds':['result'],'auto_admit':True})
            arguments = {'admitted_exchange_id':incoming['exchange_id'],'admitted_envelope_digest':incoming['envelope_digest'],
                'failure_class':'LINKED_TASK_INPUT_REQUIRED','recipient':{'project_id':first.project_id,'participant_id':a},
                'requested_contract':conditional['contract_digest'],'requested_revision':2,'payload':{'summary':'An upstream input requires action'},
                'dependency_ids':[edge['edge_id']],'return_route':{'project_id':second.project_id,'participant_id':b},
                'return_contract':correction_return['contract_digest'],'expires_at':(datetime.now(UTC)+timedelta(hours=1)).isoformat()}
            proposed = await invoke(target,second,'task_evidence_input_request',arguments)
            replay = await invoke(target,second,'task_evidence_input_request',arguments)
            assert replay['duplicate'] and replay['envelope_digest'] == proposed['envelope_digest'] and not replay['automatic_retry_allowed']
            admitted_backfire = await invoke(source,first,'task_evidence_receive',{'source_project_id':second.project_id,
                'exchange_id':proposed['exchange_id'],'envelope_digest':proposed['envelope_digest']})
            assert admitted_backfire['state'] == 'admitted'
            correction_result = await invoke(source,first,'task_evidence_send',{'sender_id':a,'receiver_id':b,'destination_project_id':second.project_id,
                'kind':'result','reply_to':proposed['exchange_id'],'expected_contract':correction_return['contract_digest'],
                'payload':{'summary':'The required linked-task input is supplied'}})
            corrected = await invoke(target,second,'task_evidence_receive',{'source_project_id':first.project_id,
                'exchange_id':correction_result['exchange_id'],'envelope_digest':correction_result['envelope_digest']})
            assert corrected['state'] == 'admitted'
    with LocalEndpoint(engine,studio_enabled=False):
        asyncio.run(exercise())

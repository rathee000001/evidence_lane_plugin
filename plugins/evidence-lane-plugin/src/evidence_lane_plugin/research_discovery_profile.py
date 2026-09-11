"""Research-owned search history with exact provider bundles and shared readers."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from .document_parsers import digest
from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations, read_compatibility
from .plan_runtime import content_digest
from .registry import ActionSpec
from .research_discovery_contracts import Discover, source_parameters
from .research_web_content import safe_url
from .research_web_profile import network_check
from .sector_evidence_profile import EvidenceResult, chunks, lane_if_present, object_bytes, result
from .sector_evidence_schema import TABLES, migrations
from .sector_evidence_workers import read_source
from .storage import json_text, now
from .tabular_profile import natural_file
from .tool_routes import ToolRoute

DISCOVERY_TABLES = dict.fromkeys(TABLES['research'], 'research_discovery_fact')


def parser_contract():
    names = ('research_discovery.py', 'research_discovery_transport.py', 'research_discovery_workers.py',
        'research_discovery_contracts.py', 'research_web_content.py', 'research_web_fetch.py', 'sector_evidence_parsers.py')
    return digest(canonical_json_bytes({name: digest(Path(__file__).with_name(name).read_bytes()) for name in names}))


def source_identity(store, parameters):
    return digest(canonical_json_bytes([store.project_id, 'research', 'discovery', source_parameters(parameters)]))


def present(store, snapshot_id=None):
    lane = lane_if_present(store, 'research')
    if lane is None:
        return False
    read_compatibility(lane, migrations('research'))
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='research_discovery_version'").fetchone():
            return False
        return snapshot_id is None or connection.execute(
            'SELECT 1 FROM research_discovery_version WHERE snapshot_id=?', (snapshot_id,)).fetchone() is not None


def current_id(store, source_id):
    if not present(store):
        return None
    with store.lane('research').connection(read_only=True) as connection:
        row = connection.execute('SELECT snapshot_id FROM research_discovery_current WHERE source_id=?', (source_id,)).fetchone()
    return row[0] if row else None


def read_snapshot(store, snapshot_id):
    if not present(store, snapshot_id):
        raise LaneError('RESEARCH_DISCOVERY_SNAPSHOT_MISSING', 'Select an exact Research discovery snapshot.')
    lane = store.lane('research')
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT v.*,s.parameters_json FROM research_discovery_version v '
            'JOIN research_discovery_source s USING(source_id) WHERE v.snapshot_id=?', (snapshot_id,)).fetchone()
    manifest = json.loads(object_bytes(lane, row['manifest_object'], 2_097_152))
    raw = object_bytes(lane, row['raw_object'], 8_388_608)
    value = json.loads(raw)
    facts = json.loads(object_bytes(lane, row['facts_object'], 8_388_608))
    valid = (digest(canonical_json_bytes(manifest)) == snapshot_id and manifest['project_id'] == store.project_id
        and manifest['lane_id'] == 'research' and manifest['source_id'] == source_identity(store, value)
        and manifest['logical_name'] == 'discovery/' + manifest['source_id'] + '.json'
        and manifest['parameters'] == json.loads(row['parameters_json']) == source_parameters(value)
        and all(manifest[key] == row[key] for key in ('source_id', 'generation', 'previous_snapshot',
            'raw_object', 'facts_object', 'parser_contract', 'created_at'))
        and facts['lane_id'] == 'research' and facts['source_sha256'] == digest(raw)
        and facts['parser'] == manifest['parser'] == 'ddgs_bounded_provider_search'
        and 0 <= value['result_count'] == len(value['results']) <= value['limit'] <= 50
        and value['automatic_target_ingestion'] is False and value['automatic_retry'] is False)
    identities = set()
    for item in facts['items']:
        valid &= (item['kind'] in DISCOVERY_TABLES and item['item_id'] not in identities
            and item['item_id'] == digest(canonical_json_bytes(['research', item['kind'], item['part'], item['ordinal']])))
        identities.add(item['item_id'])
    for item in value['results']:
        valid &= (safe_url(item['url']) == item['url'] and set(item['providers']) <= set(value['backends'])
            and item['target_status'] == 'unvisited_search_result' and item['source_assertions_validated'] is False)
    observed_wire = observed_body = 0
    for event in value['capture']['events']:
        if event['response_body_stored']:
            wire = base64.b64decode(event['wire_base64'], validate=True)
            body = base64.b64decode(event['body_base64'], validate=True)
            valid &= (digest(wire) == event['wire_sha256'] and len(wire) == event['wire_bytes']
                and digest(body) == event['body_sha256'] and len(body) == event['body_bytes'])
            observed_wire += len(wire)
            observed_body += len(body)
    valid &= (observed_wire <= value['capture']['wire_bytes'] <= value['max_bytes']
        and observed_body <= value['capture']['body_bytes'] <= value['max_bytes']
        and 1 <= value['capture']['requests'] <= value['max_requests'])
    if not valid or len(identities) > 8192:
        raise LaneError('RESEARCH_DISCOVERY_SNAPSHOT_INTEGRITY', 'The provider bundle differs from its source and recorded bounds.')
    return manifest, facts, value, raw


def receipt_body(manifest, snapshot_id):
    return {key: manifest[key] for key in ('project_id', 'lane_id', 'source_id', 'previous_snapshot',
        'operation', 'job_id', 'task_id', 'plan_revision', 'worker_digest')} | {'snapshot_id': snapshot_id,
        'target_documents_ingested': False, 'source_assertions_validated': False}


def output_body(manifest, value, lane, snapshot_id, receipt_id):
    return {'snapshot_id': snapshot_id, 'source_id': manifest['source_id'], 'generation': manifest['generation'],
        'previous_snapshot': manifest['previous_snapshot'], 'logical_name': manifest['logical_name'],
        'query': value['query'], 'backends': value['backends'], 'status': value['status'], 'attempts': value['attempts'],
        'result_count': value['result_count'], 'results_truncated': value['results_truncated'],
        'sha256': manifest['raw_object'], 'bytes': manifest['bytes'], 'receipt_id': receipt_id,
        'natural_path': str(lane.files / 'natural' / snapshot_id / 'discovery.json'),
        'target_documents_ingested': False, 'source_assertions_validated': False,
        'remote_currentness': value['remote_currentness']}


def discover(context, request):
    execution, store = context.execution, context.execution.store
    network_check(context)
    execution.guard.extension_checks.append(lambda: network_check(context))
    parameters = request.model_dump(mode='json')
    source_id = source_identity(store, parameters)
    if current_id(store, source_id) != request.expected_snapshot:
        raise LaneError('RESEARCH_DISCOVERY_SNAPSHOT_CHANGED', 'Bind discovery to the exact current query snapshot.')
    contract = parser_contract()
    name = 'discovery/' + source_id + '.json'
    query_arguments = {key: value for key, value in parameters.items() if key not in {'lane_id', 'expected_snapshot'}}
    response = execution.submit('research_discover_sources', {'query_arguments': query_arguments, 'logical_name': name}).result()
    if response['status'] != 'ok':
        raise LaneError('RESEARCH_DISCOVERY_WORKER_FAILED', 'The selected bounded provider search did not complete.')
    value, facts = response['result']['discovery'], response['result']['facts']
    raw = canonical_json_bytes(value)
    if (parser_contract() != contract or len(raw) > 8_388_608 or facts['source_sha256'] != digest(raw)
            or any(value[key] != selected for key, selected in query_arguments.items())
            or any(item['kind'] not in DISCOVERY_TABLES for item in facts['items'])):
        raise LaneError('RESEARCH_DISCOVERY_WORKER_BINDING', 'The provider evidence differs from its admitted query.')
    prior = read_snapshot(store, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior['source_id'] != source_id:
        raise LaneError('RESEARCH_DISCOVERY_SOURCE_CHANGED', 'The prior snapshot belongs to another provider query.')
    stamp = now()
    manifest = {'schema': 'evidence-lane.research-discovery-snapshot.v4', 'project_id': store.project_id,
        'lane_id': 'research', 'source_id': source_id, 'parameters': source_parameters(parameters), 'logical_name': name,
        'generation': prior['generation'] + 1 if prior else 1, 'previous_snapshot': request.expected_snapshot,
        'created_at': stamp, 'raw_object': digest(raw), 'bytes': len(raw), 'facts_object': digest(canonical_json_bytes(facts)),
        'parser': 'ddgs_bounded_provider_search', 'parser_contract': contract, 'operation': execution.guard.spec.name,
        'arguments': parameters, 'worker_envelope': {key: value for key, value in response.items() if key != 'result'},
        'tool_admission': context.tool_admission,
        'worker_digest': content_digest(response), 'job_id': execution.claim.job_id,
        'task_id': execution.task_id, 'plan_revision': context.expected_revision}
    snapshot_id = digest(canonical_json_bytes(manifest))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(['research', 'receipts']):
        lane = store.lane('research')
        apply_migrations(lane, migrations('research'), writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute('SELECT snapshot_id FROM research_discovery_current WHERE source_id=?', (source_id,)).fetchone()
            if (live[0] if live else None) != request.expected_snapshot:
                raise LaneError('RESEARCH_DISCOVERY_SNAPSHOT_CHANGED', 'The current query changed before publication.')
            execution.guard.check()
            for content in (raw, canonical_json_bytes(facts), canonical_json_bytes(manifest)):
                lane.put_object(content)
            connection.execute('INSERT OR IGNORE INTO research_discovery_source VALUES(?,?,?)',
                (source_id, json_text(manifest['parameters']), stamp))
            connection.execute('INSERT INTO research_discovery_version VALUES(?,?,?,?,?,?,?,?,?)',
                (snapshot_id, source_id, manifest['generation'], request.expected_snapshot, snapshot_id,
                    manifest['raw_object'], manifest['facts_object'], contract, stamp))
            for item in facts['items']:
                connection.execute('INSERT INTO research_discovery_fact VALUES(?,?,?,?,?,?)',
                    (snapshot_id, item['item_id'], item['kind'], item['ordinal'], item['part'], json_text(item)))
            for item_id, ordinal, text in chunks(facts):
                chunk_id = digest(canonical_json_bytes([snapshot_id, item_id, ordinal]))
                connection.execute('INSERT INTO research_discovery_chunk VALUES(?,?,?,?,?)',
                    (snapshot_id, chunk_id, item_id, ordinal, lane.put_object(text.encode())))
                connection.execute('INSERT INTO research_discovery_chunk_fts VALUES(?,?,?)', (snapshot_id, chunk_id, text))
            natural_file(lane, snapshot_id, 'discovery.json', raw)
            connection.execute('INSERT INTO research_discovery_current VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',
                (source_id, snapshot_id))
            receipt_id = store.append_receipt('research_discovery_snapshot', receipt_body(manifest, snapshot_id))
    return result(store, 'research', manifest['operation'], output_body(manifest, value, lane, snapshot_id, receipt_id))


def verify(context, request, output):
    store, snapshot_id = context.store, output.result['snapshot_id']
    manifest, facts, value, raw = read_snapshot(store, snapshot_id)
    lane = store.lane('research')
    response = {**manifest['worker_envelope'], 'result': {'discovery': value, 'facts': facts}}
    valid = (output.project_id == store.project_id and output.lane_id == 'research'
        and manifest['parser_contract'] == parser_contract() and current_id(store, manifest['source_id']) == snapshot_id
        and manifest['arguments'] == request.model_dump(mode='json') and manifest['bytes'] == len(raw)
        and output.operation == manifest['operation'] and manifest['job_id'] == context.job_id
        and manifest['task_id'] == context.task_id and manifest['plan_revision'] == context.plan_revision
        and output.result == output_body(manifest, value, lane, snapshot_id, output.result['receipt_id'])
        and content_digest(response) == manifest['worker_digest']
        and list(context.worker_evidence) == [{'digest': manifest['worker_digest'], 'status': 'ok',
            'worker_pid': manifest['worker_envelope']['worker_pid']}]
        and read_source(lane.files / 'natural' / snapshot_id / 'discovery.json', 8_388_608) == raw)
    with store.lane('plan').connection(read_only=True) as connection:
        entry_row = connection.execute('SELECT entry_object FROM delta_runs WHERE job_id=?', (context.job_id,)).fetchone()
    if entry_row is None:
        valid = False
    else:
        admitted = json.loads(object_bytes(store.lane('plan'), entry_row[0], 2_097_152))
        entry = admitted['entry']
        valid &= (entry['action'] == output.operation and entry['task_id'] == context.task_id
            and entry['plan_revision'] == context.plan_revision and entry['source_route'] is None
            and admitted['tool_admission'] == manifest['tool_admission']
            and manifest['tool_admission']['route_id'] == 'research_web_discover.ddgs'
            and type(request).model_validate(entry['arguments']) == request)
    with lane.connection(read_only=True) as connection:
        rows = connection.execute('SELECT * FROM research_discovery_fact WHERE snapshot_id=? ORDER BY item_id', (snapshot_id,)).fetchall()
        expected = sorted(facts['items'], key=lambda item: item['item_id'])
        valid &= ([json.loads(row['payload_json']) for row in rows] == expected and all(
            (row['item_id'], row['kind'], row['ordinal'], row['part']) ==
            (item['item_id'], item['kind'], item['ordinal'], item['part']) for row, item in zip(rows, expected)))
        rows = connection.execute('SELECT c.*,f.text_content FROM research_discovery_chunk c JOIN research_discovery_chunk_fts f '
            'ON c.snapshot_id=f.snapshot_id AND c.chunk_id=f.chunk_id WHERE c.snapshot_id=?', (snapshot_id,)).fetchall()
        expected_chunks = {(item, ordinal): text for item, ordinal, text in chunks(facts)}
        valid &= len(rows) == len(expected_chunks)
        for row in rows:
            text = expected_chunks.get((row['item_id'], row['ordinal']))
            valid &= (text is not None and text == row['text_content']
                and object_bytes(lane, row['text_object'], 16_384).decode() == text
                and row['chunk_id'] == digest(canonical_json_bytes([snapshot_id, row['item_id'], row['ordinal']])))
    with store.lane('receipts').connection(read_only=True) as connection:
        receipt = connection.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='research_discovery_snapshot'",
            (output.result['receipt_id'],)).fetchone()
    valid &= receipt is not None and json.loads(receipt[0]) == receipt_body(manifest, snapshot_id)
    return [{'check_id': name, 'passed': bool(valid), 'evidence': {'snapshot_id': snapshot_id,
        'remote_currentness': 'provider_responses_observed_once_not_rechecked', 'target_documents_ingested': False}}
        for name in context.requested_checks]


def current(store):
    if not present(store):
        return []
    with store.lane('research').connection(read_only=True) as connection:
        rows = connection.execute("SELECT v.*,'discovery/'||v.source_id||'.json' AS logical_name,s.parameters_json "
            'FROM research_discovery_current c JOIN research_discovery_version v USING(snapshot_id) '
            'JOIN research_discovery_source s ON s.source_id=c.source_id ORDER BY v.created_at DESC,v.snapshot_id LIMIT 129').fetchall()
    return [dict(row) | {'origin': 'provider_discovery'} for row in rows]


def query(store, request):
    from .sector_evidence_profile import query_facts
    manifest, facts, _, _ = read_snapshot(store, request.snapshot_id)
    return query_facts(store, request, manifest, facts, 'research_discovery', DISCOVERY_TABLES)


def read(store, request):
    manifest, _, _, raw = read_snapshot(store, request.snapshot_id)
    content = raw[request.offset:request.offset + request.max_bytes]
    end = request.offset + len(content)
    return {'snapshot_id': request.snapshot_id, 'logical_name': manifest['logical_name'],
        'sha256': manifest['raw_object'], 'representation': request.representation, 'total_bytes': len(raw),
        'offset': request.offset, 'content_base64': base64.b64encode(content).decode(),
        'next_offset': end if end < len(raw) else None, 'network_used': False,
        'fidelity': 'provider_query_response_bundle_not_target_documents'}


def register_discovery_actions(engine):
    engine.registry.register(ActionSpec('research_web_discover',
        'Discover bounded attributed sources through selected DDGS providers and preserve their response bundle in Research.',
        Discover, EvidenceResult, discover, permission='write', mutates=True, requires_delta=True,
        profile='research', workflow='manage-project-sources', worker_operations=('research_discover_sources',),
        verification_checks=('research_discovery_snapshot_integrity',), verifier=verify,
        tool_routes=(ToolRoute('research_web_discover.ddgs', discover, ('Python', 'DDGS', 'HTTPX', 'lxml'),
            reason='Pinned DDGS provider parsing and ranking over bounded HTTPX requests; targets remain unvisited.'),)))

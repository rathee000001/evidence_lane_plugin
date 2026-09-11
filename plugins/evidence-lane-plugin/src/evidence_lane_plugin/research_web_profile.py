"""Research-owned HTTP snapshots with coordinated publication and offline reads."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from .document_parsers import digest
from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations, read_compatibility
from .plan_runtime import content_digest
from .projects import ProjectAccess
from .registry import ActionSpec
from .research_web_content import DISTRIBUTIONS, EXTRACTORS, safe_url
from .research_web_contracts import Capture, Extract
from .sector_evidence_profile import EvidenceResult, chunks, lane_if_present, object_bytes, result
from .sector_evidence_schema import TABLES, migrations
from .storage import json_text, now
from .tabular_profile import natural_file
from .tool_routes import ToolRoute

WEB_TABLES = dict.fromkeys(TABLES['research'], 'research_web_fact')


def destination_evidence_valid(captured):
    checks = captured.get('destination_checks')
    expected_urls = [row.get('url') for row in captured.get('redirects', [])] + [
        captured.get('final_url')
    ]
    return (captured.get('destination_policy') ==
            'public_addresses_only_with_single_resolution_per_hop'
        and isinstance(checks, list) and len(checks) == len(expected_urls)
        and [row.get('url') for row in checks] == expected_urls
        and all(isinstance(row.get('address_count'), int)
            and 1 <= row['address_count'] <= 256
            and re.fullmatch(r'[0-9a-f]{64}', str(row.get('addresses_sha256'))) is not None
            and row.get('dns_rebinding_prevented') is True for row in checks))


def parser_contract():
    names = ('research_web_content.py', 'research_web_fetch.py', 'research_web_workers.py',
        'research_web_contracts.py', 'sector_evidence_parsers.py')
    return digest(canonical_json_bytes({name: digest(Path(__file__).with_name(name).read_bytes()) for name in names}))


def present(store, snapshot_id=None):
    lane = lane_if_present(store, 'research')
    if lane is None:
        return False
    read_compatibility(lane, migrations('research'))
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='research_web_version'").fetchone():
            return False
        return snapshot_id is None or connection.execute(
            'SELECT 1 FROM research_web_version WHERE snapshot_id=?', (snapshot_id,)).fetchone() is not None


def current_id(store, source_id):
    if not present(store):
        return None
    with store.lane('research').connection(read_only=True) as connection:
        row = connection.execute('SELECT snapshot_id FROM research_web_current WHERE source_id=?', (source_id,)).fetchone()
    return row[0] if row else None


def read_snapshot(store, snapshot_id):
    if not present(store, snapshot_id):
        raise LaneError('RESEARCH_WEB_SNAPSHOT_MISSING', 'Select an exact Research web snapshot.')
    lane = store.lane('research')
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT v.*,s.url FROM research_web_version v JOIN research_web_source s USING(source_id) '
            'WHERE v.snapshot_id=?', (snapshot_id,)).fetchone()
    manifest = json.loads(object_bytes(lane, row['manifest_object'], 2_097_152))
    valid = (digest(canonical_json_bytes(manifest)) == snapshot_id and manifest['project_id'] == store.project_id
        and manifest['lane_id'] == 'research' and manifest['url'] == row['url'] and all(
            manifest[key] == row[key] for key in ('source_id', 'generation', 'previous_snapshot',
                'body_object', 'wire_object', 'facts_object', 'extraction_object', 'parser_contract', 'created_at')))
    body, wire = (object_bytes(lane, manifest[key], 8_388_608) for key in ('body_object', 'wire_object'))
    facts = json.loads(object_bytes(lane, manifest['facts_object'], 8_388_608))
    extracted = json.loads(object_bytes(lane, manifest['extraction_object'], 8_388_608))
    captured = manifest['capture']
    valid &= (safe_url(manifest['url']) == manifest['url'] and captured['requested_url'] == manifest['url']
        and manifest['source_id'] == digest(canonical_json_bytes([store.project_id, 'research', 'web', manifest['url']]))
        and manifest['logical_name'] == 'web/' + manifest['source_id'] + '/response.body'
        and captured['body_sha256'] == digest(body) == manifest['raw_object'] == extracted['source_sha256']
        and captured['wire_sha256'] == digest(wire) and captured['body_bytes'] == len(body) and captured['wire_bytes'] == len(wire)
        and destination_evidence_valid(captured)
        and facts['lane_id'] == 'research' and facts['source_sha256'] == digest(body)
        and facts['parser'] == extracted['extractor'] == manifest['parser']
        and extracted['source_url'] == captured['final_url'] and extracted['text_sha256'] == digest(extracted['text'].encode()))
    identities = set()
    for item in facts['items']:
        valid &= (item['kind'] in WEB_TABLES and item['item_id'] not in identities
            and item['item_id'] == digest(canonical_json_bytes(['research', item['kind'], item['part'], item['ordinal']])))
        identities.add(item['item_id'])
    if not valid or len(identities) > 8192:
        raise LaneError('RESEARCH_WEB_SNAPSHOT_INTEGRITY', 'The web snapshot differs from its exact owning source.')
    return manifest, facts, extracted, body, wire


def network_check(context):
    if context.authorize is None:
        raise LaneError('LIVE_AUTHORIZATION_REQUIRED', 'Web capture requires a live network grant.')
    context.authorize('network')
    ProjectAccess(context.execution.store).authorize(context.client_id, 'network')


def capture_httpx(context, request):
    return capture_source(context, request, 'httpx')


def capture_requests(context, request):
    return capture_source(context, request, 'requests')


def capture_source(context, request, transport):
    execution, store = context.execution, context.execution.store
    network_check(context)
    execution.guard.extension_checks.append(lambda: network_check(context))
    source_id = digest(canonical_json_bytes([store.project_id, 'research', 'web', request.url]))
    if current_id(store, source_id) != request.expected_snapshot:
        raise LaneError('RESEARCH_WEB_SNAPSHOT_CHANGED', 'Bind capture to the exact current URL snapshot.')
    contract = parser_contract()
    response = execution.submit('research_web_capture_source', {**request.model_dump(mode='json'),
        'selected_transport': transport}).result()
    if response['status'] != 'ok':
        raise LaneError('RESEARCH_WEB_WORKER_FAILED', 'The selected web transport or parser did not complete.')
    parsed = response['result']
    captured = parsed['capture']
    if (captured['requested_url'] != request.url or captured['transport'] != transport
            or not destination_evidence_valid(captured)):
        raise LaneError('RESEARCH_WEB_WORKER_BINDING', 'The returned response differs from its selected route.')
    body, wire = (base64.b64decode(captured[key], validate=True) for key in ('body_base64', 'wire_base64'))
    return publish(context, request, response, captured, parsed['extraction'], parsed['facts'], body, wire,
        parsed['media'], contract, None)


def extract_trafilatura(context, request):
    return extract_source(context, request, 'trafilatura')


def extract_readability(context, request):
    return extract_source(context, request, 'readability')


def extract_beautifulsoup(context, request):
    return extract_source(context, request, 'beautifulsoup')


def extract_markdownify(context, request):
    return extract_source(context, request, 'markdownify')


def extract_html2text(context, request):
    return extract_source(context, request, 'html2text')


def extract_stdlib(context, request):
    return extract_source(context, request, 'stdlib')


def extract_source(context, request, selected_extractor):
    execution, store = context.execution, context.execution.store
    origin, _, _, body, wire = read_snapshot(store, request.snapshot_id)
    if current_id(store, origin['source_id']) != request.expected_snapshot:
        raise LaneError('RESEARCH_WEB_SNAPSHOT_CHANGED', 'Bind extraction to the exact current URL snapshot.')
    if origin['media'] != 'html':
        raise LaneError('RESEARCH_WEB_HTML_REQUIRED', 'Select a captured HTML response for this extractor.')
    contract = parser_contract()
    response = execution.submit('research_web_extract_source', {'body_base64': base64.b64encode(body).decode(),
        'capture': origin['capture'], 'extractor': selected_extractor, 'encoding': request.encoding}).result()
    if response['status'] != 'ok':
        raise LaneError('RESEARCH_WEB_WORKER_FAILED', 'The selected saved-byte extractor did not complete.')
    parsed = response['result']
    if (parsed['body_sha256'] != digest(body) or parsed['network_used'] is not False
            or parsed['extraction']['extractor'] != selected_extractor):
        raise LaneError('RESEARCH_WEB_WORKER_BINDING', 'The extraction differs from the selected saved response.')
    return publish(context, request, response, origin['capture'], parsed['extraction'], parsed['facts'],
        body, wire, 'html', contract, request.snapshot_id)


def receipt_body(manifest, snapshot_id):
    return {key: manifest[key] for key in ('project_id', 'lane_id', 'source_id', 'previous_snapshot',
        'operation', 'job_id', 'task_id', 'plan_revision', 'worker_digest', 'capture_snapshot')} | {
        'snapshot_id': snapshot_id, 'source_assertions_validated': False}


def output_body(manifest, snapshot_id, lane, receipt_id):
    return {'snapshot_id': snapshot_id, 'source_id': manifest['source_id'], 'generation': manifest['generation'],
        'previous_snapshot': manifest['previous_snapshot'], 'url': manifest['url'], 'logical_name': manifest['logical_name'],
        'final_url': manifest['capture']['final_url'], 'sha256': manifest['body_object'],
        'wire_sha256': manifest['wire_object'], 'bytes': manifest['capture']['body_bytes'],
        'parser': manifest['parser'], 'media': manifest['media'], 'receipt_id': receipt_id,
        'natural_path': str(lane.files / 'natural' / snapshot_id / 'response.body'),
        'capture_snapshot': manifest['capture_snapshot'], 'source_assertions_validated': False,
        'remote_currentness': manifest['capture']['remote_currentness']}


def publish(context, request, response, captured, extracted, facts, body, wire, media, contract, origin):
    execution, store = context.execution, context.execution.store
    if (parser_contract() != contract or len(body) > 8_388_608 or len(wire) > 8_388_608
            or captured['body_sha256'] != digest(body) or captured['wire_sha256'] != digest(wire)
            or captured['body_bytes'] != len(body) or captured['wire_bytes'] != len(wire)
            or facts['source_sha256'] != digest(body) or extracted['source_sha256'] != digest(body)
            or any(item['kind'] not in WEB_TABLES for item in facts['items'])):
        raise LaneError('RESEARCH_WEB_WORKER_BINDING', 'The selected worker bytes or parser changed before publication.')
    url, stamp = captured['requested_url'], now()
    source_id = digest(canonical_json_bytes([store.project_id, 'research', 'web', url]))
    prior = read_snapshot(store, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior['source_id'] != source_id:
        raise LaneError('RESEARCH_WEB_SOURCE_CHANGED', 'The previous snapshot belongs to another URL.')
    captured = {key: value for key, value in captured.items() if key not in {'body_base64', 'wire_base64'}}
    parsed = response['result']
    manifest = {'schema': 'evidence-lane.research-web-snapshot.v4', 'project_id': store.project_id,
        'lane_id': 'research', 'source_id': source_id, 'url': url, 'logical_name': 'web/' + source_id + '/response.body',
        'generation': prior['generation'] + 1 if prior else 1, 'previous_snapshot': request.expected_snapshot,
        'created_at': stamp, 'body_object': digest(body), 'raw_object': digest(body), 'wire_object': digest(wire),
        'facts_object': digest(canonical_json_bytes(facts)), 'extraction_object': digest(canonical_json_bytes(extracted)),
        'parser_contract': contract, 'parser': extracted['extractor'], 'media': media, 'capture': captured,
        'capture_snapshot': origin, 'operation': execution.guard.spec.name, 'arguments': request.model_dump(mode='json'),
        'tool_admission': context.tool_admission,
        'worker_envelope': {key: value for key, value in response.items() if key != 'result'},
        'worker_fields': {key: value for key, value in parsed.items() if key not in {'capture', 'extraction', 'facts'}},
        'worker_digest': content_digest(response), 'job_id': execution.claim.job_id,
        'task_id': execution.task_id, 'plan_revision': context.expected_revision}
    snapshot_id = digest(canonical_json_bytes(manifest))
    if len(canonical_json_bytes(manifest)) > 2_097_152:
        raise LaneError('RESEARCH_WEB_MANIFEST_BUDGET', 'The exact web receipt exceeds its bound.')
    execution._before_more_work()
    with execution.lease.coordinated_transaction(['research', 'receipts']):
        lane = store.lane('research')
        apply_migrations(lane, migrations('research'), writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute('SELECT snapshot_id FROM research_web_current WHERE source_id=?', (source_id,)).fetchone()
            if (live[0] if live else None) != request.expected_snapshot:
                raise LaneError('RESEARCH_WEB_SNAPSHOT_CHANGED', 'The current URL snapshot changed before publication.')
            execution.guard.check()
            for value in (body, wire, canonical_json_bytes(facts), canonical_json_bytes(extracted), canonical_json_bytes(manifest)):
                lane.put_object(value)
            connection.execute('INSERT OR IGNORE INTO research_web_source VALUES(?,?,?)', (source_id, url, stamp))
            connection.execute('INSERT INTO research_web_version VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (snapshot_id, source_id, manifest['generation'], request.expected_snapshot, snapshot_id,
                    manifest['body_object'], manifest['wire_object'], manifest['facts_object'], manifest['extraction_object'], contract, stamp))
            for item in facts['items']:
                connection.execute('INSERT INTO research_web_fact VALUES(?,?,?,?,?,?)',
                    (snapshot_id, item['item_id'], item['kind'], item['ordinal'], item['part'], json_text(item)))
            for item_id, ordinal, text in chunks(facts):
                chunk_id = digest(canonical_json_bytes([snapshot_id, item_id, ordinal]))
                connection.execute('INSERT INTO research_web_chunk VALUES(?,?,?,?,?)',
                    (snapshot_id, chunk_id, item_id, ordinal, lane.put_object(text.encode())))
                connection.execute('INSERT INTO research_web_chunk_fts VALUES(?,?,?)', (snapshot_id, chunk_id, text))
            natural_file(lane, snapshot_id, 'response.body', body)
            natural_file(lane, snapshot_id, 'response.wire', wire)
            connection.execute('INSERT INTO research_web_current VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',
                (source_id, snapshot_id))
            receipt_id = store.append_receipt('research_web_snapshot', receipt_body(manifest, snapshot_id))
    return result(store, 'research', manifest['operation'], output_body(manifest, snapshot_id, lane, receipt_id))


def verify(context, request, output):
    store, snapshot_id = context.store, output.result['snapshot_id']
    manifest, facts, extracted, body, wire = read_snapshot(store, snapshot_id)
    lane = store.lane('research')
    response = {**manifest['worker_envelope'], 'result': {**manifest['worker_fields'], 'extraction': extracted, 'facts': facts}}
    if isinstance(request, Capture):
        response['result']['capture'] = {**manifest['capture'], 'body_base64': base64.b64encode(body).decode(),
            'wire_base64': base64.b64encode(wire).decode()}
    valid = (output.project_id == store.project_id and output.lane_id == 'research'
        and manifest['parser_contract'] == parser_contract() and current_id(store, manifest['source_id']) == snapshot_id
        and manifest['arguments'] == request.model_dump(mode='json')
        and output.operation == manifest['operation'] and manifest['job_id'] == context.job_id
        and manifest['task_id'] == context.task_id and manifest['plan_revision'] == context.plan_revision
        and output.result == output_body(manifest, snapshot_id, lane, output.result['receipt_id'])
        and content_digest(response) == manifest['worker_digest']
        and list(context.worker_evidence) == [{'digest': manifest['worker_digest'], 'status': 'ok',
            'worker_pid': manifest['worker_envelope']['worker_pid']}])
    for name, content in (('response.body', body), ('response.wire', wire)):
        path = lane.files / 'natural' / snapshot_id / name
        from .sector_evidence_workers import read_source
        valid &= read_source(path, 8_388_608) == content
    if isinstance(request, Extract):
        source = read_snapshot(store, request.snapshot_id)
        valid &= (source[3] == body and source[4] == wire and source[0]['capture'] == manifest['capture']
            and manifest['capture_snapshot'] == request.snapshot_id
            and manifest['tool_admission']['route_id'] == 'research_web_extract.' + extracted['extractor']
            and extracted['extractor'] in (EXTRACTORS if request.extractor == 'auto' else (request.extractor,)))
    else:
        captured = manifest['capture']
        valid &= (manifest['capture_snapshot'] is None and captured['requested_url'] == request.url
            and manifest['tool_admission']['route_id'] == 'research_web_capture.' + captured['transport']
            and captured['transport'] in ({'httpx', 'requests'} if request.transport == 'auto' else {request.transport})
            and max(len(body), len(wire)) <= request.max_bytes and len(captured['redirects']) <= request.max_redirects)
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
            and type(request).model_validate(entry['arguments']) == request)
    with lane.connection(read_only=True) as connection:
        rows = connection.execute('SELECT * FROM research_web_fact WHERE snapshot_id=? ORDER BY item_id', (snapshot_id,)).fetchall()
        expected = sorted(facts['items'], key=lambda item: item['item_id'])
        valid &= ([json.loads(row['payload_json']) for row in rows] == expected and all(
            (row['item_id'], row['kind'], row['ordinal'], row['part']) ==
            (item['item_id'], item['kind'], item['ordinal'], item['part']) for row, item in zip(rows, expected)))
        rows = connection.execute('SELECT c.*,f.text_content FROM research_web_chunk c JOIN research_web_chunk_fts f '
            'ON c.snapshot_id=f.snapshot_id AND c.chunk_id=f.chunk_id WHERE c.snapshot_id=?', (snapshot_id,)).fetchall()
        expected_chunks = {(item, ordinal): text for item, ordinal, text in chunks(facts)}
        valid &= len(rows) == len(expected_chunks)
        for row in rows:
            text = expected_chunks.get((row['item_id'], row['ordinal']))
            valid &= (text is not None and text == row['text_content']
                and object_bytes(lane, row['text_object'], 16_384).decode() == text
                and row['chunk_id'] == digest(canonical_json_bytes([snapshot_id, row['item_id'], row['ordinal']])))
    with store.lane('receipts').connection(read_only=True) as connection:
        receipt = connection.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='research_web_snapshot'",
            (output.result['receipt_id'],)).fetchone()
    valid &= receipt is not None and json.loads(receipt[0]) == receipt_body(manifest, snapshot_id)
    return [{'check_id': name, 'passed': bool(valid), 'evidence': {'snapshot_id': snapshot_id,
        'remote_currentness': 'one_observed_response_chain_not_rechecked', 'source_assertions_validated': False}}
        for name in context.requested_checks]


def current(store):
    if not present(store):
        return []
    with store.lane('research').connection(read_only=True) as connection:
        rows = connection.execute("SELECT v.*,'web/'||s.source_id||'/response.body' AS logical_name,s.url FROM research_web_current c "
            'JOIN research_web_version v USING(snapshot_id) JOIN research_web_source s ON s.source_id=c.source_id '
            'ORDER BY v.created_at DESC,v.snapshot_id LIMIT 129').fetchall()
    return [dict(row) | {'origin': 'web_capture', 'remote_currentness': 'not_rechecked_by_metadata_read'} for row in rows]


def query(store, request):
    from .sector_evidence_profile import query_facts
    manifest, facts, _, _, _ = read_snapshot(store, request.snapshot_id)
    return query_facts(store, request, manifest, facts, 'research_web', WEB_TABLES)


def read(store, request):
    manifest, _, _, raw, _ = read_snapshot(store, request.snapshot_id)
    content = raw[request.offset:request.offset + request.max_bytes]
    end = request.offset + len(content)
    return {'snapshot_id': request.snapshot_id, 'logical_name': manifest['logical_name'], 'url': manifest['url'],
        'sha256': manifest['body_object'], 'representation': request.representation, 'total_bytes': len(raw),
        'offset': request.offset, 'content_base64': base64.b64encode(content).decode(),
        'next_offset': end if end < len(raw) else None, 'network_used': False}


def register_web_actions(engine):
    tools = {'trafilatura': 'trafilatura', 'readability-lxml': 'readability_lxml',
        'beautifulsoup4': 'BeautifulSoup4', 'lxml': 'lxml', 'markdownify': 'markdownify', 'html2text': 'html2text'}
    engine.registry.register(ActionSpec('research_web_capture',
        'Capture one authorized bounded HTTP response chain into immutable Research evidence.',
        Capture, EvidenceResult, capture_httpx, permission='write', mutates=True, requires_delta=True,
        profile='research', workflow='source-intake', worker_operations=('research_web_capture_source',),
        verification_checks=('research_web_snapshot_integrity',), verifier=verify,
        tool_routes=(ToolRoute('research_web_capture.httpx', capture_httpx, ('Python', 'HTTPX', 'validators', 'tldextract'),
            argument_values=(('transport', ('auto', 'httpx')),)),
            ToolRoute('research_web_capture.requests', capture_requests, ('Python', 'Requests', 'validators', 'tldextract'),
                argument_values=(('transport', ('auto', 'requests')),)))))
    extractors = dict(zip(EXTRACTORS, (extract_trafilatura, extract_readability, extract_beautifulsoup,
        extract_markdownify, extract_html2text, extract_stdlib)))
    engine.registry.register(ActionSpec('research_web_extract',
        'Project saved HTML bytes through the first ready or explicitly selected extractor without network access.',
        Extract, EvidenceResult, extract_trafilatura, permission='write', mutates=True, requires_delta=True,
        profile='research', workflow='source-intake', worker_operations=('research_web_extract_source',),
        verification_checks=('research_web_snapshot_integrity',), verifier=verify,
        tool_routes=tuple(ToolRoute('research_web_extract.' + name, extractors[name],
            ('Python', *(tools[dist] for dist in DISTRIBUTIONS[name])), argument_values=(('extractor', ('auto', name)),),
            reason='Ordered readiness selects a disclosed algorithm before invocation; an explicit selection stays exact.') for name in EXTRACTORS)))

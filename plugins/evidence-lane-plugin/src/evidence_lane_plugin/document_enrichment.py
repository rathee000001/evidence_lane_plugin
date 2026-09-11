"""Optional, attributed Docling projections of immutable document snapshots."""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from pydantic import Field

from .document_parsers import digest
from .document_profile import (
    DIGEST,
    DocumentResult,
    DocumentSelection,
    DocumentSnapshot,
    _calls,
    read_snapshot,
    result,
)
from .document_schema import DOC_MIGRATIONS
from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations
from .registry import ActionSpec
from .storage import now, project_snapshot
from .tool_routes import ToolRoute


class DocumentEnrich(DocumentSnapshot):
    max_output_bytes: int = Field(default=1_048_576, ge=4096, le=2_097_152)


class DocumentEnrichmentRead(DocumentSelection):
    enrichment_id: str = Field(pattern=DIGEST)
    offset: int = Field(default=0, ge=0, le=2_097_152)
    max_characters: int = Field(default=32_768, ge=1, le=65_536)
    include_structure: bool = False
    max_structure_bytes: int = Field(default=131_072, ge=4096, le=524_288)


def enrichment_worker(arguments):
    from .document_toolchain import DoclingRequest, extract_with_docling
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise LaneError('DOCUMENT_ENRICHMENT_INPUT_CHANGED', 'The enrichment bytes differ from the selected snapshot.')
    extension = Path(arguments['logical_name']).suffix.lower()
    if extension not in {'.docx', '.dotx'}:
        raise LaneError('DOCUMENT_ENRICHMENT_FORMAT_UNSUPPORTED', 'The Docs enrichment operation accepts DOCX or DOTX snapshots.')
    with tempfile.TemporaryDirectory(prefix='evidence-lane-document-enrichment-') as folder:
        source = Path(folder) / ('document' + extension)
        source.write_bytes(content)
        return extract_with_docling(DoclingRequest(source_path=source, host_profile='STUDIO_WORKER',
            max_file_bytes=8_388_608, max_output_bytes=arguments['max_output_bytes']))


def enrich(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    snapshot, _ = read_snapshot(store, request.snapshot_id)
    lane = store.lane('docs')
    response = execution.submit('document_enrich', {'logical_name': snapshot['logical_name'],
        'content_base64': base64.b64encode(lane.read_object(snapshot['raw_object'])).decode(),
        'expected_sha256': snapshot['raw_object'], 'max_output_bytes': request.max_output_bytes}).result()
    if response['status'] != 'ok':
        raise LaneError(response.get('code', 'DOCUMENT_ENRICHMENT_FAILED'), 'The selected rich document converter did not complete.')
    converted = json.loads(json.dumps(response['result']))
    receipt = converted.pop('receipt_sha256', None)
    if (digest(canonical_json_bytes(converted)) != receipt or converted['status'] != 'complete'
            or converted['converter_status'] != 'success' or converted['source_sha256'] != snapshot['raw_object']
            or converted['source_mutated'] or converted['allow_model_download']):
        raise LaneError('DOCUMENT_ENRICHMENT_BINDING', 'The rich conversion differs from its requested bytes or completion contract.')
    body = {'schema': 'evidence-lane.document-enrichment.v4', 'project_id': store.project_id, 'lane_id': 'docs',
        'snapshot_id': request.snapshot_id, 'conversion': {**converted, 'receipt_sha256': receipt}, 'created_at': now()}
    identity = digest(canonical_json_bytes(body))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(['docs', 'receipts']):
        apply_migrations(lane, DOC_MIGRATIONS, writer=execution.lease)
        with lane.transaction() as connection:
            obj = lane.put_object(canonical_json_bytes(body))
            connection.execute('INSERT INTO docling_extraction VALUES(?,?,?,?)', (identity, request.snapshot_id, obj, body['created_at']))
            store.append_receipt('document_enrichment', {'enrichment_id': identity, 'snapshot_id': request.snapshot_id,
                'engine': 'Docling', 'status': 'complete', 'source_bytes_mutated': False})
    return result(store, 'document_enrich', {'enrichment_id': identity, 'snapshot_id': request.snapshot_id,
        'engine': 'Docling', 'converter_status': converted['converter_status'], 'markdown_sha256': converted['markdown_sha256'],
        'source_bytes_mutated': False, 'model_assets_required': False, 'native_layout_fidelity': 'not_claimed'})


def enrichment_manifest(store, identity):
    lane = store.lane('docs')
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT * FROM docling_extraction WHERE enrichment_id=?', (identity,)).fetchone()
    if row is None:
        raise LaneError('DOCUMENT_ENRICHMENT_MISSING', 'Select an exact rich document conversion from this Docs lane.')
    body = json.loads(lane.read_object(row['manifest_object']))
    if (digest(canonical_json_bytes(body)) != identity or body['project_id'] != store.project_id
            or body['lane_id'] != 'docs' or body['snapshot_id'] != row['snapshot_id']):
        raise LaneError('DOCUMENT_ENRICHMENT_INTEGRITY', 'The rich document manifest differs from its indexed identity.')
    snapshot, _ = read_snapshot(store, body['snapshot_id'])
    if snapshot['raw_object'] != body['conversion']['source_sha256']:
        raise LaneError('DOCUMENT_ENRICHMENT_INTEGRITY', 'The rich conversion belongs to different source bytes.')
    return body


def verify_enrichment(context, request, output):
    manifest = enrichment_manifest(context.store, output.result['enrichment_id'])
    return [{'check_id': name, 'passed': manifest['snapshot_id'] == request.snapshot_id,
        'evidence': {'enrichment_id': output.result['enrichment_id'], 'converter_status': manifest['conversion']['converter_status']}}
        for name in context.requested_checks]


def register_enrichment_actions(engine):
    engine.registry.register(ActionSpec('document_enrich', 'Add a separate completed Docling projection to an exact DOCX snapshot.',
        DocumentEnrich, DocumentResult, enrich, permission='write', mutates=True, requires_delta=True, profile='document',
        workflow='manage-project-sources', worker_operations=('document_enrich',),
        verification_checks=('document_enrichment_integrity',), verifier=verify_enrichment,
        tool_routes=(ToolRoute('document_enrich.docling', enrich, ('Python', 'Docling'), systems=('Windows',)),)))

    def read(context, request):
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            body = enrichment_manifest(store, request.enrichment_id)
            conversion = body['conversion']
            text = conversion['markdown'][request.offset:request.offset + request.max_characters]
            end = request.offset + len(text)
            structure = conversion['document'] if request.include_structure else None
            if structure is not None and len(canonical_json_bytes(structure)) > request.max_structure_bytes:
                raise LaneError('DOCUMENT_ENRICHMENT_READ_BUDGET', 'The structured projection exceeds this explicit result budget.')
            return result(store, 'document_enrichment_read', {'enrichment_id': request.enrichment_id,
                'snapshot_id': body['snapshot_id'], 'source_sha256': conversion['source_sha256'], 'markdown': text,
                'offset': request.offset, 'next_offset': end if end < len(conversion['markdown']) else None,
                'complete_markdown_sha256': conversion['markdown_sha256'], 'converter_status': conversion['converter_status'],
                'enrichment_manifest_object': digest(canonical_json_bytes(body)), 'document': structure,
                'native_layout_fidelity': 'not_claimed'})
    engine.registry.register(ActionSpec('document_enrichment_read', 'Read a bounded Markdown excerpt and optional structured projection from an exact completed rich conversion.',
        DocumentEnrichmentRead, DocumentResult, read, profile='document', workflow='manage-project-sources', queryable_in_delta=True,
        cross_project_read=True, studio_read=True, read_migrations=DOC_MIGRATIONS))

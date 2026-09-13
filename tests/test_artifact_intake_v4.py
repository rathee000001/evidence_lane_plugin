"""Retained artifact text attribution and missing-extract states."""
import base64
import io
import zipfile

import pytest
from evidence_lane_plugin.document_parsers import digest
from evidence_lane_plugin.presentation_parsers import parse_presentation
from evidence_lane_plugin.sector_evidence_contracts import parser_for
from evidence_lane_plugin.sector_evidence_parsers import parse_evidence
from evidence_lane_plugin.sector_evidence_profile import read_snapshot

from .test_evidence_sectors_v4 import call, execute, plan
from .test_evidence_sectors_v4 import system as system  # noqa: PLC0414


def presentation_bytes(text='Artifact slide alpha'):
    from evidence_lane_plugin.presentation_authoring import generate_package

    from .test_presentation_profile_v4 import generation
    body = generation(slides=1)
    body['slides'][0]['objects'][0]['text'] = text
    return generate_package(body)


@pytest.mark.parametrize('suffix', ['.pptx', '.pptm', '.potx', '.ppsx'])
def test_presentation_artifact_uses_native_text_and_exact_locators(suffix):
    raw = presentation_bytes()
    name = 'report' + suffix
    assert parser_for(name, 'auto') == 'presentation'
    facts = parse_evidence('artifacts', name, raw, 'presentation')
    original = parse_presentation(name, raw)
    assert facts['fidelity']['native_fidelity'] == original['fidelity']
    native = {row['item_id']: row for row in facts['items'] if row['kind'] == 'native_fact'}
    extracts = [row for row in facts['items'] if row['kind'] == 'artifact_text_extract']
    assert extracts and any('Artifact slide alpha' in row['text'] for row in native.values())
    for row in extracts:
        owner = native[row['native_item_id']]
        assert row['source_locator'] == owner['native_payload']['part']
        assert row['text_sha256'] == digest(owner['text'].encode())
        assert not row['text']  # Pointer facts do not duplicate search chunks.
    assert not any(row['kind'] == 'artifact_review_required' for row in facts['items'])


@pytest.mark.parametrize('name,raw,parser', [
    ('empty.txt', b'', 'text'), ('empty.json', b'[]', 'json'),
    ('binary.bin', b'\x00binary', 'opaque'),
    ('empty.ipynb', b'{"nbformat":4,"cells":[]}', 'notebook'),
])
def test_no_text_extract_has_original_artifact_review_record(name, raw, parser):
    facts = parse_evidence('artifacts', name, raw, parser)
    rows = [row for row in facts['items'] if row['kind'] == 'artifact_review_required']
    assert len(rows) == 1
    assert rows[0]['reason'] == 'NO_TEXT_EXTRACT_AVAILABLE'
    assert rows[0]['exact_bytes_preserved'] is True
    assert facts['fidelity']['text_extract_available'] is False
    assert facts['fidelity']['review_required'] is True


def test_archive_member_names_are_not_content_extraction():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('not-extracted.txt', 'Hidden content alpha')
    facts = parse_evidence('artifacts', 'bundle.zip', stream.getvalue(), 'archive')
    assert any(row['kind'] == 'archive_member' and row['text'] for row in facts['items'])
    assert any(row['kind'] == 'artifact_review_required' for row in facts['items'])
    assert facts['fidelity']['text_extract_available'] is False


@pytest.mark.parametrize('has_text', [False, True])
def test_vector_metadata_is_distinct_from_extracted_text(has_text):
    content = '<text x="1" y="3">Artifact alpha</text>' if has_text else '<rect width="8" height="9"/>'
    raw = ('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="9">' + content + '</svg>').encode()
    facts = parse_evidence('artifacts', 'diagram.svg', raw, 'media')
    assert facts['fidelity']['text_extract_available'] is has_text
    assert any(row['kind'] == 'artifact_review_required' for row in facts['items']) is not has_text
    assert any(row['kind'] == 'artifact_media_probe' for row in facts['items'])


def test_raster_metadata_does_not_claim_ocr_text():
    from PIL import Image
    stream = io.BytesIO()
    Image.new('RGB', (8, 9), 'blue').save(stream, format='PNG')
    facts = parse_evidence('artifacts', 'image.png', stream.getvalue(), 'media')
    assert any(row.get('native_kind') == 'frame' and row['text'] for row in facts['items'])
    assert not any(row['kind'] == 'artifact_text_extract' for row in facts['items'])
    assert facts['fidelity']['text_extract_available'] is False


def test_powerpoint_requires_its_selected_tool_before_worker_dispatch(system):
    from evidence_lane_plugin.plan_runtime import PlanStore
    (system[1].source_root / 'report.pptx').write_bytes(presentation_bytes())
    plan(system, 'artifacts', ['artifacts_index'], tools=('Python',))
    task = PlanStore(system[1]).task('evidence-0', expected_revision=1)
    admitted = call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': 'artifacts_index',
        'arguments': {'filename': 'report.pptx'}}, expected_revision=1)
    assert admitted.status == 'error'
    assert system[0].workers.status()['succeeded_operations'] == 0
    assert not (system[1].root / 'artifacts').exists()


def test_presentation_artifact_owned_worker_refresh_and_historical_reads(system):
    store = system[1]
    path = store.source_root / 'report.pptx'
    first_bytes = presentation_bytes()
    path.write_bytes(first_bytes)
    plan(system, 'artifacts', ['artifacts_index', 'artifacts_refresh'], tools=('Python', 'PPTX_OpenXML'))
    first = execute(system, 'artifacts_index', {'filename': path.name})
    manifest, facts = read_snapshot(store, 'artifacts', first['snapshot_id'])
    assert manifest['parser'] == 'presentation' and manifest['worker_fields']['evidence']['parser'] == 'presentation'
    assert not facts['fidelity']['imported_code_executed']
    second_bytes = presentation_bytes('Artifact slide beta')
    path.write_bytes(second_bytes)
    second = execute(system, 'artifacts_refresh', {'filename': path.name,
        'expected_snapshot': first['snapshot_id']}, index=1)
    assert second['previous_snapshot'] == first['snapshot_id']
    assert path.read_bytes() == second_bytes
    for snapshot, expected, text in [(first, first_bytes, 'alpha'), (second, second_bytes, 'beta')]:
        read = call(system, 'artifacts_read', {'snapshot_id': snapshot['snapshot_id'], 'max_bytes': 131072})
        assert read.status == 'ok', read.error
        assert base64.b64decode(read.result['result']['content_base64']) == expected
        query = call(system, 'artifacts_query', {'snapshot_id': snapshot['snapshot_id'], 'query': text})
        assert query.status == 'ok' and query.result['result']['rows'], query.error
    assert not (store.root / 'ppt').exists()
    assert system[0].workers.status()['succeeded_operations'] == 2


@pytest.mark.parametrize('lane_id', ['research', 'custom'])
def test_shared_presentation_parser_keeps_other_evidence_lane_ownership(system, lane_id):
    path = system[1].source_root / 'report.pptx'
    path.write_bytes(presentation_bytes())
    plan(system, lane_id, [lane_id + '_index'], tools=('Python', 'PPTX_OpenXML'))
    indexed = execute(system, lane_id + '_index', {'filename': path.name})
    manifest, facts = read_snapshot(system[1], lane_id, indexed['snapshot_id'])
    assert manifest['parser'] == 'presentation' and manifest['lane_id'] == lane_id
    assert all(not row['kind'].startswith('artifact_') for row in facts['items'])
    searched = call(system, lane_id + '_query', {'snapshot_id': indexed['snapshot_id'], 'query': 'alpha'})
    assert searched.status == 'ok' and searched.result['result']['rows'], searched.error
    assert not (system[1].root / 'artifacts').exists()

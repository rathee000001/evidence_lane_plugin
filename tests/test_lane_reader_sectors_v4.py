"""Exact source fetch through the declared Office, BI and PDF owning readers."""
import base64
import hashlib

import pytest

from tests import test_document_profile_v4 as document
from tests import test_pdf_profile_v4 as pdf
from tests import test_powerbi_profile_v4 as powerbi
from tests import test_presentation_profile_v4 as presentation
from tests import test_tableau_profile_v4 as tableau
from tests import test_tabular_profile_v4 as tabular
from tests.test_code_profile_v4 import call
from tests.test_project_search_sectors_v4 import (
    document_system,
    indexed_media_system,
    pdf_assets,
    pdf_system,
    powerbi_assets,
    powerbi_system,
    presentation_system,
    tableau_system,
    tabular_system,
)
from tests.test_project_search_v4 import bytes_digest

__all__ = ['document_system', 'indexed_media_system', 'pdf_assets', 'pdf_system', 'powerbi_assets',
    'powerbi_system', 'presentation_system', 'tableau_system', 'tabular_system']


@pytest.mark.parametrize('fixture,owner,prefix,lane,filename', [
    ('document_system', document, 'document', 'docs', 'fixture.docx'),
    ('presentation_system', presentation, 'presentation', 'ppt', 'fixture.pptx'),
    ('tabular_system', tabular, 'spreadsheet', 'data_excel', 'fixture.xlsx'),
    ('tabular_system', tabular, 'data', 'data', 'values.json'),
    ('tableau_system', tableau, 'tableau', 'tableau', 'datasource_test.twb'),
    ('powerbi_system', powerbi, 'powerbi', 'power_bi', 'model.bim'),
    ('pdf_system', pdf, 'pdf', 'pdf_ocr', 'fixture.pdf'),
])
def test_lane_fetch_preserves_original_bytes_and_rejects_wrong_path(request, fixture, owner, prefix, lane, filename):
    system = request.getfixturevalue(fixture)
    raw = (system[1].source_root / filename).read_bytes()
    planner = owner.create_plan if owner is document else owner.plan
    planner(system, [prefix + '_index'])
    arguments = {'filename': filename}
    if prefix == 'powerbi':
        arguments['inspect_models'] = False
    if prefix == 'pdf':
        arguments['text_backend'] = 'pypdf'
    indexed = owner.execute(system, prefix + '_index', arguments)
    before = bytes_digest(system[1].root)
    args = {'lane_id': lane, 'snapshot_id': indexed['snapshot_id'], 'path': filename,
        'representation': 'original_source', 'max_bytes': 1024}
    fetch = owner.call(system, 'lane_fetch', args)
    assert fetch.status == 'ok', fetch.error
    data = fetch.result['result']['read']['result']
    assert base64.b64decode(data['content_base64']) == raw[:1024]
    assert data['sha256'] == hashlib.sha256(raw).hexdigest()
    assert fetch.result['result']['typed_facts_action'] == prefix + '_query'
    assert fetch.result['source_currentness'] == 'not_rechecked'
    wrong = owner.call(system, 'lane_fetch', args | {'path': 'different-source.bin'})
    assert wrong.error.code == 'LANE_FETCH_PATH_MISMATCH'
    lines = owner.call(system, 'lane_fetch', args | {'start_line': 2})
    assert lines.error.code == 'LANE_FETCH_LINE_RANGE_UNSUPPORTED'
    status = owner.call(system, 'lane_status', {'lane_id': lane, 'include_views': False})
    assert status.status == 'ok', status.error
    assert status.result['result']['database_head_verified']
    assert status.result['result']['schema_history_verified']
    assert bytes_digest(system[1].root) == before
    assert (system[1].source_root / filename).read_bytes() == raw


def test_media_fetch_uses_immutable_original_after_external_source_edit(indexed_media_system):
    system, indexed, raw = indexed_media_system
    store = system[1]
    (store.source_root / 'fixture.svg').write_bytes(b'external change')
    before = bytes_digest(store.root)
    for representation in ('primary', 'original_source'):
        response = call(system, 'lane_fetch', {'lane_id': 'images_ocr',
            'snapshot_id': indexed['snapshot_id'], 'path': 'fixture.svg',
            'representation': representation, 'byte_offset': 8, 'max_bytes': 1024})
        assert response.status == 'ok', response.error
        body = response.result['result']
        assert body['action'] == 'media_read' and body['typed_facts_action'] == 'media_query'
        assert base64.b64decode(body['read']['result']['content_base64']) == raw[8:]
        assert body['read']['result']['sha256'] == hashlib.sha256(raw).hexdigest()
    status = call(system, 'lane_status', {'lane_id': 'images_ocr', 'include_views': False})
    assert status.status == 'ok', status.error
    assert status.result['result']['schema_history_verified']
    assert bytes_digest(store.root) == before
    assert (store.source_root / 'fixture.svg').read_bytes() == b'external change'

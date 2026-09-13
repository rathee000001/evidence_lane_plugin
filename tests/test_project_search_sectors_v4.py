"""Exercise every registered sector's actual indexed lexical reader."""
import json
import time

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.media_workers import media_worker_operations
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.workers import WorkerPool

from tests import test_document_profile_v4 as document
from tests import test_pdf_profile_v4 as pdf
from tests import test_powerbi_profile_v4 as powerbi
from tests import test_presentation_profile_v4 as presentation
from tests import test_tableau_profile_v4 as tableau
from tests import test_tabular_profile_v4 as tabular
from tests.test_code_profile_v4 import call
from tests.test_document_profile_v4 import document_system
from tests.test_pdf_parsers_v4 import pdf_assets
from tests.test_pdf_profile_v4 import pdf_system
from tests.test_powerbi_parsers_v4 import powerbi_assets
from tests.test_powerbi_profile_v4 import powerbi_system
from tests.test_presentation_profile_v4 import presentation_system
from tests.test_project_search_v4 import bytes_digest
from tests.test_tableau_profile_v4 import tableau_system
from tests.test_tabular_profile_v4 import tabular_system

__all__ = ['document_system', 'pdf_assets', 'pdf_system', 'powerbi_assets', 'powerbi_system',
    'presentation_system', 'tableau_system', 'tabular_system']


@pytest.mark.parametrize('fixture,owner,prefix,lane,filename,term', [
    ('document_system', document, 'document', 'docs', 'fixture.docx', 'Scope'),
    ('presentation_system', presentation, 'presentation', 'ppt', 'fixture.pptx', 'Versioned'),
    ('tabular_system', tabular, 'spreadsheet', 'data_excel', 'fixture.xlsx', 'Alpha'),
    ('tabular_system', tabular, 'data', 'data', 'values.json', '0007'),
    ('tableau_system', tableau, 'tableau', 'tableau', 'datasource_test.twb', 'calculation'),
    ('powerbi_system', powerbi, 'powerbi', 'power_bi', 'model.bim', 'Facts'),
    ('pdf_system', pdf, 'pdf', 'pdf_ocr', 'fixture.pdf', 'Native'),
])
def test_search_uses_actual_snapshot_reader_and_literal_any_matching(request, fixture, owner, prefix, lane, filename, term):
    system = request.getfixturevalue(fixture)
    planner = owner.create_plan if owner is document else owner.plan
    planner(system, [prefix + '_index'])
    arguments = {'filename': filename}
    if prefix == 'powerbi':
        arguments['inspect_models'] = False
    if prefix == 'pdf':
        arguments['text_backend'] = 'pypdf'
    indexed = owner.execute(system, prefix + '_index', arguments)
    source_before, before = bytes_digest(system[1].source_root), bytes_digest(system[1].root)
    query = term + ' missinglexicalterm'
    direct = owner.call(system, prefix + '_query', {'snapshot_id': indexed['snapshot_id'], 'query': query, 'collection': 'text'})
    assert direct.status == 'ok' and not direct.result['result']['rows'], direct.error
    search = owner.call(system, 'lane_search', {'lane_id': lane, 'query': query})
    assert search.status == 'ok', search.error
    arm = search.result['arms'][0]
    assert arm['state'] == 'hit' and arm['hit_count'] > 0
    assert arm['queries'][0]['action'] == prefix + '_query'
    for method in ('tfidf', 'hybrid', 'rank_bm25'):
        ranked = owner.call(system, 'lane_search', {'lane_id': lane, 'query': query, 'retrieval': method})
        assert ranked.status == 'ok', ranked.error
        ranked_query = ranked.result['arms'][0]['queries'][0]
        assert ranked_query['ranking']['selected'] and ranked_query['ranking']['method'] == method
        assert ranked_query['owner_result_unchanged']
    assert arm['queries'][0]['snapshot_id'] == indexed['snapshot_id']
    assert arm['queries'][0]['arguments']['match_mode'] == 'any'
    assert before == bytes_digest(system[1].root)
    assert source_before == bytes_digest(system[1].source_root)


@pytest.fixture
def indexed_media_system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80"><text x="5" y="30">Distinctive media evidence</text></svg>'
    (source / 'fixture.svg').write_bytes(raw)
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool(media_worker_operations(), workers=1)) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,
            permissions=['read', 'write', 'tools', 'admin'])]))
        system = engine, store, session
        task = TaskDefinition(task_id='media-search', title='Index the SVG', requested_outcome='Exact searchable SVG evidence',
            profile='images_ocr', allowed_actions=['media_index'], permitted_paths=['.'],
            permitted_tools=['Python', 'defusedxml'], acceptance_checks=list(engine.registry.get('media_index').verification_checks))
        with engine.project_work.mutation(store) as lease:
            PlanStore(store).create(PlanCreate(title='Media query fixture', tasks=[task]), lease, actor_id='fixture')
        current = PlanStore(store).task('media-search', expected_revision=1)
        response = call(system, 'delta_enter', {'task_id': 'media-search', 'plan_revision': 1,
            'contract_digest': current.contract_digest, 'action': 'media_index', 'arguments': {'filename': 'fixture.svg'}}, expected_revision=1)
        assert response.status == 'queued', response.error
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with store.lane('plan').connection(read_only=True) as connection:
                run = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (response.job_id,)).fetchone())
            if run['state'] in {'verified', 'blocked'}:
                break
            time.sleep(.03)
        assert run['state'] == 'verified', run['error_code']
        engine.delta.owned_completion(response.job_id).result(
            timeout=max(1, deadline - time.monotonic())
        )
        indexed = json.loads(store.lane('plan').read_object(run['result_object']))['result']['result']
        yield system, indexed, raw


def test_media_owner_binding_and_svg_text_search_use_actual_worker(indexed_media_system):
    system, indexed, raw = indexed_media_system
    store = system[1]
    before = bytes_digest(store.root)
    with store.lane('images_ocr').connection(read_only=True) as connection:
        assert connection.execute("SELECT owner FROM schema_migrations WHERE owner='media'").fetchone()
    result = call(system, 'lane_search', {'query': 'Distinctive missinglexicalterm', 'lane_id': 'images_ocr'})
    assert result.status == 'ok', result.error
    assert result.result['arms'][0]['hit_count'] > 0
    assert result.result['arms'][0]['queries'][0]['snapshot_id'] == indexed['snapshot_id']
    ranked = call(system, 'lane_search', {'query': 'Distinctive', 'lane_id': 'images_ocr', 'retrieval': 'hybrid'})
    assert ranked.status == 'ok', ranked.error
    assert ranked.result['arms'][0]['queries'][0]['ranking']['selected']
    direct = call(system, 'media_query', {'snapshot_id': indexed['snapshot_id'], 'collection': 'text',
        'query': 'Distinctive missinglexicalterm'})
    assert direct.status == 'ok' and not direct.result['result']['rows']
    assert bytes_digest(store.root) == before
    assert (store.source_root / 'fixture.svg').read_bytes() == raw

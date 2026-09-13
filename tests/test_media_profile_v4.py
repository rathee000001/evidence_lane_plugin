"""Current media actions across verified Deltas, lane storage and real codecs."""
import base64
import io
import json

import pytest
from evidence_lane_plugin.media_derivatives import manifest
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskBudget, TaskDefinition
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from .media_fixtures import container_bytes
from .test_pdf_media_export_refresh_v4 import media_system as media_system  # noqa: PLC0414
from .test_pdf_parsers_v4 import pdf_assets as pdf_assets  # noqa: PLC0414
from .test_pdf_profile_v4 import call


def plan(system, actions, *, ocr_backend='rapidocr'):
    engine, store, _ = system
    tools = ['Python', 'Pillow', 'defusedxml', 'FFmpeg', 'SQLite_FTS5_BM25',
        'LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx']
    tools += ['OpenCV', 'RapidOCR_ONNX_Runtime'] if ocr_backend == 'rapidocr' else ['pytesseract_Tesseract']
    tasks = [TaskDefinition(task_id='media-' + str(i), title=action,
        requested_outcome='Verify source-bound media and independently readable derivatives',
        profile='images_ocr', allowed_actions=[action], permitted_paths=['.'], permitted_tools=tools,
        acceptance_checks=list(engine.registry.get(action).verification_checks),
        budget=TaskBudget(max_input_bytes=33_554_432, max_output_bytes=67_108_864, max_seconds=240))
        for i, action in enumerate(actions)]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Current media qualification', tasks=tasks), lease, actor_id='fixture')


def execute(system, action, arguments, *, index=0, expected='verified'):
    engine, store, _ = system
    task = PlanStore(store).task('media-' + str(index), expected_revision=1)
    admitted = call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments}, expected_revision=1)
    assert admitted.status == 'queued', admitted.error
    with engine._admission:
        assert engine._admission.wait_for(lambda: engine._background_jobs == 0, timeout=110), 'Media driver did not finish.'
    with store.lane('plan').connection(read_only=True) as connection:
        row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone())
    assert row['state'] == expected, row
    if expected == 'blocked':
        return row
    return json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']


@pytest.mark.parametrize('backend', ['rapidocr', 'tesseract'])
def test_real_ocr_preserves_empty_frame_review_and_honors_any_search(media_system, backend):
    store = media_system[1]
    with Image.new('RGB', (900, 220), 'white') as text, Image.new('RGB', (900, 220), 'white') as blank:
        ImageDraw.Draw(text).text((50, 70), 'Invoice 4827', font=ImageFont.load_default(size=48), fill='black')
        output = io.BytesIO()
        blank.save(output, format='TIFF', save_all=True, append_images=[text])
    raw = output.getvalue()
    (store.source_root / 'frames.tiff').write_bytes(raw)
    plan(media_system, ['media_index', 'media_ocr'], ocr_backend=backend)
    first = execute(media_system, 'media_index', {'filename': 'frames.tiff'})
    ocr = execute(media_system, 'media_ocr', {'snapshot_id': first['snapshot_id'],
        'frames': [2, 1], 'min_confidence': 1}, index=1)
    body = manifest(store, ocr['ocr_id'], 'ocr')['result']
    assert '4827' in ' '.join(row['text'] for row in body['lines'])
    assert body['compute']['selected_provider'] == 'CPU' and body['compute']['execution_state'] == 'executed'
    assert [r['frame'] for r in body['review_frames']] == [1]
    assert body['review_frames'][0]['reason'] == 'OCR_EMPTY'
    assert ocr['review_regions'] == len(body['review_regions']) + 1
    before = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    common = {'snapshot_id': first['snapshot_id'], 'collection': 'ocr_line', 'query': '4827 nevermatchingtoken'}
    found = call(media_system, 'media_query', {**common, 'match_mode': 'any'})
    assert found.status == 'ok', found.error
    assert any('4827' in r['text'] for r in found.result['result']['rows'])
    assert call(media_system, 'media_query', {**common, 'match_mode': 'all'}).result['result']['rows'] == []
    reviews = call(media_system, 'media_query', {'snapshot_id': first['snapshot_id'],
        'collection': 'review_region', 'frame': 1}).result['result']['rows']
    assert len(reviews) == 1 and reviews[0]['kind'] == 'frame_review'
    read = call(media_system, 'media_ocr_read', {'ocr_id': ocr['ocr_id']})
    assert read.result['result']['review_frames'] == body['review_frames']
    graph = call(media_system, 'lane_view_preview', {'view_id': 'images_ocr.structure',
        'scope': {'query': first['media_id']}})
    assert graph.status == 'ok', graph.error
    assert any(n['kind'] == 'media_review_frame' for n in graph.result['graph']['nodes'])
    assert before == {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    assert (store.source_root / 'frames.tiff').read_bytes() == raw
    assert not (store.root / 'pdf_ocr').exists()
    with media_system[0].project_work.mutation(store) as lease, lease.transaction('images_ocr'), store.lane('images_ocr').transaction() as connection:
        connection.execute('DELETE FROM media_review_frame WHERE ocr_id=?', (ocr['ocr_id'],))
    changed = call(media_system, 'media_ocr_read', {'ocr_id': ocr['ocr_id']})
    assert changed.status == 'error' and changed.error.code == 'MEDIA_OCR_INTEGRITY'


@pytest.mark.parametrize(('extension', 'kind', 'codec'), [('.mp4', 'video', 'libx264'), ('.wav', 'audio', 'pcm_s16le')])
def test_extracted_files_keep_original_snapshot_after_source_refresh(media_system, tmp_path, extension, kind, codec):
    store = media_system[1]
    raw, _ = container_bytes(tmp_path, extension, kind, codec)
    filename = 'selected' + extension
    (store.source_root / filename).write_bytes(raw)
    plan(media_system, ['media_index', 'media_extract', 'media_refresh'])
    first = execute(media_system, 'media_index', {'filename': filename})
    extracted = execute(media_system, 'media_extract', {'snapshot_id': first['snapshot_id'],
        'kind': 'video_frame' if kind == 'video' else 'audio_segment', 'duration_seconds': 0.25}, index=1)
    second = execute(media_system, 'media_refresh', {'filename': filename, 'expected_snapshot': first['snapshot_id']}, index=2)
    assert second['generation'] == 2 and second['previous_snapshot'] == first['snapshot_id']
    body = manifest(store, extracted['extraction_id'], 'extraction')
    assert body['snapshot_id'] == first['snapshot_id']
    read = call(media_system, 'media_extraction_read', {'extraction_id': extracted['extraction_id']})
    assert read.status == 'ok', read.error
    artifact = read.result['result']
    assert artifact['snapshot_id'] == first['snapshot_id']
    content = base64.b64decode(artifact['content_base64'])
    assert store.lane('images_ocr').read_object(extracted['sha256']) == content
    assert artifact['total_bytes'] == len(content)
    assert (store.source_root / filename).read_bytes() == raw
    assert not (store.root / 'artifacts').exists()


def test_missing_ocr_language_blocks_without_publishing_derivative(media_system):
    store = media_system[1]
    plan(media_system, ['media_index', 'media_ocr'], ocr_backend='tesseract')
    first = execute(media_system, 'media_index', {'filename': 'fixture.png'})
    blocked = execute(media_system, 'media_ocr', {'snapshot_id': first['snapshot_id'], 'language': 'zzz'}, index=1, expected='blocked')
    assert blocked['error_code'] == 'MEDIA_OCR_LANGUAGE_UNSUPPORTED'
    with store.lane('images_ocr').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM media_ocr_run').fetchone()[0] == 0


def test_metadata_respects_complete_item_read_budget(media_system):
    store = media_system[1]
    text = PngImagePlugin.PngInfo()
    text.add_text('Comment', 'Metadata ' * 800)
    with Image.new('RGB', (12, 8), 'navy') as image:
        image.save(store.source_root / 'fixture.png', pnginfo=text)
    plan(media_system, ['media_index'])
    first = execute(media_system, 'media_index', {'filename': 'fixture.png'})
    head = store.pv_head()
    small = call(media_system, 'media_query', {'snapshot_id': first['snapshot_id'], 'max_bytes': 1024})
    assert small.status == 'error' and small.error.code == 'MEDIA_QUERY_ITEM_TOO_LARGE'
    enough = call(media_system, 'media_query', {'snapshot_id': first['snapshot_id'], 'max_bytes': 16384})
    assert enough.status == 'ok' and enough.result['result']['metadata']['info']['Comment'] == 'Metadata ' * 800
    assert store.pv_head() == head

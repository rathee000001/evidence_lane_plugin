"""OCR compute fences plus real CPU lane operations; no AMD hardware claim."""
from __future__ import annotations

import base64
import hashlib
import json

import pytest
from evidence_lane_plugin.compute_routes import ComputeContract
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.provider_ocr import profile_evidence, raster

from .pdf_fixtures import generation
from .test_compute_routes_v4 import system as system  # noqa: PLC0414
from .test_pdf_media_export_refresh_v4 import execute as media_execute
from .test_pdf_media_export_refresh_v4 import media_system as media_system  # noqa: PLC0414
from .test_pdf_media_export_refresh_v4 import plan as media_plan
from .test_pdf_parsers_v4 import pdf_assets as pdf_assets  # noqa: PLC0414
from .test_pdf_profile_v4 import execute, plan
from .test_pdf_profile_v4 import pdf_system as pdf_system  # noqa: PLC0414


@pytest.mark.parametrize('providers', [[], ['DmlExecutionProvider'], ['CPUExecutionProvider'],
    ['DmlExecutionProvider', 'CPUExecutionProvider'], ['CUDAExecutionProvider']])
def test_model_profile_rejects_any_other_execution_provider(tmp_path, providers):
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps([{'cat': 'Node', 'args': {'provider': provider}} for provider in providers]))
    if set(providers) - {'DmlExecutionProvider', 'CPUExecutionProvider'}:
        with pytest.raises(ValueError, match='OCR_PROVIDER_FALLBACK'):
            profile_evidence(path, tmp_path)
    else:
        evidence = profile_evidence(path, tmp_path)
        assert evidence['providers'] == sorted(set(providers))
        assert evidence['node_events'] == len(providers)
        assert evidence['dml_node_events'] == providers.count('DmlExecutionProvider')
        assert evidence['cpu_support_node_events'] == providers.count('CPUExecutionProvider')


@pytest.mark.parametrize('fault', ['changed_bytes', 'wrong_size', 'wrong_name'])
def test_provider_raster_is_exact_bounded_engine_input(tmp_path, fault):
    png = base64.b64decode(generation(mixed=True)['pages'][1]['elements'][0]['image_base64'])
    path = tmp_path / 'raster.png'
    path.write_bytes(png)
    arguments = {'png_path': str(path), 'png_sha256': hashlib.sha256(png).hexdigest(), 'png_bytes': len(png)}
    assert raster(arguments) == png
    if fault == 'changed_bytes':
        path.write_bytes(png + b'changed')
    elif fault == 'wrong_size':
        arguments['png_bytes'] += 1
    else:
        arguments['png_path'] = str(path.with_name('caller.png'))
    with pytest.raises((ValueError, FileNotFoundError)):
        raster(arguments)


@pytest.mark.parametrize('fault', [None, 'selected', 'lines', 'invoked', 'provider_claim'])
def test_no_ocr_acceptance_requires_explicit_empty_native_page_result(system, fault):
    contract = ComputeContract('pdf_ocr', 'OCR_MEDIA', ('CPU', 'DIRECTML'), 2048, 'rapidocr_lines')
    invocation = system.router.invocation(contract, system.context, system.router.select(contract, system.context))
    value = {'compute': {'selected_provider': None, 'execution_state': 'not_required',
        'reason': 'native_text_satisfied_selected_pages'}, 'pages': [{'ocr_selected': False, 'ocr_lines': 0}],
        'lines': [], 'evidence': {'invoked': False}}
    if fault == 'selected':
        value['pages'][0]['ocr_selected'] = True
    elif fault == 'lines':
        value['lines'] = [{'text': 'invented'}]
    elif fault == 'invoked':
        value['evidence']['invoked'] = True
    elif fault == 'provider_claim':
        value['compute']['selected_provider'] = 'DIRECTML'
    if fault:
        with pytest.raises(LaneError) as error:
            invocation.result({'status': 'ok', 'result': value})
        assert error.value.code == 'COMPUTE_RESULT_UNBOUND'
    else:
        assert invocation.result({'status': 'ok', 'result': value})['execution_state'] == 'not_required'


def test_pdf_cpu_ocr_and_no_model_page_are_separate_verified_deltas(pdf_system):
    from evidence_lane_plugin.pdf_derivatives import manifest

    plan(pdf_system, ['pdf_index', 'pdf_ocr', 'pdf_ocr'])
    store = pdf_system[1]
    source = store.source_root / 'fixture.pdf'
    before = source.read_bytes()
    indexed = execute(pdf_system, 'pdf_index', {'filename': 'fixture.pdf'})
    native = execute(pdf_system, 'pdf_ocr', {'snapshot_id': indexed['snapshot_id'], 'pages': [1]}, index=1)
    native_result = manifest(store, native['ocr_id'], 'ocr')['result']
    assert native_result['compute']['execution_state'] == 'not_required'
    assert native_result['evidence']['invoked'] is False
    converted = execute(pdf_system, 'pdf_ocr', {'snapshot_id': indexed['snapshot_id'], 'pages': [2], 'dpi': 160}, index=2)
    value = manifest(store, converted['ocr_id'], 'ocr')['result']
    assert '4827' in ' '.join(line['text'] for line in value['lines'])
    assert value['compute']['selected_provider'] == 'CPU'
    assert value['compute']['execution_state'] == 'executed'
    assert value['evidence']['provider'] == 'CPUExecutionProvider'
    assert not value['evidence']['acceleration_claimed']
    assert source.read_bytes() == before


def test_media_cpu_ocr_has_actual_model_evidence_in_own_lane(media_system):
    from evidence_lane_plugin.media_derivatives import manifest

    store = media_system[1]
    source = store.source_root / 'fixture.png'
    raw = base64.b64decode(generation(mixed=True)['pages'][1]['elements'][0]['image_base64'])
    source.write_bytes(raw)
    sector = {'family': 'media', 'prefix': 'media', 'lane': 'images_ocr', 'system': media_system}
    media_plan(sector, ['index', 'ocr'])
    indexed = media_execute(sector, 'index', {'filename': 'fixture.png'})
    converted = media_execute(sector, 'ocr', {'snapshot_id': indexed['snapshot_id'], 'frames': [1]}, index=1)
    value = manifest(store, converted['ocr_id'], 'ocr')['result']
    assert '4827' in ' '.join(line['text'] for line in value['lines'])
    assert value['compute']['selected_provider'] == 'CPU'
    assert value['compute']['execution_state'] == 'executed'
    assert store.lane('images_ocr').database.is_relative_to(store.root / 'images_ocr')
    assert not (store.root / 'pdf_ocr').exists()
    assert source.read_bytes() == raw

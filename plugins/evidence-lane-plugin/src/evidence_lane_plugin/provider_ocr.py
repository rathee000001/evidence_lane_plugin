"""Python 3.12+ DirectML OCR operation in the isolated provider environment."""
from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import math
import tempfile
from pathlib import Path

MODEL_NAMES = ('PP-OCRv6_det_small.onnx', 'ch_ppocr_mobile_v2.0_cls_mobile.onnx', 'PP-OCRv6_rec_small.onnx')


def raster(arguments):
    path = Path(arguments['png_path'])
    if (not path.is_absolute() or path.name != 'raster.png' or path.is_symlink()
            or path.resolve(strict=True) != path or not 1 <= arguments['png_bytes'] <= 100_663_296):
        raise ValueError('OCR_RASTER_PATH')
    with path.open('rb') as stream:
        raw = stream.read(arguments['png_bytes'] + 1)
    if len(raw) != arguments['png_bytes'] or hashlib.sha256(raw).hexdigest() != arguments['png_sha256']:
        raise ValueError('OCR_RASTER_CHANGED')
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as value:
        if value.format != 'PNG' or not 0 < value.width * value.height <= 25_000_000:
            raise ValueError('OCR_RASTER_BUDGET')
    return raw


def profile_evidence(path, directory):
    path = Path(path)
    if (not path.resolve(strict=True).is_relative_to(directory.resolve())
            or path.is_symlink() or path.stat().st_size > 8_388_608):
        raise ValueError('OCR_PROFILE_BUDGET')
    raw = path.read_bytes()
    events = json.loads(raw)
    node_events = [item for item in events if item.get('cat') == 'Node']
    providers = {item.get('args', {}).get('provider') for item in node_events} - {None}
    if providers - {'DmlExecutionProvider', 'CPUExecutionProvider'}:
        raise ValueError('OCR_PROVIDER_FALLBACK')
    dml_nodes = sum(
        item.get('args', {}).get('provider') == 'DmlExecutionProvider'
        for item in node_events
    )
    cpu_nodes = sum(
        item.get('args', {}).get('provider') == 'CPUExecutionProvider'
        for item in node_events
    )
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'providers': sorted(providers),
        'node_events': len(node_events), 'dml_node_events': dml_nodes,
        'cpu_support_node_events': cpu_nodes,
        'dml_node_fraction': dml_nodes / len(node_events) if node_events else 0.0}


def run(request, manifest, probe, verify_asset):
    arguments = request['arguments']
    if manifest['runtime_id'] != 'directml' or arguments['language'] != 'eng':
        raise ValueError('OCR_PROVIDER_UNSUPPORTED')
    folder = verify_asset(arguments)
    if not set(MODEL_NAMES) <= {row['path'] for row in arguments['model_files']}:
        raise ValueError('OCR_MODELS_UNAVAILABLE')
    png = raster(arguments)
    observed = probe.directml_probe(request)  # Exact DirectML adapter plus numerical provider check.
    import cv2
    import numpy as np
    import onnxruntime as ort
    from rapidocr import RapidOCR

    # Initialize preprocessing and character metadata from local pinned models.
    # Replace all sessions before the first OCR model forward pass.
    params = {'Global.log_level': 'critical', 'Global.model_root_dir': str(folder),
        'EngineConfig.onnxruntime.intra_op_num_threads': 2, 'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        **{f'EngineConfig.onnxruntime.use_{name}': False for name in ('cuda', 'dml', 'cann', 'coreml')},
        **{f'{kind}.model_path': str(folder / name) for kind, name in zip(('Det', 'Cls', 'Rec'), MODEL_NAMES)}}
    engine = RapidOCR(params=params)
    profiles = []
    with tempfile.TemporaryDirectory(
        prefix='evidence-lane-ocr-profile-', ignore_cleanup_errors=True
    ) as temporary:
        directory = Path(temporary)
        sessions = []
        for ordinal, (part, name) in enumerate(zip((engine.text_det, engine.text_cls, engine.text_rec), MODEL_NAMES)):
            options = ort.SessionOptions()
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.enable_profiling = True
            options.profile_file_prefix = str(directory / f'model-{ordinal}')
            session = ort.InferenceSession(str(folder / name), sess_options=options,
                providers=[('DmlExecutionProvider', {'device_id': str(request['device_index'])}),
                    'CPUExecutionProvider'])
            session.disable_fallback()
            if session.get_providers()[:2] != [
                'DmlExecutionProvider', 'CPUExecutionProvider'
            ]:
                raise ValueError('OCR_PROVIDER_UNAVAILABLE')
            part.session.session = session
            sessions.append(session)
        image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('OCR_RASTER_INVALID')
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        try:
            output = engine(gray, text_score=0.0)
        finally:
            for name, session in zip(MODEL_NAMES, sessions):
                profiles.append(profile_evidence(session.end_profiling(), directory) | {'model': name})
        if not profiles or not all(row['dml_node_events'] > 0 for row in profiles):
            raise ValueError('OCR_EXECUTION_UNPROVEN')
        lines = []
        if output.txts is not None:
            if len(output.txts) > 5000 or not len(output.txts) == len(output.scores) == len(output.boxes):
                raise ValueError('OCR_LINE_BUDGET')
            for text, score, polygon in zip(output.txts, output.scores, output.boxes):
                points = polygon.tolist()
                if (not isinstance(text, str) or len(text) > 100_000 or not math.isfinite(float(score))
                        or not 0 <= score <= 1 or len(points) != 4
                        or any(len(point) != 2 or not all(math.isfinite(float(value)) for value in point) for point in points)):
                    raise ValueError('OCR_RESULT_INVALID')
                lines.append({'text': text, 'confidence': float(score), 'polygon_pixels': points})
    verify_asset(arguments)
    raster(arguments)
    return {'lines': lines, 'asset_identity': arguments['asset_identity'], 'raster_sha256': arguments['png_sha256'],
        'evidence': {'engine': 'RapidOCR', 'version': importlib.metadata.version('rapidocr'),
            'onnxruntime': ort.__version__, 'provider': 'DmlExecutionProvider', 'acceleration_claimed': True,
            'models_sha256': arguments['asset_identity'], 'models_downloaded': False, 'model_profiles': profiles},
        'compute': {'selected_provider': 'DIRECTML', 'runtime_id': 'directml',
            'device_id': observed['device_id'], 'device_index': request['device_index'],
            'environment_digest': manifest['lock_sha256'], 'runtime_manifest_sha256': request['manifest_sha256'],
            'execution_state': 'executed', 'runtime_version': observed['runtime_version'],
            'execution_basis': 'each_model_profile_proves_DmlExecutionProvider_nodes_with_attributed_CPU_support_nodes',
            'memory_limit_mechanism': 'admission_reservation_only; DirectML_has_no_per_session_VRAM_cap',
            'scope': 'neural_model_forward_uses_DirectML_and_CPU_support_nodes; raster_preprocessing_and_OCR_postprocessing_use_CPU'}}

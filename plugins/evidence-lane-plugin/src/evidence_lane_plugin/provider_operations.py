"""Fixed Python 3.12+ lane operations for an isolated provider interpreter.

This executable accepts engine-owned bindings over stdin, never a module name
or command. The pinned model is read locally; source texts are not persisted.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path


def sibling_module(name):
    if name not in {'provider_probe', 'provider_ocr'}:
        raise ValueError('PROVIDER_MODULE_UNAVAILABLE')
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('evidence_lane_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe_module():
    return sibling_module('provider_probe')


def verify_asset(arguments):
    folder = Path(arguments['model_path']).resolve(strict=True)
    rows = arguments['model_files']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise ValueError('MODEL_FILE_BUDGET')
    expected = set()
    for row in rows:
        relative = Path(row['path'])
        if relative.is_absolute() or relative.drive or '..' in relative.parts or ':' in str(relative):
            raise ValueError('MODEL_PATH_INVALID')
        path = folder / relative
        if path.is_symlink() or path.resolve() != path or not path.is_file() or path.stat().st_size != row['bytes']:
            raise ValueError('MODEL_FILE_CHANGED')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']:
                raise ValueError('MODEL_FILE_CHANGED')
        if relative.as_posix() in expected:
            raise ValueError('MODEL_FILE_DUPLICATE')
        expected.add(relative.as_posix())
    actual = set()
    for visited, path in enumerate(folder.rglob('*'), 1):
        if visited > 512 or path.is_symlink() or path.resolve() != path:
            raise ValueError('MODEL_DIRECTORY_INVALID')
        if path.is_file() and '.cache' not in path.relative_to(folder).parts:
            actual.add(path.relative_to(folder).as_posix())
    if actual != expected:
        raise ValueError('MODEL_FILE_SET_CHANGED')
    return folder


def embed(request, manifest, probe, cache=None):
    arguments = request['arguments']
    texts = arguments['texts']
    model_id = 'BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a'
    if (arguments['model_id'] != model_id or not isinstance(texts, list) or not 1 <= len(texts) <= 8
            or any(not isinstance(text, str) for text in texts) or sum(len(text) for text in texts) > 262_144):
        raise ValueError('CODE_EMBEDDING_INPUT_BUDGET')
    folder = verify_asset(arguments)
    runtime = manifest['runtime_id']
    if runtime not in {'cpu', 'cuda', 'rocm'}:
        raise ValueError('EMBEDDING_PROVIDER_UNSUPPORTED')
    # This checks the actual Torch build and exact device UUID, then runs a
    # numerical kernel. It is distinct from the model execution below.
    observed = probe.torch_probe(runtime, request)
    import torch
    from sentence_transformers import SentenceTransformer

    device = 'cpu' if runtime == 'cpu' else f"cuda:{request['device_index']}"
    if runtime != 'cpu':
        free, total = torch.cuda.mem_get_info(device)
        reservation = request['required_vram_mib'] * 1_048_576
        if not 0 < reservation <= free or reservation >= total:
            raise ValueError('PROVIDER_MEMORY_BUDGET')
        torch.cuda.set_per_process_memory_fraction(reservation / total, device)
    key = (runtime, device, model_id, str(folder), arguments['asset_identity'])
    if cache is not None and cache and key not in cache:
        raise ValueError('PROVIDER_MODEL_CACHE_SCOPE')
    reused = cache is not None and key in cache
    model = cache[key] if reused else SentenceTransformer(str(folder), local_files_only=True, trust_remote_code=False, device=device)
    if cache is not None:
        cache[key] = model
    selected = torch.device(device)
    if any(parameter.device != selected for parameter in model.parameters()):
        raise ValueError('EMBEDDING_MODEL_DEVICE_MISMATCH')
    with torch.inference_mode():
        vectors = model.encode(texts, convert_to_tensor=True, normalize_embeddings=True,
            show_progress_bar=False, device=device)
        if vectors.device != selected or tuple(vectors.shape) != (len(texts), 384):
            raise ValueError('EMBEDDING_OUTPUT_DEVICE_MISMATCH')
        if runtime != 'cpu':
            torch.cuda.synchronize(device)
        rows = [[float(value) for value in row] for row in vectors.cpu().tolist()]
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError('CODE_VECTOR_INVALID')
    verify_asset(arguments)
    return {'model_id': model_id, 'dimension': 384, 'vectors': rows,
        'asset_identity': arguments['asset_identity'], 'device': device, 'network_downloads': False,
        'text_window': 'pinned_model_token_window; exact_full_text_remains_in_FTS',
        'compute': {'selected_provider': {'cuda': 'NVIDIA_CUDA', 'rocm': 'AMD_ROCM'}.get(runtime, 'CPU'),
            'runtime_id': runtime, 'device_id': observed['device_id'], 'device_index': request['device_index'],
            'environment_digest': manifest['lock_sha256'], 'runtime_manifest_sha256': request['manifest_sha256'],
            'execution_state': 'executed', 'runtime_version': observed['runtime_version'],
            'resident_model_reused': reused,
            'execution_basis': 'model_parameters_and_encoded_tensor_on_selected_device_then_synchronized',
            'scope': 'neural_model_forward; tokenization_and_result_serialization_use_CPU'}}


def main():
    request = {}
    try:
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError('PROVIDER_INPUT_BUDGET')
        request = json.loads(raw)
        probe = probe_module()
        with contextlib.redirect_stdout(sys.stderr):
            manifest = probe.verify_environment(Path(request['manifest_path']), request['manifest_sha256'])
            if (manifest['runtime_id'] != request['runtime_id'] or request['operation'] not in {'code_embed_text', 'rapidocr_lines'}
                    or request['operation'] not in manifest.get('operations', [])):
                raise ValueError('PROVIDER_OPERATION_UNAVAILABLE')
            value = (embed(request, manifest, probe) if request['operation'] == 'code_embed_text'
                else sibling_module('provider_ocr').run(request, manifest, probe, verify_asset))
        result = {'status': 'ok', 'result': value}
    except Exception:  # noqa: BLE001 - no text, local path or vendor exception crosses this boundary
        result = {'status': 'error', 'code': 'PROVIDER_OPERATION_FAILED'}
    result.update(request_id=request.get('request_id'), operation=request.get('operation'))
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()

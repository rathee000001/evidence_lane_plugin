"""Compile all retained tool requirements separately from operation selection.

Imported pins remain unqualified inputs until the full installer verifies the
selected platform bundle. Nothing here installs, downloads or claims readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/evidence-lane-plugin'
sys.path.insert(0, str(PLUGIN / 'src'))

from evidence_lane_plugin.tool_catalog import DISTRIBUTIONS

EXTRA_DISTRIBUTIONS = {
    'DOCX_OpenXML': ['python-docx', 'olefile'],
    'PPTX_OpenXML': ['lxml', 'Pillow', 'olefile'],
    'MCP_Python_SDK': ['mcp'], 'Pydantic': ['pydantic'], 'HTTPX': ['httpx'],
    'defusedxml': ['defusedxml'],
    'Cryptography_PyJWT': ['cryptography', 'PyJWT'],
    'RapidOCR_ONNX_Runtime': ['rapidocr', 'onnxruntime'],
    'OpenCV': ['opencv-python'], 'pytesseract_Tesseract': ['pytesseract'],
    'HuggingFace_Hub_ModelSnapshot': ['huggingface-hub'], 'FFmpeg': ['imageio-ffmpeg'],
    'LangGraph_Mermaid_engine': ['langgraph', 'langchain-core'],
}
NATIVE = {'Graphviz_dot': 'graphviz', 'Poppler_pdftotext_pdfinfo': 'poppler',
          'Ghostscript': 'ghostscript', 'pytesseract_Tesseract': 'tesseract',
          'ripgrep_15_2_0': 'ripgrep', 'SevenZip_NSIS_extractor': 'seven_zip_extractor',
          'jq': 'jq', 'FFmpeg': 'ffmpeg', 'LibreOffice': 'libreoffice',
          'PowerBI_TOM': 'powerbi_tom', 'PBIXRay': 'powerbi_pbix'}
MODEL_ASSETS = {'SentenceTransformers': ['embedding_snapshot'],
                'HuggingFace_Hub_ModelSnapshot': ['embedding_snapshot'],
                'TreeSitter_LanguagePack': ['parser_grammars'],
                'RapidOCR_ONNX_Runtime': ['rapidocr_models'],
                'pytesseract_Tesseract': ['tesseract_languages', 'tesseract_runtime'], 'Docling': ['docling_models'],
                'Poppler_pdftotext_pdfinfo': ['poppler_runtime'],
                'FFmpeg': ['ffmpeg_runtime'],
                'PowerBI_TOM': ['powerbi_tom_runtime'], 'PBIXRay': ['powerbi_pbix_runtime']}
HTTP_ONLY_EXTERNAL_SERVICES = {'LangSmith', 'Langfuse', 'OpenTelemetry'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(name):
    return re.sub('[-_.]+', '-', name).lower()


def lock_entries(path):
    result = {}
    # Hashes are stored with each logical pinned requirement, including all
    # platform wheel hashes already present in the admitted lock input.
    blocks = re.split(r'(?=^[A-Za-z0-9_.-]+==)', path.read_text(encoding='utf-8'), flags=re.MULTILINE)
    for block in blocks:
        match = re.match(r'([A-Za-z0-9_.-]+)==([^\s;\\]+)', block)
        if match:
            result[normalized(match[1])] = {'distribution': match[1], 'version': match[2],
                'hashes': sorted(set(re.findall(r'--hash=sha256:([a-f0-9]{64})', block)))}
    return result


def outputs():
    catalog_path = PLUGIN / 'toolchains/tool-catalog.v4.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    lock_paths = [ROOT / 'requirements.lock.txt', ROOT / 'requirements-dev.lock.txt',
                  PLUGIN / 'requirements.toolchain.lock.txt', PLUGIN / 'requirements.lock.txt',
                  PLUGIN / 'requirements.runtime.lock.txt']
    locks = [(path, lock_entries(path)) for path in lock_paths]
    lock_refs = dict(zip(lock_paths, ['toolchains/locks/engine.lock.txt', 'toolchains/locks/development.lock.txt',
                                    'toolchains/locks/tools.lock.txt', 'toolchains/locks/admitted-base.lock.txt',
                                    'toolchains/locks/runtime.lock.txt'], strict=True))
    files = {lock_refs[path]: path.read_bytes() for path in lock_paths}
    provider_environments = []
    for runtime_id in ('cpu', 'cuda', 'directml', 'rocm'):
        contract_path = ROOT / 'contracts/optional-runtimes' / (runtime_id + '.json')
        contract = json.loads(contract_path.read_text(encoding='utf-8'))
        lock_path = contract_path.with_name(contract['lock_file'])
        if sha(lock_path) != contract['lock_sha256']:
            raise RuntimeError('Provider lock does not match its pinned manifest: ' + runtime_id)
        manifest_ref = 'toolchains/providers/' + contract_path.name
        lock_ref = 'toolchains/providers/' + lock_path.name
        files[manifest_ref], files[lock_ref] = contract_path.read_bytes(), lock_path.read_bytes()
        for package in contract['packages']:
            if 'wheel_file' in package:
                wheel = contract_path.parent / package['wheel_file']
                if wheel.resolve().parent != (contract_path.parent / 'wheels').resolve() or sha(wheel) != package['sha256']:
                    raise RuntimeError('A provider source-built wheel differs from its admitted bytes')
                files['toolchains/providers/' + package['wheel_file']] = wheel.read_bytes()
                receipt = contract_path.parent / package['source_build_receipt']
                if receipt.resolve().parent != contract_path.parent.resolve():
                    raise RuntimeError('A provider build receipt escaped its source contract directory')
                if sha(receipt) != package.get('source_build_receipt_sha256'):
                    raise RuntimeError('A provider source-build receipt differs from its admitted hash')
                files['toolchains/providers/' + receipt.name] = receipt.read_bytes()
        provider_environments.append({'runtime_id': runtime_id, 'manifest': manifest_ref,
            'manifest_sha256': sha(contract_path), 'lock': lock_ref, 'lock_sha256': sha(lock_path),
            'python_version': contract['python_version'], 'system': contract['system'], 'machine': contract['machine'],
            'installation_state': 'not_verified', 'hardware_execution': 'not_verified',
            'operations': contract.get('operations', []),
            'qualification_requires': 'compatible_host_plus_exact_installed_record_plus_fresh_provider_self_test_and_operation_qualification',
            'automatic_driver_installation': False})
    native_path = PLUGIN / 'toolchains/native-tool-definitions.v4.json'
    native = json.loads(native_path.read_text(encoding='utf-8'))
    if (native.get('schema') != 'evidence-lane.native-tool-definitions.v4'
            or native.get('status') != 'CURRENT_RETAINED_ONLY'
            or len(native.get('tools', [])) != 12):
        raise RuntimeError('The retained native-tool definitions do not reconcile')
    native = {**native, 'schema': 'evidence-lane.native-toolchain-manifest.v4', 'version': 4,
        'source_definitions': 'toolchains/native-tool-definitions.v4.json',
        'source_definitions_sha256': sha(native_path), 'install_scope': 'SHARED_WINDOWS_STUDIO_BUNDLE',
        'acquisition_gate': 'PLUGIN_FIRST_DETECTION_AFTER_CANDIDATE_INSTALL',
        'network_acquisition_during_mcp_handshake': False, 'installation_qualification': 'pending',
        'installed_pointer': 'toolchains/native-installation.v4.json',
        'platform': 'windows-x86_64', 'studio_default_root': 'C:/Apps/EvidenceLaneStudio'}
    entries = []
    for tool in catalog['entries']:
        if tool['lifecycle'] != 'retained':
            continue
        identity = tool['tool_id']
        distribution = DISTRIBUTIONS.get(identity)
        requirements = ([] if identity in HTTP_ONLY_EXTERNAL_SERVICES
                        else EXTRA_DISTRIBUTIONS.get(identity, [distribution] if distribution else []))
        packages = []
        for distribution in requirements:
            matches = [(path, data[normalized(distribution)]) for path, data in locks if normalized(distribution) in data]
            if not matches:
                raise RuntimeError('Missing inherited package pin for ' + identity + ': ' + distribution)
            path, requirement = matches[0]
            if not requirement['hashes']:
                raise RuntimeError('Missing artifact hashes for ' + distribution)
            packages.append({**requirement, 'lock_path': lock_refs[path], 'lock_sha256': sha(path)})
        if tool['kind'] in {'external_service', 'mcp_tool_provider'}:
            route = 'packaged_adapter_and_explicit_external_service_configuration'
        elif tool['kind'] == 'internal_component' or identity in {'SQLite_CAS', 'SQLite_FTS5_BM25'}:
            route = 'packaged_engine_code_or_standard_library'
        elif identity in NATIVE:
            route = 'shared_native_manifest'
        elif identity in {'NodeJS_TypeScript', 'Mermaid_CLI_mmdc', 'NextJS_React_ThreeJS_FramerMotion'}:
            route = 'shared_node_runtime_and_locked_npm_assets'
        elif identity == 'Python':
            route = 'pinned_shared_engine_and_tool_interpreters'
        elif identity == 'Git':
            route = 'shared_portable_git'
        elif identity == 'PowerShell_Win32_APIs':
            route = 'windows_host_api_with_platform_verification'
        elif packages:
            route = 'shared_locked_python_environment'
        else:
            raise RuntimeError('No provisioning owner for ' + identity)
        entries.append({'tool_id': identity, 'kind': tool['kind'], 'provisioning_route': route,
            'packages': packages, 'native_manifest_tool': NATIVE.get(identity),
            'model_assets': MODEL_ASSETS.get(identity, []),
            'installation_required_for_windows_bundle': bool(packages) or (
                tool['kind'] not in {'external_service', 'mcp_tool_provider', 'internal_component'}
                and identity not in {'SQLite_CAS', 'SQLite_FTS5_BM25', 'PowerShell_Win32_APIs'}),
            'external_configuration_required': tool['kind'] in {'external_service', 'mcp_tool_provider'},
            'license_grant_required': identity == 'Ghostscript',
            'installation_scope': 'shared_once_all_projects', 'automatic_per_lane_installation': False,
            'installation_state': 'not_verified', 'adapter_execution_state': 'not_verified',
            'operation_selection_authority': 'registered_operation_tool_routes',
            'base_requirement': tool['requirement']})
    if len(entries) != catalog['counts']['retained'] or len({row['tool_id'] for row in entries}) != len(entries):
        raise RuntimeError('The retained tool matrix does not reconcile')
    body = {'schema_version': 4, 'retained_tool_count': len(entries), 'tool_catalog_sha256': sha(catalog_path),
        'full_bundle_ready': False, 'qualification': 'provisioning_inputs_and_routes_only',
        'shared_root': 'C:/Apps/EvidenceLaneStudio', 'override': 'EVIDENCE_LANE_STUDIO_ROOT',
        'windows_studio_only': True, 'mac_path': 'reduced_native_sdk_mcp_hooks_without_managed_bundle',
        'vm_path': 'explicit_engine_route_with_verified_durable_storage',
        'full_bundle_gate': 'all_required_local_tools_models_and_compatible_provider_environments_verified',
        'operation_selection_does_not_reduce_installation_scope': True,
        'source_locks': [{'path': lock_refs[path], 'sha256': sha(path)} for path in lock_paths],
        'resolved_installation_lock': {'path': 'toolchains/locks/runtime.lock.txt',
            'sha256': sha(PLUGIN / 'requirements.runtime.lock.txt'),
            'receipt': 'requirements.runtime-lock.v4.json',
            'receipt_sha256': sha(PLUGIN / 'requirements.runtime-lock.v4.json'),
            'package_count': len(lock_entries(PLUGIN / 'requirements.runtime.lock.txt')),
            'target': 'windows-amd64-cpython-3.14',
            'direct_network_locations_allowed_at_install': False},
        'provider_environments': provider_environments,
        'pin_qualification': 'inherited_pins_require_full_platform_resolution_before_actual_installation',
        'native_manifest': 'toolchains/native-tools.v4.json',
        'models': {
            'embedding_snapshot': {'repository': 'BAAI/bge-small-en-v1.5',
                'revision': '5c38ec7c405ec4b44b94cc5a9bb96e735b38267a', 'acquisition_owner': 'full_bundle_installer'},
            'parser_grammars': {'distribution': 'tree-sitter-language-pack', 'version': '1.14.3',
                'acquisition_owner': 'full_bundle_installer', 'auto_download_during_operation': False},
            'rapidocr_models': {'acquisition_owner': 'full_bundle_installer', 'file_hash_manifest_required': True},
            'tesseract_languages': {'acquisition_owner': 'full_bundle_installer', 'file_hash_manifest_required': True},
            'docling_models': {'acquisition_owner': 'full_bundle_installer', 'file_hash_manifest_required': True}},
        'entries': entries}
    for identity, filename in [('rapidocr_models', 'pdf-ocr-models.v4.json'),
            ('tesseract_languages', 'tesseract-models.v4.json'), ('docling_models', 'docling-models.v4.json')]:
        path = PLUGIN / 'toolchains' / filename
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest['asset_id'] != identity or not manifest['files'] or not manifest['no_runtime_downloads']:
            raise RuntimeError('The PDF model acquisition contract is incomplete: ' + identity)
        body['models'][identity].update(manifest='toolchains/' + filename, manifest_sha256=sha(path),
            no_runtime_downloads=True)
    return {**files, 'toolchains/shared-toolchain.v4.json': body,
            'toolchains/native-tools.v4.json': native}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    arguments = parser.parse_args()
    changed = []
    for relative, document in outputs().items():
        path = PLUGIN / relative
        data = document if isinstance(document, bytes) else (json.dumps(document, indent=2, ensure_ascii=False) + '\n').encode()
        if not path.is_file() or path.read_bytes() != data:
            changed.append(relative)
            if not arguments.check:
                path.parent.mkdir(exist_ok=True, parents=True)
                path.write_bytes(data)
    if arguments.check and changed:
        raise RuntimeError('Shared toolchain projections changed: ' + ', '.join(changed))
    print(json.dumps({'changed': changed, 'check': arguments.check, 'installation_claimed': False}))


if __name__ == '__main__':
    main()

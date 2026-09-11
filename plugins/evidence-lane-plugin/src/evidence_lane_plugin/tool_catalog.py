"""Complete tool declarations plus separately attributed runtime observations."""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from .errors import LaneError

OBSERVED_IDS = {'Git': 'git', 'MCP_Python_SDK': 'mcp', 'Pydantic': 'pydantic',
                'HTTPX': 'httpx', 'defusedxml': 'safe_xml', 'SQLite_FTS5_BM25': 'sqlite_fts5'}
DISTRIBUTIONS = {
    'HTTPX': 'httpx',
    'defusedxml': 'defusedxml',
    'ReportLab': 'reportlab',
    'OpenCV': 'opencv-python',
    'DOCX_OpenXML': 'python-docx',
    'APSW_SQLite_engine': 'apsw', 'Python_Graphviz_DOT_engine': 'graphviz',
    'LlamaIndex_SQLite_indexer': 'llama-index-core', 'LangGraph_Mermaid_engine': 'langgraph',
    'NodeJS_TypeScript': None, 'pytest': 'pytest', 'Ruff': 'ruff', 'MyPy': 'mypy',
    'openpyxl': 'openpyxl', 'pandas': 'pandas', 'python_calamine': 'python-calamine',
    'pyarrow': 'pyarrow', 'pypdf': 'pypdf', 'pypdfium2': 'pypdfium2', 'pdfplumber': 'pdfplumber',
    'Pillow': 'Pillow', 'PyMuPDF': 'PyMuPDF', 'Docling': 'docling', 'lxml': 'lxml',
    'BeautifulSoup4': 'beautifulsoup4', 'markdownify': 'markdownify', 'html2text': 'html2text',
    'trafilatura': 'trafilatura', 'DuckDB': 'duckdb', 'SQLAlchemy': 'sqlalchemy',
    'Tableau_Hyper_API': 'tableauhyperapi', 'LangChain': 'langchain',
    'SentenceTransformers': 'sentence-transformers', 'FAISS_CPU': 'faiss-cpu',
    'rank_bm25': 'rank-bm25', 'FastAPI': 'fastapi', 'Uvicorn': 'uvicorn',
    'Pydantic_Settings': 'pydantic-settings', 'python_multipart': 'python-multipart',
    'Requests': 'requests', 'aiofiles': 'aiofiles', 'orjson': 'orjson',
    'python_dotenv': 'python-dotenv', 'Tenacity': 'tenacity', 'DDGS': 'ddgs',
    'tldextract': 'tldextract', 'validators': 'validators', 'readability_lxml': 'readability-lxml',
    'LangSmith': 'langsmith', 'Langfuse': 'langfuse', 'Polars': 'polars',
    'TreeSitter_LanguagePack': 'tree-sitter-language-pack',
    'rustworkx': 'rustworkx', 'sqlite_vec': 'sqlite-vec', 'FastMCP': 'fastmcp',
    'OpenAI_Agents_SDK': 'openai-agents', 'GitPython': 'GitPython', 'PyGithub': 'PyGithub',
    'OpenTelemetry': 'opentelemetry-sdk', 'JSONSchema': 'jsonschema',
}


@lru_cache(maxsize=1)
def declarations() -> dict:
    path = Path(__file__).resolve().parents[2] / 'toolchains/tool-catalog.v4.json'
    if path.stat().st_size > 1_000_000:
        raise LaneError('TOOL_CATALOG_INVALID', 'The tool catalog exceeds its byte budget.')
    document = json.loads(path.read_text(encoding='utf-8'))
    entries = document['entries']
    if (document['schema_version'] != 4 or len(entries) != document['base_entry_count'] + document.get('additional_entry_count', 0)
            or len({item['tool_id'] for item in entries}) != len(entries)):
        raise LaneError('TOOL_CATALOG_INVALID', 'Tool identities and catalog counts do not reconcile.')
    return document


def installed_metadata() -> dict:
    """Read installed distribution metadata; do not import or execute tool packages."""
    versions: dict[str, str | None] = {}
    for tool, distribution in DISTRIBUTIONS.items():
        if distribution is None:
            continue
        try:
            versions[tool] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[tool] = None
    return versions


def opencv_observation() -> dict:
    """Each OpenCV flavor owns cv2; overlapping distributions cannot be ready."""
    versions = {}
    for name in ('opencv-python', 'opencv-python-headless',
                 'opencv-contrib-python', 'opencv-contrib-python-headless'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    if len(versions) > 1:
        reason = 'OPENCV_DISTRIBUTION_CONFLICT'
    elif not versions:
        reason = 'DEPENDENCY_UNAVAILABLE'
    elif DISTRIBUTIONS['OpenCV'] not in versions:
        reason = 'OPENCV_DISTRIBUTION_UNSUPPORTED'
    else:
        reason = 'PACKAGE_PRESENT'
    return {'ready': reason == 'PACKAGE_PRESENT', 'reason': reason, 'distributions': versions,
            'version': versions.get(DISTRIBUTIONS['OpenCV'] or ''), 'basis': 'distribution_metadata',
            'loaded_module_or_file_hash_attested': False}


def snapshot(inventory: dict, *, registry=None) -> dict:
    document = copy.deepcopy(declarations())
    observed = {item['name']: item for item in inventory.get('tools', [])}
    versions = installed_metadata()
    metadata_observed_at = datetime.now(UTC).isoformat()
    shared = shared_requirements()
    installation = {row['tool_id']: copy.deepcopy(row) for row in shared['entries']}
    bindings: dict[str, list[dict]] = {}
    if registry is not None:
        from .tool_routes import TOOL_ALIASES, declared_pipeline_tools, routes_for
        for action in registry.schemas():
            spec = registry.get(action['name'])
            for ordinal, route in enumerate(routes_for(spec), 1):
                schema = route.schema()
                for tool_id in declared_pipeline_tools(schema):
                    bindings.setdefault(TOOL_ALIASES.get(tool_id, tool_id), []).append({
                        'action': spec.name, 'profile': spec.profile, 'workflow': spec.workflow,
                        'route_id': route.route_id, 'route_ordinal': ordinal,
                        'adapter': schema['adapter'],
                        **({'condition': {'selected_view': route.view_refresh.view_id,
                            'dot_validation': 'native'}} if tool_id not in route.tool_ids else {}),
                        'execution_verified_by_catalog': False})
    package_versions: dict[str, str | None] = {}
    for item in document['entries']:
        observation = observed.get(OBSERVED_IDS.get(item['tool_id']))
        item['configuration_state'] = 'not_assessed'
        item['readiness'] = 'not_assessed'
        item['execution_state'] = 'not_verified'
        item['version'] = versions.get(item['tool_id'])
        item['observation_basis'] = 'catalog_declaration_only'
        item['availability_reason'] = 'NO_RUNTIME_OBSERVATION_FOR_DECLARED_COMPONENT'
        item['installation_requirement'] = installation.get(item['tool_id'])
        item['operation_bindings'] = copy.deepcopy(bindings.get(item['tool_id'], []))
        item['operation_binding_basis'] = ('current_action_registry' if item['operation_bindings'] else
            'no_direct_operation_binding' if registry is not None else 'registry_not_inspected')
        item['package_pin_observations'] = []
        for package in (item['installation_requirement'] or {}).get('packages', []):
            distribution = package['distribution']
            if distribution not in package_versions:
                try:
                    package_versions[distribution] = importlib.metadata.version(distribution)
                except importlib.metadata.PackageNotFoundError:
                    package_versions[distribution] = None
            version = package_versions[distribution]
            item['package_pin_observations'].append({'distribution': distribution,
                'required_version': package['version'], 'observed_version': version,
                'state': 'matches_pin' if version == package['version'] else 'not_detected' if version is None else 'differs_from_pin',
                'basis': 'engine_environment_distribution_metadata',
                'loaded_module_or_file_hash_attested': False})
        if item['lifecycle'] == 'retired':
            item['readiness'] = 'retired'
            item['availability_reason'] = 'RETIRED_BY_CURRENT_PRODUCT_CONTRACT'
        elif observation:
            item['readiness'] = observation['state']
            item['execution_state'] = observation.get('execution_state', 'not_verified')
            item['observation_basis'] = 'engine_capability_observation'
            item['availability_reason'] = observation.get('reason', 'SEE_ENGINE_CAPABILITY_OBSERVATION')
            item['capability_observation'] = copy.deepcopy(observation)
        elif item['kind'] in {'external_service', 'mcp_tool_provider'}:
            item['readiness'] = 'connection_unverified'
            item['availability_reason'] = 'PROJECT_SCOPED_CONNECTION_AND_GRANT_NOT_ASSESSED'
        elif item['tool_id'] in versions:
            item['readiness'] = 'package_present' if item['version'] is not None else 'package_not_detected'
            item['observation_basis'] = 'engine_environment_distribution_metadata_only'
            item['availability_reason'] = 'PACKAGE_PRESENT_OPERATION_NOT_VERIFIED' if item['version'] else 'PACKAGE_NOT_DETECTED_IN_ENGINE_ENVIRONMENT'
        if item['tool_id'] == 'OpenCV' and item['lifecycle'] == 'retained':
            opencv = opencv_observation()
            item['opencv_distribution_observation'] = opencv
            if not opencv['ready']:
                item['readiness'] = {'OPENCV_DISTRIBUTION_CONFLICT': 'package_conflict',
                    'OPENCV_DISTRIBUTION_UNSUPPORTED': 'package_unsupported'}.get(opencv['reason'], 'package_not_detected')
                item['availability_reason'] = opencv['reason']
                item['observation_basis'] = 'engine_environment_distribution_metadata_only'
    document['observed_at'] = inventory.get('observed_at')
    document['metadata_observed_at'] = metadata_observed_at
    document['metadata_scope'] = 'engine_environment; isolated provider packages are not inferred from this lookup'
    document['readiness_counts'] = dict(Counter(item['readiness'] for item in document['entries']))
    document['availability_is_not_execution'] = True
    document['shared_bundle_ready'] = False
    document['shared_bundle_readiness_basis'] = 'no_complete_installation_receipt_checked_by_this_catalog'
    document['operation_binding_scope'] = 'direct_registered_routes_only; internal, transport, build and provisioning use is separate'
    return document


@lru_cache(maxsize=1)
def shared_requirements() -> dict:
    """Bounded installation inputs shared by all projects; never a readiness claim."""
    root = Path(__file__).resolve().parents[2] / 'toolchains'
    path = root / 'shared-toolchain.v4.json'
    with path.open('rb') as stream:
        raw = stream.read(2_097_153)
    if len(raw) > 2_097_152:
        raise LaneError('SHARED_TOOLCHAIN_INVALID', 'The shared toolchain exceeds its metadata budget.')
    value = json.loads(raw)
    catalog = declarations()
    retained = {row['tool_id'] for row in catalog['entries'] if row['lifecycle'] == 'retained'}
    if (value['schema_version'] != 4 or value['retained_tool_count'] != len(retained)
            or len(value['entries']) != len(retained)
            or {row['tool_id'] for row in value['entries']} != retained
            or value['tool_catalog_sha256'] != hashlib.sha256((root / 'tool-catalog.v4.json').read_bytes()).hexdigest()
            or value['full_bundle_ready'] is not False):
        raise LaneError('SHARED_TOOLCHAIN_INVALID', 'The shared installation inputs do not match the retained catalog.')
    return value

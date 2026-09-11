"""Operation-owned shared tool routes and attributed adapter execution.

Adapters are registered by trusted plugin code. Clients cannot submit Python,
argv, module paths or a replacement adapter. Installation, readiness, execution
and acceptance are separate facts. Fallback is a pre-invocation choice only.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import marshal
import platform
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .errors import LaneError

TOOL_ALIASES = {'hashlib': 'hashlib_pathlib', 'sqlite3': 'SQLite_CAS', 'git': 'Git'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ToolRoute:
    route_id: str
    handler: Callable
    tool_ids: tuple[str, ...] = ('Python',)
    systems: tuple[str, ...] = ('Windows', 'Darwin', 'Linux')
    fidelity: str = 'exact_contract'
    provider: str = 'engine_cpu'
    reason: str = 'Registered engine adapter'
    extension: object | None = None
    argument_values: tuple[tuple[str, tuple[str, ...]], ...] = ()
    argument_suffixes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    applicable: Callable | None = None
    worker_operations: tuple[str, ...] | None = None
    compute: object | None = None
    host_profiles: tuple[str, ...] = ()
    view_refresh: object | None = None

    def schema(self):
        return {'route_id': self.route_id, 'adapter': self.handler.__module__ + ':' + self.handler.__qualname__,
                'tool_ids': list(self.tool_ids), 'systems': list(self.systems),
                'fidelity': self.fidelity, 'provider': self.provider, 'reason': self.reason,
                'host_profiles': list(self.host_profiles),
                'extension': self.extension.schema() if self.extension else None,
                'argument_values': {name: list(values) for name, values in self.argument_values},
                'argument_suffixes': {name: list(values) for name, values in self.argument_suffixes},
                **({'applicable': self.applicable.__module__ + ':' + self.applicable.__qualname__}
                   if self.applicable else {}),
                **({'worker_operations': list(self.worker_operations)} if self.worker_operations is not None else {}),
                **({'compute': self.compute.schema()} if self.compute is not None else {}),
                **({'view_refresh': self.view_refresh.schema()} if self.view_refresh is not None else {})}


def routes_for(spec):
    # This is an operation binding, never an implicit lane-wide tool bundle.
    return spec.tool_routes or (ToolRoute(spec.name + '.engine', spec.handler,
        tuple(dict.fromkeys(('Python', *spec.required_tools)))),)


def supports_arguments(route, context, arguments):
    """The shared input predicate, without readiness or execution claims."""
    if arguments is None and (route.argument_values or route.argument_suffixes or route.applicable):
        return False
    supported = all(arguments is None or getattr(arguments, name) in values for name, values in route.argument_values)
    supported &= all(arguments is None or Path(str(getattr(arguments, name))).suffix.lower() in suffixes
                     for name, suffixes in route.argument_suffixes)
    if route.applicable is not None:
        applicable = route.applicable(context, arguments) if arguments is not None else False
        if type(applicable) is not bool:
            raise LaneError('TOOL_ROUTE_INVALID', 'The registered context selector must return a boolean.')
        supported &= applicable
    return supported


def operation_contract(spec):
    routes = [route.schema() for route in routes_for(spec)]
    body = {'operation': spec.name, 'profile': spec.profile, 'routes': routes,
            'installation_matrix': 'toolchains/shared-toolchain.v4.json',
            'selection': 'ordered_pre_invocation_readiness_and_exact_fidelity',
            'fallback_after_invocation': False,
            'no_fallback_reason': None if len(routes) > 1 else 'No equivalent alternate adapter is registered.',
            'worker_operations': list(spec.worker_operations), 'installation_implied': False}
    return {**body, 'contract_digest': _digest(body)}


def declared_pipeline_tools(route_schema):
    """All declared tools, including conditions; this is not a ready pipeline."""
    conditional = route_schema.get('view_refresh', {}).get('native_dot', {}).get('tool_ids', [])
    return tuple(dict.fromkeys((*route_schema['tool_ids'], *conditional)))


def validate_routes(spec):
    from .host_routing import HOST_MATRIX
    from .tool_catalog import declarations

    retained = {row['tool_id'] for row in declarations()['entries'] if row['lifecycle'] == 'retained'}
    external = {row['tool_id'] for row in declarations()['entries'] if row['kind'] == 'external_service'}
    routes = routes_for(spec)
    if not 1 <= len(routes) <= 8 or len({row.route_id for row in routes}) != len(routes):
        raise LaneError('TOOL_ROUTE_INVALID', 'Register one to eight distinct ordered routes per operation.')
    for route in routes:
        if (len(set(route.host_profiles)) != len(route.host_profiles)
                or not set(route.host_profiles) <= HOST_MATRIX.keys()
                or not {TOOL_ALIASES.get(tool, tool) for tool in route.tool_ids} <= retained):
            raise LaneError('TOOL_ROUTE_INVALID', 'Routes require retained tool identities and supported host profiles.')
        if route.applicable is not None and (not callable(route.applicable) or not hasattr(route.applicable, '__code__')):
            raise LaneError('TOOL_ROUTE_INVALID', 'Context selection must be a registered plugin function.')
        if route.worker_operations is not None and (len(set(route.worker_operations)) != len(route.worker_operations)
                or not set(route.worker_operations) <= set(spec.worker_operations)):
            raise LaneError('TOOL_ROUTE_INVALID', 'A route may select only the action-owned workers.')
        if (len(route.argument_suffixes) > 4 or len({name for name, _ in route.argument_suffixes}) != len(route.argument_suffixes)
                or any(name not in spec.input_model.model_fields or not values or len(values) > 32
                    or any(not isinstance(value, str) or not value.startswith('.') or not value[1:].isalnum()
                        or value.lower() != value or len(value) > 20 for value in values)
                    for name, values in route.argument_suffixes)):
            raise LaneError('TOOL_ROUTE_INVALID', 'Filename routes require bounded lowercase suffixes on declared inputs.')
        if (len(route.argument_values) > 8 or len({name for name, _ in route.argument_values}) != len(route.argument_values)
                or any(name not in spec.input_model.model_fields or not values or len(values) > 32 or
                       any(not isinstance(value, str) or len(value) > 100 for value in values)
                       for name, values in route.argument_values)):
            raise LaneError('TOOL_ROUTE_INVALID', 'Input-specific routes require bounded declared input values.')
        if (not route.route_id or len(route.route_id) > 128 or not callable(route.handler)
                or not hasattr(route.handler, '__code__')
                or not 1 <= len(route.tool_ids) <= 32 or len(set(route.tool_ids)) != len(route.tool_ids)
                or not set(route.systems) <= {'Windows', 'Darwin', 'Linux'} or not route.systems
                or route.fidelity != 'exact_contract' or route.provider != 'engine_cpu'):
            raise LaneError('TOOL_ROUTE_INVALID', 'Adapters require explicit tools, platforms and exact output fidelity.')
        if route.extension is not None:
            from .extension_routes import ExtensionBinding
            if not isinstance(route.extension, ExtensionBinding):
                raise LaneError('EXTENSION_BINDING_INVALID', 'Register a typed extension adapter binding.')
            route.extension.validate(spec)
        if {TOOL_ALIASES.get(tool, tool) for tool in route.tool_ids} & external and route.extension is None:
            raise LaneError('EXTENSION_BINDING_INVALID', 'External service tools require an exact project connector binding.')
        if route.compute is not None:
            from .compute_routes import ComputeContract
            if not isinstance(route.compute, ComputeContract) or route.extension is not None:
                raise LaneError('COMPUTE_CONTRACT_INVALID', 'Core compute belongs to a typed engine adapter contract.')
            route.compute.validate(spec, route)
        if route.view_refresh is not None:
            import re

            from .artifact_contract import VIEW_ID, SelectedViewRefresh
            if (not isinstance(route.view_refresh, SelectedViewRefresh)
                    or not re.fullmatch(VIEW_ID, route.view_refresh.view_id)
                    or not spec.requires_delta or not spec.mutates
                    or 'render_lane_view' not in spec.worker_operations):
                raise LaneError('VIEW_REFRESH_BINDING_INVALID', 'Selected export refresh belongs to its source-changing action and declared worker.')
    if routes[0].handler is not spec.handler:
        raise LaneError('TOOL_ROUTE_INVALID', 'The primary route must bind the registered operation handler.')


class ToolRouter:
    def __init__(self, *, observer=None, system=None):
        self.observer = observer or self.observe
        self.system = system or platform.system
        self.extensions = None
        self.compute = None

    @staticmethod
    def observe(tool_id, context):
        """Inspect dependencies without importing a tool or contacting a service."""
        from .tool_catalog import DISTRIBUTIONS, declarations
        identity = TOOL_ALIASES.get(tool_id, tool_id)
        entries = {row['tool_id']: row for row in declarations()['entries']}
        entry = entries.get(identity)
        evidence = {'tool_id': identity, 'requested_id': tool_id, 'ready': False,
                    'basis': 'unresolved', 'tool_executed': False}
        if entry is None or entry['lifecycle'] != 'retained':
            return {**evidence, 'reason': 'TOOL_NOT_RETAINED'}
        if entry['kind'] == 'external_service':
            return {**evidence, 'ready': True, 'basis': 'registered_extension_precondition',
                    'reason': 'EXACT_PROJECT_GRANT_AND_SERVICE_READINESS_REQUIRED'}
        if identity == 'Python':
            return {**evidence, 'ready': True, 'basis': 'current_interpreter',
                    'version': platform.python_version(), 'reason': 'INTERPRETER_RUNNING'}
        if identity == 'ENV_UOP_classifier':
            from .flash_authority import SessionFlashAuthority
            try:
                status = SessionFlashAuthority().verify()
                return {**evidence, 'ready': True, 'basis': 'verified_current_operating_policy',
                        'manifest_digest': status.manifest_digest, 'reason': 'LOCKED_POLICY_AVAILABLE'}
            except LaneError as error:
                return {**evidence, 'ready': False, 'basis': 'locked_operating_policy_check', 'reason': error.code}
        if identity in {'PowerBI_TOM', 'PBIXRay'}:
            from .shared_tool_assets import resolve_shared_asset
            asset_id, executable, version = ('powerbi_tom_runtime', 'evidence-lane-powerbi.exe', '19.114.12') if identity == 'PowerBI_TOM' else ('powerbi_pbix_runtime', 'python.exe', '0.15.5')
            try:
                folder, asset = resolve_shared_asset(asset_id)
                present = (folder / executable).is_file() and (identity != 'PBIXRay' or (folder / 'powerbi_pbix_child.py').is_file())
                return {**evidence, 'ready': present, 'basis': 'verified_shared_asset_files', 'version': version,
                    'asset_id': asset_id, 'files_sha256': asset['files_sha256'], 'loaded_runtime_attested': False,
                    'reason': 'ASSET_FILES_VERIFIED' if present else 'ASSET_ENTRYPOINT_MISSING'}
            except LaneError as error:
                return {**evidence, 'basis': 'shared_asset_verification', 'reason': error.code}
        if identity in {'Poppler_pdftotext_pdfinfo', 'FFmpeg'}:
            from .shared_native_tools import try_resolve_native_tool
            from .shared_tool_assets import resolve_shared_asset
            try:
                _, asset = resolve_shared_asset('ffmpeg_runtime' if identity == 'FFmpeg' else 'poppler_runtime')
                present = all(try_resolve_native_tool(name) is not None for name in (('ffmpeg',) if identity == 'FFmpeg' else ('poppler_pdfinfo', 'poppler_pdftotext')))
                return {**evidence, 'ready': present, 'basis': 'verified_runtime_and_binaries',
                    'files_sha256': asset['files_sha256'], 'loaded_runtime_attested': False,
                    'reason': 'ASSET_FILES_VERIFIED' if present else 'ASSET_ENTRYPOINT_MISSING'}
            except LaneError as error:
                return {**evidence, 'reason': error.code, 'basis': 'shared_asset_verification'}
        if identity in {'RapidOCR_ONNX_Runtime', 'pytesseract_Tesseract'}:
            from .pdf_ocr import MODEL_NAMES
            from .shared_native_tools import try_resolve_native_tool
            from .shared_tool_assets import resolve_shared_asset
            try:
                distributions = ('rapidocr', 'onnxruntime') if identity == 'RapidOCR_ONNX_Runtime' else ('pytesseract',)
                versions = {name: importlib.metadata.version(name) for name in distributions}
                _, asset = resolve_shared_asset('rapidocr_models' if identity == 'RapidOCR_ONNX_Runtime' else 'tesseract_languages')
                names = {row['path'] for row in asset['files']}
                if identity == 'pytesseract_Tesseract':
                    resolve_shared_asset('tesseract_runtime')
                present = set(MODEL_NAMES.values()) <= names if identity == 'RapidOCR_ONNX_Runtime' else try_resolve_native_tool('tesseract') is not None
                return {**evidence, 'ready': present, 'basis': 'package_metadata_and_verified_model_files',
                    'versions': versions, 'models_sha256': asset['files_sha256'], 'loaded_runtime_attested': False,
                    'reason': 'DEPENDENCY_AND_ASSET_FILES_PRESENT' if present else 'OCR_ASSETS_INCOMPLETE'}
            except (LaneError, OSError, ValueError, KeyError, TypeError, importlib.metadata.PackageNotFoundError):
                return {**evidence, 'reason': 'OCR_ASSETS_UNAVAILABLE', 'basis': 'required_package_model_and_binary_files'}
        if identity in {'hashlib_pathlib', 'SQLite_CAS', 'SQLite_immutable_URI_reader'}:
            return {**evidence, 'ready': True, 'basis': 'standard_library', 'reason': 'BUILTIN_DEPENDENCY'}
        if identity == 'Python_structural_parser':
            return {**evidence, 'ready': True, 'basis': 'standard_library_ast', 'reason': 'BUILTIN_DEPENDENCY'}
        if identity == 'PPTX_OpenXML':
            files = [Path(__file__).with_name(name) for name in ('presentation_parsers.py', 'presentation_authoring.py')]
            present = all(path.is_file() for path in files)
            return {**evidence, 'ready': present, 'basis': 'native_openxml_component_files',
                'source_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.is_file()},
                'loaded_runtime_attested': False, 'reason': 'COMPONENT_PRESENT' if present else 'COMPONENT_UNAVAILABLE'}
        if identity == 'SQLite_FTS5_BM25':
            from .runtime_health import _fts5_available
            ready = _fts5_available()
            return {**evidence, 'ready': ready, 'basis': 'bounded_in_memory_fts5_self_test',
                    'reason': 'FTS5_SELF_TEST_PASSED' if ready else 'FTS5_UNAVAILABLE'}
        if identity == 'Git':
            from .git_adapter import try_resolve_git_executable
            executable = try_resolve_git_executable()
            return {**evidence, 'ready': executable is not None, 'basis': 'absolute_external_executable_lookup',
                    'reason': 'EXECUTABLE_PRESENT' if executable else 'TOOL_UNAVAILABLE'}
        if identity == 'LibreOffice':
            from .shared_native_tools import try_resolve_native_tool
            resolved = try_resolve_native_tool('libreoffice')
            return {**evidence, 'ready': resolved is not None, 'basis': 'hash_verified_shared_executable',
                'version': resolved.version if resolved else None,
                'reason': 'EXECUTABLE_PRESENT_FULL_ASSET_CHECK_AT_WORKER_ENTRY' if resolved else 'TOOL_UNAVAILABLE'}
        if identity == 'Graphviz_dot':
            from .shared_native_tools import try_resolve_native_tool
            resolved = try_resolve_native_tool('graphviz')
            return {**evidence, 'ready': resolved is not None, 'basis': 'hash_verified_shared_executable',
                'version': resolved.version if resolved else None,
                'executable_sha256': resolved.executable_sha256 if resolved else None,
                'reason': 'EXECUTABLE_VERIFIED' if resolved else 'TOOL_UNAVAILABLE'}
        distribution = DISTRIBUTIONS.get(identity)
        if identity == 'OpenCV':
            from .tool_catalog import opencv_observation
            return {**evidence, **opencv_observation()}
        if distribution:
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                return {**evidence, 'reason': 'DEPENDENCY_UNAVAILABLE', 'basis': 'distribution_metadata'}
            if identity in {'TreeSitter_LanguagePack', 'SentenceTransformers'}:
                from .shared_tool_assets import resolve_shared_asset
                asset_id = 'parser_grammars' if identity == 'TreeSitter_LanguagePack' else 'embedding_snapshot'
                try:
                    _, asset = resolve_shared_asset(asset_id)
                except (LaneError, OSError, ValueError, KeyError, TypeError):
                    return {**evidence, 'reason': 'SHARED_ASSET_UNAVAILABLE',
                            'basis': 'required_shared_asset_manifest_and_file_hashes', 'version': version}
                return {**evidence, 'ready': True, 'basis': 'distribution_metadata_and_verified_asset_files',
                        'version': version, 'asset_manifest_sha256': asset['installation_manifest_sha256'],
                        'reason': 'DEPENDENCY_AND_ASSET_FILES_PRESENT'}
            return {**evidence, 'ready': True, 'basis': 'distribution_metadata',
                    'version': version, 'reason': 'PACKAGE_PRESENT'}
        return {**evidence, 'reason': 'ADAPTER_READINESS_NOT_CONFIGURED'}

    def resolve(self, spec, context, *, permitted_tools=None, arguments=None):
        contract = operation_contract(spec)
        attempts = []
        system = self.system()
        host = context.host_observation
        configured_profile = host.client.configured_profile if host is not None else None
        selection_context = {'project_id': context.project_id, 'client_id': context.client_id,
            'operation_profile': spec.profile, 'workflow': spec.workflow,
            'engine_operating_system': system, 'configured_host_profile': configured_profile,
            'host_observation_id': host.observation_id if host is not None else None,
            'host_profile_basis': host.client_evidence_basis if host is not None else 'not_observed',
            'host_profile_attestation': 'unavailable', 'native_task_attestation': 'unavailable',
            'arguments_sha256': _digest(arguments.model_dump(mode='json')) if arguments is not None else None}
        if permitted_tools is None and context.execution is not None and context.execution.guard is not None:
            permitted_tools = context.execution.guard.task.permitted_tools
        for route in routes_for(spec):
            input_supported = supports_arguments(route, context, arguments)
            view_refresh = route.view_refresh.resolve(context) if input_supported and route.view_refresh is not None else None
            tool_ids = tuple(dict.fromkeys((*route.tool_ids, *(view_refresh['tool_ids'] if view_refresh else ()))))
            compatible = system in route.systems
            host_compatible = not route.host_profiles or configured_profile in route.host_profiles
            if view_refresh is not None:
                compatible &= not view_refresh['native_dot'] or system == 'Windows'
                host_compatible &= view_refresh['host_compatible']
            scoped = permitted_tools is None or {TOOL_ALIASES.get(tool, tool) for tool in tool_ids} <= {
                'Python', *(TOOL_ALIASES.get(tool, tool) for tool in permitted_tools)}
            observations = [self.observer(identity, context) for identity in tool_ids] if compatible and host_compatible and scoped and input_supported else []
            ready = compatible and host_compatible and scoped and input_supported and all(row.get('ready') is True for row in observations)
            extension = None
            if ready and route.extension is not None:
                extension = self.extensions.resolve(spec, route, context, arguments) if self.extensions else {
                    'ready': False, 'reason': 'EXTENSION_ROUTER_UNAVAILABLE'}
                ready = extension['ready']
            attempts.append({'route_id': route.route_id, 'ready': ready,
                             'input_supported': input_supported,
                             'reason': 'READY_FOR_INVOCATION' if ready else
                             extension['reason'] if extension is not None else
                             'HOST_PLATFORM_UNSUPPORTED' if not compatible else
                             'HOST_PROFILE_UNSUPPORTED' if not host_compatible else
                             'OPERATION_ARGUMENTS_REQUIRED' if not input_supported and arguments is None else
                             'INPUT_VALUE_UNSUPPORTED' if not input_supported else
                             'DELTA_TOOL_SCOPE' if not scoped else 'DEPENDENCIES_UNAVAILABLE',
                             'conditions': {'platform': compatible, 'host_profile': host_compatible,
                                'task_tool_scope': scoped, 'input': input_supported},
                             'observations': observations, 'extension': extension, 'adapter_invoked': False,
                             **({'view_refresh': view_refresh} if view_refresh is not None else {})})
            if ready:
                selection = {'operation': spec.name, 'contract_digest': contract['contract_digest'],
                        'selected_route': route.route_id, 'selection_basis': 'pre_invocation_observations',
                        'fallback_used': any(row['input_supported'] for row in attempts[:-1]), 'attempts': attempts,
                        'context': selection_context,
                        'installation_verified': False, 'execution_authorized': False}
                if route.compute is not None:
                    if self.compute is None:
                        raise LaneError('COMPUTE_ROUTER_UNAVAILABLE', 'The registered compute adapter needs its owning engine.')
                    selection['compute'] = self.compute.select(route.compute, context)
                return selection
        return {'operation': spec.name, 'contract_digest': contract['contract_digest'],
                'selected_route': None, 'selection_basis': 'no_ready_equivalent_adapter',
                'fallback_used': False, 'attempts': attempts,
                'context': selection_context,
                'installation_verified': False, 'execution_authorized': False}

    def invoke(self, spec, context, arguments, *, control_plane=None):
        selection = self.resolve(spec, context, arguments=arguments)
        if selection['selected_route'] is None:
            raise LaneError('TOOL_ROUTE_UNAVAILABLE', 'No compatible operation adapter is ready.', details=selection)
        route = next(row for row in routes_for(spec) if row.route_id == selection['selected_route'])
        binding = admission_binding(selection)
        if context.tool_admission is not None and context.tool_admission != binding:
            raise LaneError('TOOL_ADMISSION_CHANGED', 'The admitted adapter or exact extension grant changed before invocation.')
        # Recheck revocable authorization after readiness checks and before effects.
        if context.authorize:
            context.authorize(spec.permission)
        if context.execution is not None and context.execution.guard is not None:
            context.execution.guard.check()
        computation = None
        if route.compute is not None:
            computation = self.compute.invocation(route.compute, context, selection['compute'])
            computation.check()
            context = replace(context, computation=computation)
            if context.execution is not None and context.execution.guard is not None:
                context.execution.guard.compute_checks.append(computation.check)
        extension_proof = binding['extension']
        if route.extension:
            check = lambda: self.extensions.authorize(spec, route, context, arguments, extension_proof)
            check()
            # Every subsequent owned worker/effect boundary rechecks expiry and revocation.
            context.execution.guard.extension_checks.append(check)
        governance = control_plane.admit(spec, context, arguments, selection) if control_plane is not None else None
        started = time.monotonic()
        source = inspect.getsourcefile(route.handler)
        source_digest = hashlib.sha256(Path(source).read_bytes()).hexdigest() if source and Path(source).is_file() else None
        receipt = {'schema_version': 4, 'operation': spec.name, 'request_id': context.request_id,
                   'project_id': context.project_id, 'client_id': context.client_id,
                   'plan_revision': context.expected_revision,
                   'selection': selection, 'adapter': route.schema()['adapter'],
                   'adapter_source_sha256': source_digest, 'python_version': platform.python_version(),
                   'adapter_code_sha256': hashlib.sha256(marshal.dumps(route.handler.__code__)).hexdigest(),
                   'source_observation_basis': 'filesystem_bytes_not_loaded_module_attestation',
                   'interpreter_platform': sys.platform, 'observed_at': datetime.now(UTC).isoformat(),
                   'input_digest': _digest(arguments.model_dump(mode='json')),
                   'extension_binding': extension_proof,
                   'env_uop': governance,
                   'adapter_invoked': False, 'dependency_execution_claimed': False,
                   'native_host_tool_attested': False, 'automatic_retry': False}
        try:
            if route.extension:
                self.extensions.record(context, extension_proof, receipt, 'execution_prepared')
                check()
            receipt['adapter_invoked'] = True
            value = route.handler(context, arguments)
            if computation is not None:
                computation.check()
            # Common output validation prevents an alternate adapter weakening the contract.
            from pydantic import ValidationError
            try:
                result = spec.output_model.model_validate(value).model_dump(mode='json')
            except ValidationError:
                raise LaneError('INVALID_RESULT', 'The handler violated its output contract.') from None
            if route.extension:
                self.extensions.validate_output(route, result)
                check()
            if control_plane is not None:
                control_plane.validate_result(result)
            output_digest = _digest(result)
        except LaneError as error:
            receipt.update({'outcome': 'failed', 'error_code': error.code,
                            'elapsed_seconds': round(time.monotonic() - started, 6)})
            error.details['tool_execution'] = {**receipt, 'receipt_digest': _digest(receipt)}
            if route.extension:
                self.extensions.record(context, extension_proof, receipt, 'execution_failed')
            raise
        except Exception:  # noqa: BLE001 - vendor errors cannot disclose arguments or trigger a fallback
            receipt.update({'outcome': 'failed', 'error_code': 'TOOL_ADAPTER_FAILED',
                            'elapsed_seconds': round(time.monotonic() - started, 6)})
            if route.extension:
                self.extensions.record(context, extension_proof, receipt, 'execution_failed')
            raise LaneError('TOOL_ADAPTER_FAILED', 'The selected adapter failed; inspect its owning operation.',
                details={'tool_execution': {**receipt, 'receipt_digest': _digest(receipt)}}) from None
        receipt.update({'outcome': 'returned_validated_result', 'output_digest': output_digest,
                        'elapsed_seconds': round(time.monotonic() - started, 6)})
        if route.extension:
            self.extensions.record(context, extension_proof, receipt, 'execution_returned')
        return result, {**receipt, 'receipt_digest': _digest(receipt)}


def admission_binding(selection):
    selected = next((row for row in selection['attempts'] if row['route_id'] == selection['selected_route']), None)
    result = {'contract_digest': selection['contract_digest'], 'route_id': selection['selected_route'],
            'extension': selected['extension']['proof'] if selected and selected.get('extension') else None}
    if selected and 'view_refresh' in selected:
        result['view_refresh'] = selected['view_refresh']
    if 'compute' in selection:
        from .compute_routes import binding
        result['compute'] = binding(selection['compute'])
    return result


def register_toolchain_actions(engine):
    from pydantic import Field

    from .registry import ActionSpec, Contract
    from .tool_catalog import snapshot

    class CatalogRequest(Contract):
        pass

    class CatalogResult(Contract):
        tools: dict
        operations: list[dict]
        installation_scope: str = 'shared_windows_studio_bundle_once_per_installation'

    class ResolveRequest(Contract):
        action: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
        arguments: dict | None = None

    class ResolveResult(Contract):
        resolution: dict

    def catalog(context, request):
        return CatalogResult(tools=snapshot(engine.capabilities.snapshot(), registry=engine.registry), operations=[
            operation_contract(engine.registry.get(row['name'])) for row in engine.registry.schemas()])

    def resolve(context, request):
        spec = engine.registry.get(request.action)
        if context.allowed_actions is not None and spec.name not in context.allowed_actions:
            raise LaneError('ACTION_SCOPE_DENIED', 'The connection does not grant the selected operation.')
        if spec.permission not in context.permissions:
            raise LaneError('PERMISSION_DENIED', 'This connection lacks the selected operation permission.')
        if context.authorize:
            context.authorize(spec.permission)
        if spec.project_required and context.project_id is None:
            raise LaneError('PROJECT_REQUIRED', 'Select the operation project before resolving its route.')
        arguments = engine.registry.validate(spec.name, request.arguments, context) if request.arguments is not None else None
        return ResolveResult(resolution=engine.registry.tool_router.resolve(spec, context, arguments=arguments))

    engine.registry.register(ActionSpec('toolchain_catalog', 'Read shared tool declarations and executable operation routes; installation and execution remain separately qualified.',
        CatalogRequest, CatalogResult, catalog, project_required=False, workflow='toolchain'))
    engine.registry.register(ActionSpec('toolchain_resolve', 'Inspect ordered compatible routes for an authorized operation without invoking or installing tools.',
        ResolveRequest, ResolveResult, resolve, project_required=False, workflow='toolchain'))

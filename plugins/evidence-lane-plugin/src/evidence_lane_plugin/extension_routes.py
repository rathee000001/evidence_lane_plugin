"""Exact project grants for trusted, operation-owned optional adapters.

An adapter binding is executable plugin code, not configuration-supplied code.
The configured host profile is routing metadata, never native host attestation.
Core tools and MCP transport frameworks do not become optional extensions.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path

from .connector_governance import connector_service, normalize_role_schema
from .errors import LaneError


@dataclass(frozen=True)
class ExtensionBinding:
    backend_id: str
    backend_version: str
    backend_runtime: str
    capability: str
    lane: str
    role: str
    role_schema: tuple[tuple[str, str], ...]
    readiness: Callable = field(repr=False, compare=False)
    read_path_fields: tuple[str, ...] = ()
    write_path_fields: tuple[str, ...] = ()
    resource_fields: tuple[str, ...] = ()
    plugin_id_field: str | None = None
    lane_field: str | None = None

    def validate(self, spec):
        from .lanes import CANONICAL_LANE_IDS
        if (not re.fullmatch(r'[a-z][a-z0-9_.-]{2,127}', self.backend_id)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}', self.backend_version)
                or self.backend_runtime not in {'python', 'java', 'kotlin', 'go', 'rust', 'cpp', 'external_mcp'}
                or (self.lane_field is None and self.lane not in CANONICAL_LANE_IDS)
                or (self.lane_field is not None and self.lane != 'selected_by_action')
                or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', self.capability)
                or not re.fullmatch(r'[a-z][a-z0-9_-]{2,63}', self.role)
                or not callable(self.readiness) or not hasattr(self.readiness, '__code__')
                or not spec.requires_delta or spec.studio_read):
            raise LaneError('EXTENSION_BINDING_INVALID', 'Optional adapters require a registered Delta contract and readiness probe.')
        try:
            schema = normalize_role_schema(dict(self.role_schema))
        except (ValueError, TypeError):
            raise LaneError('EXTENSION_BINDING_INVALID', 'The adapter needs an explicit output role schema.') from None
        fields = self.read_path_fields + self.write_path_fields + self.resource_fields
        if (len(schema) != len(self.role_schema) or set(schema) != set(spec.output_model.model_fields)
                or len(fields) != len(set(fields))
                or not set(fields) <= set(spec.input_model.model_fields)
                or not set(self.read_path_fields + self.write_path_fields) <= set(spec.path_fields)
                or (self.plugin_id_field is not None and self.plugin_id_field not in spec.input_model.model_fields)
                or (self.lane_field is not None and self.lane_field not in spec.input_model.model_fields)
                or (spec.permission in {'write', 'publish'} and not self.write_path_fields and not self.resource_fields)):
            raise LaneError('EXTENSION_BINDING_INVALID', 'Adapter scope fields and output roles must match the operation contract.')

    def schema(self):
        body = {item.name: getattr(self, item.name) for item in fields(self) if item.name != 'readiness'}
        body['role_schema'] = dict(self.role_schema)
        body = {key: list(value) if isinstance(value, tuple) else value for key, value in body.items()}
        return body | {'execution_owner': 'engine_delta', 'code_loaded_from_registration': False}


class ExtensionRouter:
    def __init__(self, engine):
        self.engine = engine

    def _service(self, context, *, write=False):
        if context.project_id is None:
            raise LaneError('PROJECT_REQUIRED', 'Select the project for this adapter grant.')
        return connector_service(self.engine, self.engine.directory.open(context.project_id, write=write))

    def _host(self, context):
        # Only the authenticated engine session can provide its configured profile.
        return self.engine.clients.session(context.client_id).host_observation.client.configured_profile

    def resolve(self, spec, route, context, arguments=None):
        binding = route.extension
        try:
            if context.authorize is None:
                raise LaneError('LIVE_AUTHORIZATION_REQUIRED', 'Reconnect the client before selecting an extension.')
            context.authorize('tools')
            context.authorize(spec.permission)
            service, host = self._service(context), self._host(context)
            with service.store.connection(read_only=True) as connection:
                present = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='extensions_registration'").fetchone()
            rows = service.active_catalog() if present else []
            preferred = None
            if binding.plugin_id_field and arguments is not None:
                preferred = getattr(arguments, binding.plugin_id_field)
                if preferred is not None and not isinstance(preferred, str):
                    raise LaneError('PLUGIN_SELECTION_INVALID', 'Select one configured plugin identity.')
            lane = getattr(arguments, binding.lane_field) if binding.lane_field and arguments is not None else binding.lane
            from .lanes import CANONICAL_LANE_IDS
            if lane not in CANONICAL_LANE_IDS:
                raise LaneError('PLUGIN_LANE_SCOPE_DENIED', 'Select one registered lane for this adapter operation.')
            eligible = [row for row in rows if row['active'] and row['grant_live']
                and row.get('backend_id') == binding.backend_id and row.get('backend_version') == binding.backend_version
                and row['backend_runtime'] == binding.backend_runtime and row['role'] == binding.role
                and row['role_schema'] == dict(binding.role_schema) and binding.capability in row['capabilities']
                and spec.name in row['allowed_actions'] and lane in row['allowed_lanes']
                and host in row['host_profiles'] and (preferred is None or row['plugin_id'] == preferred)]
            if len(eligible) != 1:
                raise LaneError('PLUGIN_ROUTE_AMBIGUOUS' if len(eligible) > 1 else 'PLUGIN_ROUTE_DENIED',
                                'Select exactly one live project grant for this adapter contract.')
            selected = eligible[0]
            if arguments is not None:
                self._scope(service, selected, binding, context, arguments)
            # Pass only the exact selected registration. A readiness probe must
            # not choose credentials from another otherwise eligible grant.
            ready = binding.readiness(context, selected)
            # Probe code must actually check its backend/configuration. Package presence alone is insufficient.
            if not isinstance(ready, dict) or ready.get('ready') is not True or ready.get('backend_version') != binding.backend_version:
                raise LaneError('PLUGIN_RUNTIME_UNAVAILABLE', 'The selected exact backend has not passed its readiness probe.')
            proof = {'plugin_id': selected['plugin_id'], 'registration_version': selected['version'],
                     'registration_digest': selected['digest'], 'backend_id': binding.backend_id,
                     'backend_version': binding.backend_version, 'backend_runtime': binding.backend_runtime,
                     'lane': lane, 'capability': binding.capability, 'role': binding.role,
                     'configured_host': host, 'host_attestation': 'unavailable',
                     'purpose_digest': _digest(selected['purpose']), 'expires_at': selected['expires_at']}
            return {'ready': True, 'reason': 'EXACT_GRANT_AND_BACKEND_READY', 'proof': proof,
                    'argument_scope_checked': arguments is not None, 'execution_authorized': False}
        except LaneError as error:
            return {'ready': False, 'reason': error.code, 'execution_authorized': False}
        except Exception:  # noqa: BLE001 - readiness must not leak vendor configuration or secret values
            return {'ready': False, 'reason': 'PLUGIN_READINESS_FAILED', 'execution_authorized': False}

    @staticmethod
    def _values(arguments, fields):
        for name in fields:
            value = getattr(arguments, name)
            values = value if isinstance(value, list) else [value]
            if not 1 <= len(values) <= 128 or any(not isinstance(item, str) or not item or len(item) > 32768 for item in values):
                raise LaneError('PLUGIN_SCOPE_INVALID', 'Select bounded exact operation targets.')
            yield from values

    def _scope(self, service, plugin, binding, context, arguments):
        from .storage import reject_links
        for path_fields, roots, permission in ((binding.read_path_fields, plugin.get('read_roots', []), 'read'),
                                         (binding.write_path_fields, plugin['write_roots'], 'write')):
            for value in self._values(arguments, path_fields):
                path = Path(value)
                if '..' in path.parts or (path.drive and not path.is_absolute()) or '\x00' in value:
                    raise LaneError('PLUGIN_PATH_SCOPE_DENIED', 'The adapter target is outside its exact root grant.')
                path = path if path.is_absolute() else service.project.source_root / path
                if any(':' in part for part in path.parts if part != path.anchor):
                    raise LaneError('PLUGIN_PATH_SCOPE_DENIED', 'Alternate stream targets are outside the root grant.')
                reject_links(path, Path(path.anchor))
                path = path.resolve(strict=False)
                if not any(path.is_relative_to(Path(root)) for root in roots):
                    raise LaneError('PLUGIN_PATH_SCOPE_DENIED', 'The adapter target is outside its exact root grant.')
                service._authorize(context, permission, path=path)
        if not set(self._values(arguments, binding.resource_fields)) <= set(plugin.get('resource_ids', [])):
            raise LaneError('PLUGIN_RESOURCE_SCOPE_DENIED', 'The resource is outside the adapter grant.')

    def authorize(self, spec, route, context, arguments, expected):
        execution = context.execution
        if execution is None or execution.guard is None:
            raise LaneError('DELTA_REQUIRED', 'Optional adapters run within the owning Plan task.')
        service = self._service(context)
        service._lease(execution.lease)
        current = self.resolve(spec, route, context, arguments)
        if not current['ready']:
            raise LaneError(current['reason'], 'The current extension boundary no longer permits execution.')
        if current['proof'] != expected:
            raise LaneError('PLUGIN_VERSION_CONFLICT', 'The exact admitted extension grant changed; replan this operation.')
        return current['proof']

    def record(self, context, proof, receipt, kind):
        service = self._service(context, write=True)
        lease = context.execution.lease
        service._lease(lease)
        with lease.transaction('receipts') as connection:
            service._event(connection, proof['plugin_id'], kind, context.client_id,
                {'binding': proof, 'operation': receipt['operation'], 'request_id': context.request_id,
                 'job_id': context.execution.claim.job_id, 'task_id': context.execution.task_id,
                 'plan_revision': context.expected_revision, 'tool_receipt_digest': _digest(receipt),
                 'outcome': receipt.get('outcome'), 'automatic_replay': False})

    def validate_output(self, route, output):
        from .connector_governance import ConnectorGovernance
        # No storage is needed for this deterministic field/type check.
        return ConnectorGovernance.validate_role_output({'role_schema': dict(route.extension.role_schema)}, output)


def _digest(value):
    from .tool_routes import _digest as digest
    return digest(value)

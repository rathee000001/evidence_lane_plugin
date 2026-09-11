"""Conditional non-agent ecosystem adapters for the governed Codex toolchain.

The adapter registry is executable routing policy, not a second agent system.
Codex owns reasoning and every Evidence Lane lifecycle decision.  Adapters may
transport, index, evaluate, observe, or deploy one already-authorized action;
they never own Project Truth, Plan, Goal, HIL, memory, registration, scheduling,
or tunnel/task identity.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

ECOSYSTEM_TOOLCHAIN_SCHEMA = "evidence-lane.ecosystem-toolchain.v4"

EXCLUDED_AGENT_OWNERS: tuple[str, ...] = (
    "Claude",
    "GPT-5",
    "Gemini",
    "Llama",
    "Mistral",
    "DeepSeek",
    "Claude_Code",
    "GitHub_Copilot",
    "Cursor",
    "Windsurf",
    "Cline",
    "Aider",
    "LangGraph_Agent",
    "CrewAI",
    "AutoGen",
    "Google_ADK",
    "Semantic_Kernel_Agent",
)


@dataclass(frozen=True)
class AdapterContract:
    tool_id: str
    category: str
    transport: str
    capabilities: tuple[str, ...]
    action_classes: tuple[str, ...]
    credential_names: tuple[str, ...] = ()
    python_modules: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    project_grant_required: bool = True
    root_scope_required: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "category": self.category,
            "transport": self.transport,
            "capabilities": list(self.capabilities),
            "action_classes": list(self.action_classes),
            "credential_names": list(self.credential_names),
            "python_modules": list(self.python_modules),
            "commands": list(self.commands),
            "project_grant_required": self.project_grant_required,
            "root_scope_required": self.root_scope_required,
            "agent_authority": False,
            "lifecycle_authority": False,
            "project_authority": False,
            "memory_authority": False,
            "scheduler_authority": False,
            "sqlite_authority_replacement": False,
            "credentials_external": True,
            "runs_only_when_selected": True,
        }


_ADAPTERS: tuple[AdapterContract, ...] = (
    AdapterContract(
        "OpenAI_Agents_SDK",
        "subordinate_orchestration_sdk",
        "python_sdk",
        ("typed_tool_orchestration", "mcp_client", "trace_correlation"),
        ("GOVERNANCE", "CODE", "DOCUMENT", "OCR_MEDIA", "DATA", "WEB_RESEARCH", "GRAPH", "RUNTIME_API"),
        ("OPENAI_API_KEY",),
        ("agents",),
    ),
    AdapterContract(
        "FastMCP",
        "mcp_runtime",
        "python_sdk",
        ("mcp_server", "mcp_client", "server_composition", "stdio", "streamable_http"),
        ("RUNTIME_API",),
        python_modules=("fastmcp",),
        project_grant_required=False,
    ),
    AdapterContract("Pinecone", "context_index", "https_api", ("vector_upsert", "vector_query", "delete_by_identity"), ("RETRIEVAL",), ("PINECONE_API_KEY", "PINECONE_HOST")),
    AdapterContract("Weaviate", "context_index", "https_api", ("schema_inspect", "vector_upsert", "hybrid_query"), ("RETRIEVAL",), ("WEAVIATE_URL", "WEAVIATE_API_KEY")),
    AdapterContract("Milvus", "context_index", "https_or_grpc_adapter", ("collection_inspect", "vector_upsert", "vector_query"), ("RETRIEVAL",), ("MILVUS_URI", "MILVUS_TOKEN")),
    AdapterContract("OpenSearch", "context_index", "https_api", ("index_inspect", "lexical_query", "vector_query"), ("RETRIEVAL",), ("OPENSEARCH_URL", "OPENSEARCH_USERNAME", "OPENSEARCH_PASSWORD")),
    AdapterContract("LangSmith", "evaluation", "https_api", ("feedback_create",), ("EVALUATION",), ("LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT", "LANGSMITH_WORKSPACE_ID")),
    AdapterContract("Langfuse", "observability", "otlp_http_json", ("span", "generation", "event", "tool", "evaluator"), ("OBSERVABILITY",), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")),
    AdapterContract("OpenTelemetry", "observability", "otlp_http_json", ("trace", "metric", "log"), ("OBSERVABILITY",), ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS")),
    AdapterContract("Grafana", "observability", "https_api", ("dashboard_evidence", "alert_evidence", "trace_link"), ("OBSERVABILITY",), ("GRAFANA_URL", "GRAFANA_SERVICE_ACCOUNT_TOKEN")),
)

ECOSYSTEM_ADAPTERS: dict[str, AdapterContract] = {
    row.tool_id: row for row in _ADAPTERS
}


def build_openai_agents_function_tool(*, action: dict[str, Any], client: Any) -> Any:
    """Compose a current authenticated SDK action without creating an Agent.

    The current native MCP request envelope is also the FunctionTool contract.
    The engine keeps permission, Delta, lane and output-validation ownership;
    this adapter adds no authority and never retries an uncertain invocation.
    """
    import asyncio
    import copy

    from agents import FunctionTool
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError as SchemaValidationError

    from .errors import LaneError
    from .local_transport import MAX_ACTION_MESSAGE_BYTES
    from .mcp_adapter import tool_from_action
    from .sdk import ActionRequest, ActionResponse, EvidenceLaneClient

    if not isinstance(client, EvidenceLaneClient) or not isinstance(action, dict):
        raise LaneError('ECOSYSTEM_BOUND_SDK_REQUIRED', 'Select an authenticated Evidence Lane SDK client and current action.')
    name = action.get('name')
    if not isinstance(name, str) or not name or len(name) > 64:
        raise LaneError('ECOSYSTEM_ACTION_REQUIRED', 'Select one exact current action.')
    catalog_method = getattr(client.transport, 'catalog', None)
    if not callable(catalog_method):
        raise LaneError('ECOSYSTEM_CATALOG_REQUIRED', 'The selected SDK transport must expose its authenticated catalog.')

    def current_action():
        try:
            catalog = catalog_method()
        except RuntimeError:
            raise LaneError('ECOSYSTEM_CATALOG_UNAVAILABLE', 'The selected SDK catalog is unavailable; reconnect explicitly before rebuilding this tool.') from None
        if not isinstance(catalog, list) or not 1 <= len(catalog) <= 1024:
            raise LaneError('ECOSYSTEM_CATALOG_INVALID', 'The authenticated action catalog exceeds this adapter contract.')
        matches = [row for row in catalog if isinstance(row, dict) and row.get('name') == name]
        if len(matches) != 1:
            raise LaneError('ECOSYSTEM_ACTION_UNAVAILABLE', 'The selected action is not uniquely present in the current catalog.')
        return matches[0]

    selected = copy.deepcopy(current_action())
    schema_digest = sha256_bytes(canonical_json_bytes(selected))
    if sha256_bytes(canonical_json_bytes(action)) != schema_digest:
        raise LaneError('ECOSYSTEM_ACTION_SCHEMA_CHANGED', 'The supplied action differs from the current authenticated catalog.')
    native_tool = tool_from_action(selected)
    input_validator = Draft202012Validator(copy.deepcopy(native_tool.inputSchema))
    output_validator = Draft202012Validator(copy.deepcopy(selected['outputSchema']))

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate field')
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError('Nonfinite number')

    async def invoke(_context: Any, raw_arguments: str) -> str:
        try:
            if not isinstance(raw_arguments, str) or len(raw_arguments.encode()) > MAX_ACTION_MESSAGE_BYTES:
                raise ValueError('Input budget')
            values = json.loads(raw_arguments, object_pairs_hook=unique_object, parse_constant=invalid_constant)
            input_validator.validate(values)
            ActionRequest(action=name, **values)
        except (ValueError, TypeError, RecursionError, SchemaValidationError):
            raise LaneError('ECOSYSTEM_INPUT_INVALID', 'The invocation must match the bounded current SDK envelope.') from None
        fresh = await asyncio.to_thread(current_action)
        if sha256_bytes(canonical_json_bytes(fresh)) != schema_digest:
            raise LaneError('ECOSYSTEM_ACTION_SCHEMA_CHANGED', 'Rebuild the tool from the changed authenticated catalog.')
        try:
            response = await asyncio.to_thread(client.call, name, project_id=values.get('project_id'),
                expected_revision=values.get('expected_revision'), arguments=values['arguments'])
            response = ActionResponse.model_validate(response)
            if response.action != name:
                raise ValueError('Action binding')
            if response.status in {'ok', 'queued'}:
                output_validator.validate(response.result)
            return response.model_dump_json()
        except (ValueError, TypeError, RecursionError, SchemaValidationError):
            raise LaneError('ECOSYSTEM_RESPONSE_INVALID', 'The SDK response does not match the current action contract.') from None
        except RuntimeError:
            raise LaneError('ECOSYSTEM_DELIVERY_UNCONFIRMED', 'SDK delivery was not confirmed. Reconcile before retrying; no automatic retry was performed.') from None

    tool: Any = FunctionTool(name=name, description=selected['description'],
        params_json_schema=copy.deepcopy(native_tool.inputSchema), on_invoke_tool=invoke,
        strict_json_schema=False, is_enabled=True, needs_approval=False)
    # Strict conversion would rewrite optional/default fields. The registered
    # envelope and action result are validated above without changing schemas.
    tool._evidence_lane_contract = {
        'schema': 'evidence-lane.agents-sdk-action.v4', 'status': 'BOUND_TOOL_CONSTRUCTED',
        'action_schema_sha256': schema_digest, 'input_schema_sha256': sha256_bytes(canonical_json_bytes(native_tool.inputSchema)),
        'output_schema_sha256': sha256_bytes(canonical_json_bytes(selected['outputSchema'])),
        'input_validation_owner': 'current_mcp_envelope_and_authenticated_sdk',
        'output_validation_owner': 'current_action_schema_and_authenticated_sdk',
        'invocation_revalidates_catalog': True, 'automatic_retry': False,
        'agent_created': False, 'runner_created': False, 'native_host_provenance_verified': False,
        'construction_authorizes_execution': False,
    }
    return tool


def build_fastmcp_gateway(native_transport: Any) -> Any:
    """Compose a supplied current MCP transport without claiming it is live.

    The backend must be the current native adapter with its own explicit client
    and project grants. A legacy low-level Server object is not a transport.
    Construction never installs, starts or authorizes an Evidence Lane engine.
    """
    from importlib.metadata import version

    from fastmcp import Client
    from fastmcp.server import create_proxy

    from . import __version__
    from .errors import LaneError

    if isinstance(native_transport, Client) and native_transport.is_connected():
        raise LaneError('ECOSYSTEM_SHARED_CLIENT_UNSUPPORTED', 'Use a transport or disconnected client to preserve separate backend sessions.')
    gateway: Any = create_proxy(native_transport, name='Evidence Lane FastMCP Gateway',
        instructions='Transport-only proxy. Current action schemas, project permissions, Plan and lane ownership remain in the authenticated Evidence Lane backend.',
        version=__version__, mask_error_details=True, provider_error_strategy='raise', dereference_schemas=False)
    gateway._evidence_lane_contract = {
        'schema': 'evidence-lane.fastmcp-gateway.v4', 'status': 'COMPOSED_NOT_CONTACTED',
        'fastmcp_version': version('fastmcp'), 'native_action_names_unchanged': None,
        'native_action_schemas_unchanged': None, 'backend_protocol_verified': False,
        'agent_authority': False, 'lifecycle_authority': False, 'project_authority': False,
        'scheduler_authority': False, 'construction_authorizes_execution': False,
    }
    return gateway


def validate_ecosystem_adapter_catalog(
    *, declared_tools: Iterable[str] | None = None
) -> dict[str, Any]:
    rows = [row.public() for row in _ADAPTERS]
    ids = [row["tool_id"] for row in rows]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("The non-agent ecosystem adapter catalog must be non-empty and unique.")
    if any(
        row[authority]
        for row in rows
        for authority in (
            "agent_authority",
            "lifecycle_authority",
            "project_authority",
            "memory_authority",
            "scheduler_authority",
            "sqlite_authority_replacement",
        )
    ):
        raise ValueError("An ecosystem adapter claims forbidden Evidence Lane authority.")
    for row in rows:
        for name in row["credential_names"]:
            if not name or name != name.upper() or any(char.isspace() for char in name):
                raise ValueError(f"Credential contract is not an environment-variable name: {name!r}")
    declared = set(declared_tools or ())
    if declared and set(ids) - declared:
        raise ValueError(
            f"Ecosystem adapters missing from tool matrix: {sorted(set(ids) - declared)}"
        )
    body = {
        "schema": ECOSYSTEM_TOOLCHAIN_SCHEMA,
        "status": "PASS",
        "adapter_count": len(rows),
        "adapters": rows,
        "codex_is_sole_agent_authority": True,
        "openai_agents_sdk_is_subordinate": True,
        "excluded_agent_owners": list(EXCLUDED_AGENT_OWNERS),
        "conditional_execution_not_run_everything": True,
        "credentials_external": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def inspect_ecosystem_adapter_runtime(tool_id: str) -> dict[str, Any]:
    adapter = ECOSYSTEM_ADAPTERS[tool_id]
    modules = {}
    for name in adapter.python_modules:
        try:
            modules[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            modules[name] = False
    commands = {name: shutil.which(name) for name in adapter.commands}
    if adapter.python_modules:
        state = "MODULES_FOUND" if all(modules.values()) else "MODULES_MISSING"
    elif adapter.commands:
        state = "COMMANDS_FOUND" if any(commands.values()) else "COMMANDS_MISSING"
    else:
        state = "CONFIGURATION_REQUIRED"
    body = {
        "schema": "evidence-lane.ecosystem-adapter-runtime.v4",
        "status": "DISCOVERY_COMPLETE",
        "runtime_activated": False,
        "service_readiness_verified": False,
        "tool_id": tool_id,
        "state": state,
        "modules": modules,
        "commands": commands,
        "credential_names": list(adapter.credential_names),
        "credential_values_read": False,
        "network_probe_performed": False,
        "agent_authority": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def resolve_ecosystem_adapters(
    *,
    capability: str,
    action_class: str,
    lane_id: str,
    project_id: str,
    task_id: str,
    granted_tools: Iterable[str],
    available_tools: Iterable[str],
) -> dict[str, Any]:
    """Resolve an ordered, bounded adapter set for one already-governed action."""

    exact_capability = capability.strip().lower()
    exact_class = action_class.strip().upper()
    exact_lane = lane_id.strip().lower()
    exact_project = project_id.strip()
    exact_task = task_id.strip()
    if not all((exact_capability, exact_class, exact_lane, exact_project, exact_task)):
        raise ValueError("Adapter resolution requires action, lane, project, and task identity.")
    granted = set(granted_tools)
    available = set(available_tools)
    candidates = [
        row
        for row in _ADAPTERS
        if exact_capability in row.capabilities and exact_class in row.action_classes
    ]
    selected = [
        row.tool_id
        for row in candidates
        if row.tool_id in available
        and (not row.project_grant_required or row.tool_id in granted)
    ]
    body = {
        "schema": "evidence-lane.ecosystem-adapter-resolution.v4",
        "status": "CANDIDATE_SELECTED" if selected else "NO_CANDIDATE",
        "input_provenance": "CALLER_SUPPLIED_CAPABILITY_LISTS",
        "permissions_verified": False,
        "runtime_readiness_verified": False,
        "execution_authorized": False,
        "capability": exact_capability,
        "action_class": exact_class,
        "lane_id": exact_lane,
        "project_id": exact_project,
        "task_id": exact_task,
        "candidate_tools": [row.tool_id for row in candidates],
        "selected_tools": selected[:1],
        "unselected_tools": [row.tool_id for row in candidates if row.tool_id not in selected[:1]],
        "run_every_tool": False,
        "selected_tool_count_maximum": 1,
        "codex_is_agent_authority": True,
        "adapter_has_agent_authority": False,
        "adapter_has_scheduler_authority": False,
    }
    return {**body, "resolution_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "ECOSYSTEM_ADAPTERS",
    "ECOSYSTEM_TOOLCHAIN_SCHEMA",
    "EXCLUDED_AGENT_OWNERS",
    "build_fastmcp_gateway",
    "build_openai_agents_function_tool",
    "inspect_ecosystem_adapter_runtime",
    "resolve_ecosystem_adapters",
    "validate_ecosystem_adapter_catalog",
]

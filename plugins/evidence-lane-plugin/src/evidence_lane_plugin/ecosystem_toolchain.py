"""Conditional non-agent ecosystem adapters for the governed Codex toolchain.

The adapter registry is executable routing policy, not a second agent system.
Codex owns reasoning and every Evidence Lane lifecycle decision.  Adapters may
transport, index, evaluate, observe, or deploy one already-authorized action;
they never own Project Truth, Plan, Goal, HIL, memory, registration, scheduling,
or tunnel/task identity.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

ECOSYSTEM_TOOLCHAIN_SCHEMA = "evidence-lane.ecosystem-toolchain.v1"

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
    AdapterContract(
        "GitHub_MCP_Server",
        "mcp_connector",
        "mcp_stdio_or_streamable_http",
        ("repository_read", "repository_write", "checks", "pull_requests"),
        ("CODE",),
        ("EVIDENCE_LANE_GITHUB_APP_ID", "EVIDENCE_LANE_GITHUB_APP_PRIVATE_KEY_PATH"),
        commands=("github-mcp-server",),
    ),
    AdapterContract(
        "Filesystem_MCP_Server",
        "mcp_connector",
        "mcp_stdio",
        ("bounded_read", "bounded_write", "search", "metadata"),
        ("CODE", "DOCUMENT", "DATA"),
        commands=("mcp-server-filesystem",),
        root_scope_required=True,
    ),
    AdapterContract(
        "PostgreSQL_MCP_Server",
        "mcp_connector",
        "mcp_stdio_or_streamable_http",
        ("schema_inspect", "bounded_query", "migration_evidence"),
        ("DATA", "RETRIEVAL"),
        ("EVIDENCE_LANE_POSTGRES_DSN",),
        commands=("postgres-mcp",),
    ),
    AdapterContract(
        "Slack_MCP_Server",
        "mcp_connector",
        "mcp_stdio_or_streamable_http",
        ("channel_read", "message_draft", "approved_send"),
        ("RUNTIME_API",),
        ("EVIDENCE_LANE_SLACK_TOKEN",),
        commands=("slack-mcp-server",),
    ),
    AdapterContract("Pinecone", "context_index", "https_api", ("vector_upsert", "vector_query", "delete_by_identity"), ("RETRIEVAL",), ("PINECONE_API_KEY", "PINECONE_HOST")),
    AdapterContract("Weaviate", "context_index", "https_api", ("schema_inspect", "vector_upsert", "hybrid_query"), ("RETRIEVAL",), ("WEAVIATE_URL", "WEAVIATE_API_KEY")),
    AdapterContract("Milvus", "context_index", "https_or_grpc_adapter", ("collection_inspect", "vector_upsert", "vector_query"), ("RETRIEVAL",), ("MILVUS_URI", "MILVUS_TOKEN")),
    AdapterContract("OpenSearch", "context_index", "https_api", ("index_inspect", "lexical_query", "vector_query"), ("RETRIEVAL",), ("OPENSEARCH_URL", "OPENSEARCH_USERNAME", "OPENSEARCH_PASSWORD")),
    AdapterContract("LangSmith", "evaluation", "python_sdk", ("trace", "dataset", "evaluator", "experiment"), ("EVALUATION", "OBSERVABILITY"), ("LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT"), ("langsmith",)),
    AdapterContract("TruLens", "evaluation", "python_or_http_adapter", ("feedback_function", "record", "evaluation"), ("EVALUATION",), ("TRULENS_ENDPOINT", "TRULENS_API_KEY")),
    AdapterContract("DeepEval", "evaluation", "python_or_command_adapter", ("metric", "dataset", "test_run"), ("EVALUATION",), ("DEEPEVAL_API_KEY",), commands=("deepeval",)),
    AdapterContract("Promptfoo", "evaluation", "command_adapter", ("prompt_test", "red_team", "assertion"), ("EVALUATION",), ("PROMPTFOO_CONFIG",), commands=("promptfoo",)),
    AdapterContract("Langfuse", "observability", "python_sdk", ("trace", "span", "generation", "score"), ("OBSERVABILITY",), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"), ("langfuse",)),
    AdapterContract("Helicone", "observability", "https_proxy", ("request_observation", "cost", "latency"), ("OBSERVABILITY",), ("HELICONE_API_KEY", "HELICONE_BASE_URL")),
    AdapterContract("OpenTelemetry", "observability", "python_sdk", ("trace", "metric", "log", "otlp_export"), ("OBSERVABILITY",), ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS"), ("opentelemetry.sdk", "opentelemetry.exporter.otlp.proto.http.trace_exporter"), project_grant_required=False),
    AdapterContract("Grafana", "observability", "https_api", ("dashboard_evidence", "alert_evidence", "trace_link"), ("OBSERVABILITY",), ("GRAFANA_URL", "GRAFANA_SERVICE_ACCOUNT_TOKEN")),
    AdapterContract("Docker", "runtime_deployment", "host_command", ("image_build", "container_run", "artifact_inspect"), ("DEPLOYMENT",), commands=("docker",)),
    AdapterContract("Kubernetes", "runtime_deployment", "host_command", ("manifest_validate", "apply_after_approval", "rollout_evidence"), ("DEPLOYMENT",), ("KUBECONFIG",), commands=("kubectl",)),
    AdapterContract("AWS_Lambda", "runtime_deployment", "cloud_api", ("package_validate", "deploy_after_approval", "invoke_evidence"), ("DEPLOYMENT",), ("AWS_PROFILE", "AWS_REGION")),
    AdapterContract("Google_Cloud_Run", "runtime_deployment", "cloud_api", ("container_validate", "deploy_after_approval", "revision_evidence"), ("DEPLOYMENT",), ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT")),
    AdapterContract("AWS", "cloud_provider", "cloud_api_or_cli", ("identity", "artifact", "deployment_evidence"), ("DEPLOYMENT",), ("AWS_PROFILE", "AWS_REGION"), commands=("aws",)),
    AdapterContract("Azure", "cloud_provider", "cloud_api_or_cli", ("identity", "artifact", "deployment_evidence"), ("DEPLOYMENT",), ("AZURE_TENANT_ID", "AZURE_CLIENT_ID"), commands=("az",)),
    AdapterContract("Google_Cloud", "cloud_provider", "cloud_api_or_cli", ("identity", "artifact", "deployment_evidence"), ("DEPLOYMENT",), ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"), commands=("gcloud",)),
)

ECOSYSTEM_ADAPTERS: dict[str, AdapterContract] = {
    row.tool_id: row for row in _ADAPTERS
}


def build_openai_agents_function_tool(
    *,
    action: dict[str, Any],
    dispatcher: Any,
) -> Any:
    """Pair one governed action with the Agents SDK without creating an Agent.

    The returned FunctionTool delegates to the existing Evidence Lane SDK
    dispatcher.  This module never instantiates ``Agent`` or ``Runner`` and
    therefore cannot become an independent reasoning or lifecycle owner.
    """

    from agents import FunctionTool

    name = str(action["name"])
    description = str(action["description"])
    input_schema = dict(action["input_schema"])
    output_schema = dict(action["output_schema"])

    async def invoke(_context: Any, raw_arguments: str) -> Any:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise TypeError("Evidence Lane action arguments must be an object.")
        result = dispatcher(name, arguments)
        if inspect.isawaitable(result):
            result = await result
        return result

    tool = FunctionTool(
        name=name,
        description=description,
        params_json_schema=input_schema,
        output_json_schema=None,
        on_invoke_tool=invoke,
        strict_json_schema=False,
        is_enabled=True,
        needs_approval=False,
        _is_agent_tool=False,
        _is_codex_tool=True,
    )
    tool._evidence_lane_output_schema = output_schema  # type: ignore[attr-defined]
    tool._evidence_lane_input_schema_sha256 = sha256_bytes(  # type: ignore[attr-defined]
        canonical_json_bytes(input_schema)
    )
    tool._evidence_lane_output_schema_sha256 = sha256_bytes(  # type: ignore[attr-defined]
        canonical_json_bytes(output_schema)
    )
    tool._evidence_lane_output_validation_owner = (  # type: ignore[attr-defined]
        "EVIDENCE_LANE_INTERNAL_SDK"
    )
    tool._evidence_lane_input_validation_owner = (  # type: ignore[attr-defined]
        "EVIDENCE_LANE_INTERNAL_SDK"
    )
    return tool


def build_fastmcp_gateway(native_server: Any) -> Any:
    """Compose the pinned FastMCP 3 gateway over the governed native server."""

    from fastmcp.server import create_proxy

    gateway = create_proxy(
        native_server,
        name="Evidence Lane FastMCP Gateway",
        instructions=(
            "Transport-only proxy for the governed Evidence Lane native action "
            "catalog. Codex, SDK, ENV/UOP, authority, HIL, and receipt ownership "
            "remain in the backend server."
        ),
        version="3.0.0",
        mask_error_details=False,
    )
    gateway._evidence_lane_contract = {  # type: ignore[attr-defined]
        "schema": "evidence-lane.fastmcp-gateway.v1",
        "status": "ACTIVE_TRANSPORT_ONLY",
        "fastmcp_version": "3.4.7",
        "native_action_names_unchanged": True,
        "native_action_schemas_unchanged": True,
        "agent_authority": False,
        "lifecycle_authority": False,
        "project_authority": False,
        "scheduler_authority": False,
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
    modules = {
        name: importlib.util.find_spec(name) is not None
        for name in adapter.python_modules
    }
    commands = {name: shutil.which(name) for name in adapter.commands}
    if adapter.python_modules:
        state = "ACTIVE" if all(modules.values()) else "UNAVAILABLE"
    elif adapter.commands:
        state = "AVAILABLE" if any(commands.values()) else "CONFIGURATION_REQUIRED"
    else:
        state = "CONFIGURATION_REQUIRED"
    body = {
        "schema": "evidence-lane.ecosystem-adapter-runtime.v1",
        "status": "PASS",
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
        "schema": "evidence-lane.ecosystem-adapter-resolution.v1",
        "status": "PASS" if selected else "BLOCKED_NO_GRANTED_AVAILABLE_ADAPTER",
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

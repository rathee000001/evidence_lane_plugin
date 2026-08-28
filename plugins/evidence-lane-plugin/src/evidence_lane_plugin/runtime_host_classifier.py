"""Secret-safe runtime, surface, namespace, and capability classification."""

from __future__ import annotations

import re
from typing import Any

from .constants import ENGINE_VERSION
from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes
from .model_compatibility import classify_model_compatibility
from .models import HostKind, normalize_host_kind
from .state_travel_contract import execution_profile_from_context

RUNTIME_HOST_CLASSIFIER_SCHEMA = "evidence-lane.runtime-host-classifier.v1"
RUNTIME_NAMESPACE_SCHEMA = "evidence-lane.runtime-namespace.v1"

_CONTAINER_CHANNEL_ALIASES = {
    "CHATGPT_DESKTOP_CURRENT": "CHATGPT_DESKTOP_STABLE_OR_CURRENT",
    "CHATGPT_DESKTOP_STABLE": "CHATGPT_DESKTOP_STABLE_OR_CURRENT",
    "CHATGPT_DESKTOP_STABLE_OR_CURRENT": "CHATGPT_DESKTOP_STABLE_OR_CURRENT",
    "CHATGPT_DESKTOP_BETA": "CHATGPT_DESKTOP_BETA",
    "CODEX_CLI": "CODEX_CLI",
    "CODEX_VM": "CODEX_VM",
    "PUBLIC_AI": "PUBLIC_AI",
}
_SURFACE_ALIASES = {
    "CODEX": "CODEX",
    "CODEX_LAYER": "CODEX",
    "CODEX_VIEW": "CODEX",
    "CHATGPT": "CHATGPT_CHAT",
    "CHATGPT_CHAT": "CHATGPT_CHAT",
    "CHATGPT_WORK": "CHATGPT_WORK",
    "WORK": "CHATGPT_WORK",
    "UNAVAILABLE": "UNPROVEN",
    "UNPROVEN": "UNPROVEN",
}
_SURFACE_EVIDENCE = {
    "NATIVE_EVIDENCE_LANE_MCP_ROUTE",
    "HOST_SESSION_TASK_CAPABILITY_RECEIPT",
    "CODEX_TASK_RUNTIME_CONTEXT",
    "HOST_CAPABILITY_UNAVAILABLE",
}
_NATIVE_CAPABILITY_KEYS = {
    "exact_task_binding",
    "goal",
    "hooks",
    "host_plan",
    "host_session_identity",
    "local_filesystem",
    "model_tooling",
    "native_mcp",
    "tunnel_control",
}
_NON_AUTHORITY_IDENTITY_KEYS = {
    "cwd",
    "executable",
    "package_id",
    "process_name",
    "task_title",
    "window_title",
}
_WORKSPACE_CLASS_ALIASES = {
    "LOCAL": "LOCAL_WORKSPACE",
    "LOCAL_WORKSPACE": "LOCAL_WORKSPACE",
    "WORKTREE": "CODEX_MANAGED_WORKTREE",
    "CODEX_WORKTREE": "CODEX_MANAGED_WORKTREE",
    "CODEX_MANAGED_WORKTREE": "CODEX_MANAGED_WORKTREE",
    "DURABLE_REMOTE": "DURABLE_REMOTE_WORKSPACE",
    "DURABLE_REMOTE_WORKSPACE": "DURABLE_REMOTE_WORKSPACE",
    "EPHEMERAL": "EPHEMERAL_REMOTE_WORKSPACE",
    "EPHEMERAL_REMOTE_WORKSPACE": "EPHEMERAL_REMOTE_WORKSPACE",
}
_SAFE_VERSION_RE = re.compile(r"^[A-Za-z0-9._+\-]{1,128}$")


def _normalized_token(value: Any) -> str:
    return str(value or "").strip().replace("-", "_").replace(" ", "_").upper()


def _surface_context(runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    context = dict(runtime_context or {})
    nested = context.get("host_surface")
    return dict(nested) if isinstance(nested, dict) else context


def classify_runtime_host(
    host: HostKind | str,
    *,
    ephemeral: bool,
    server_has_durable_filesystem: bool | None,
    runtime_context: dict[str, Any] | None = None,
    host_session_id: str | None = None,
) -> dict[str, Any]:
    """Classify only measured or explicitly unavailable public host selectors.

    The package-local native MCP route is valid evidence of a Codex execution
    surface. A desktop package/process/title/CWD is deliberately not evidence.
    Explicit ChatGPT Chat or Work surfaces fail closed because the current
    Evidence Lane package governs the Codex layer only.
    """

    kind = normalize_host_kind(host)
    context = dict(runtime_context or {})
    surface = _surface_context(context)

    raw_channel = _normalized_token(surface.get("container_channel"))
    if raw_channel:
        require(
            raw_channel in _CONTAINER_CHANNEL_ALIASES,
            "CONTAINER_CHANNEL_INVALID",
            "The supplied desktop container channel is not supported.",
            status="BLOCKED",
            provided=raw_channel,
            supported=sorted(_CONTAINER_CHANNEL_ALIASES),
        )
        container_channel = _CONTAINER_CHANNEL_ALIASES[raw_channel]
        container_channel_source = "HOST_EXPOSED_RUNTIME_CONTEXT"
    elif kind == HostKind.CODEX_CLI:
        container_channel = "CODEX_CLI"
        container_channel_source = "NATIVE_HOST_KIND"
    elif kind == HostKind.CODEX_VM:
        container_channel = "CODEX_VM"
        container_channel_source = "NATIVE_HOST_KIND"
    elif kind == HostKind.PUBLIC_AI:
        container_channel = "PUBLIC_AI"
        container_channel_source = "NATIVE_HOST_KIND"
    else:
        container_channel = "DESKTOP_CHANNEL_UNAVAILABLE"
        container_channel_source = "HOST_CAPABILITY_UNAVAILABLE"

    raw_container_version = str(surface.get("container_version") or "").strip()
    require(
        not raw_container_version
        or _SAFE_VERSION_RE.fullmatch(raw_container_version) is not None,
        "CONTAINER_VERSION_INVALID",
        "Container-version evidence must be a bounded public release selector.",
        status="BLOCKED",
    )
    container_version = raw_container_version or "UNAVAILABLE"

    raw_active_surface = _normalized_token(surface.get("active_surface"))
    raw_evidence = _normalized_token(surface.get("active_surface_evidence"))
    if raw_active_surface:
        require(
            raw_active_surface in _SURFACE_ALIASES,
            "ACTIVE_SURFACE_INVALID",
            "The active host surface is not recognized.",
            status="BLOCKED",
            provided=raw_active_surface,
            supported=sorted(_SURFACE_ALIASES),
        )
        active_surface = _SURFACE_ALIASES[raw_active_surface]
    elif raw_evidence and raw_evidence != "NATIVE_EVIDENCE_LANE_MCP_ROUTE":
        active_surface = "UNPROVEN"
    elif any(key in surface for key in _NON_AUTHORITY_IDENTITY_KEYS):
        active_surface = "UNPROVEN"
        raw_evidence = "HOST_CAPABILITY_UNAVAILABLE"
    elif kind in {HostKind.CODEX_DESKTOP, HostKind.CODEX_CLI, HostKind.CODEX_VM}:
        active_surface = "CODEX"
        raw_evidence = "NATIVE_EVIDENCE_LANE_MCP_ROUTE"
    else:
        active_surface = "UNPROVEN"
        raw_evidence = "HOST_CAPABILITY_UNAVAILABLE"

    if not raw_evidence and active_surface == "CODEX":
        raw_evidence = "NATIVE_EVIDENCE_LANE_MCP_ROUTE"
    require(
        raw_evidence in _SURFACE_EVIDENCE,
        "ACTIVE_SURFACE_EVIDENCE_INVALID",
        "Active-surface evidence must come from a supported host capability receipt.",
        status="BLOCKED",
        provided=raw_evidence or None,
        supported=sorted(_SURFACE_EVIDENCE),
    )
    require(
        active_surface != "UNPROVEN",
        "ACTIVE_SURFACE_UNPROVEN",
        "Evidence Lane cannot bind authority from container, process, title, or CWD evidence alone.",
        status="BLOCKED",
        container_channel=container_channel,
        evidence=raw_evidence,
    )
    require(
        active_surface != "CODEX"
        or raw_evidence
        in {
            "NATIVE_EVIDENCE_LANE_MCP_ROUTE",
            "HOST_SESSION_TASK_CAPABILITY_RECEIPT",
            "CODEX_TASK_RUNTIME_CONTEXT",
        },
        "ACTIVE_SURFACE_EVIDENCE_UNAVAILABLE",
        "A Codex surface claim requires positive native or host capability evidence.",
        status="BLOCKED",
        container_channel=container_channel,
        evidence=raw_evidence,
    )
    require(
        active_surface == "CODEX",
        "ACTIVE_SURFACE_OUT_OF_SCOPE",
        "The current Evidence Lane package governs only the Codex layer; ChatGPT Chat and Work surfaces remain out of scope.",
        status="BLOCKED",
        active_surface=active_surface,
        container_channel=container_channel,
    )

    raw_workspace_class = _normalized_token(
        surface.get("workspace_class") or context.get("workspace_class")
    )
    if raw_workspace_class:
        require(
            raw_workspace_class in _WORKSPACE_CLASS_ALIASES,
            "WORKSPACE_CLASS_INVALID",
            "The supplied workspace class is not supported.",
            status="BLOCKED",
            provided=raw_workspace_class,
            supported=sorted(_WORKSPACE_CLASS_ALIASES),
        )
        workspace_class = _WORKSPACE_CLASS_ALIASES[raw_workspace_class]
        workspace_class_source = "HOST_EXPOSED_RUNTIME_CONTEXT"
    elif kind == HostKind.CODEX_VM and ephemeral:
        workspace_class = "EPHEMERAL_REMOTE_WORKSPACE"
        workspace_class_source = "MEASURED_HOST_EPHEMERALITY"
    elif kind == HostKind.CODEX_VM:
        workspace_class = "DURABLE_REMOTE_WORKSPACE"
        workspace_class_source = "MEASURED_HOST_LOCALITY"
    else:
        workspace_class = "LOCAL_WORKSPACE"
        workspace_class_source = "MEASURED_HOST_LOCALITY"

    require(
        workspace_class != "EPHEMERAL_REMOTE_WORKSPACE" or ephemeral,
        "WORKSPACE_EPHEMERALITY_MISMATCH",
        "An ephemeral workspace classification requires an ephemeral host.",
        status="MISMATCH",
    )
    require(
        workspace_class != "DURABLE_REMOTE_WORKSPACE"
        or (
            kind == HostKind.CODEX_VM
            and not ephemeral
            and server_has_durable_filesystem is not False
        ),
        "WORKSPACE_DURABILITY_MISMATCH",
        "A durable remote workspace requires a non-ephemeral Codex VM with durable or not-yet-exposed filesystem capability.",
        status="MISMATCH",
    )
    require(
        workspace_class not in {"LOCAL_WORKSPACE", "CODEX_MANAGED_WORKTREE"}
        or kind in {HostKind.CODEX_DESKTOP, HostKind.CODEX_CLI},
        "WORKSPACE_LOCALITY_MISMATCH",
        "Local and managed-worktree classifications require a local Codex host surface.",
        status="MISMATCH",
    )
    if kind == HostKind.CODEX_DESKTOP and container_channel not in {
        "CHATGPT_DESKTOP_STABLE_OR_CURRENT",
        "CHATGPT_DESKTOP_BETA",
        "DESKTOP_CHANNEL_UNAVAILABLE",
    }:
        require(
            False,
            "DESKTOP_CONTAINER_CHANNEL_MISMATCH",
            "A desktop Codex surface must bind a supported ChatGPT desktop container channel when exposed.",
            status="MISMATCH",
            container_channel=container_channel,
        )

    require_measured = context.get("require_measured_capabilities") is True
    require(
        server_has_durable_filesystem is not None or not require_measured,
        "DURABILITY_CAPABILITY_UNAVAILABLE",
        "A strict runtime classification requires an explicit durable-filesystem capability measurement.",
        status="BLOCKED",
    )
    durability_evidence = (
        "HOST_REPORTED_DURABLE_FILESYSTEM"
        if server_has_durable_filesystem is True
        else "HOST_REPORTED_EPHEMERAL_OR_UNAVAILABLE_FILESYSTEM"
        if server_has_durable_filesystem is False
        else "LEGACY_COMPATIBILITY_INFERENCE_EXPLICITLY_LABELED"
    )

    execution_profile = execution_profile_from_context(context)
    required_profile_fields = {
        "model",
        "reasoning_effort",
        "reasoning_speed",
    }
    profile_status = (
        "COMPLETE"
        if required_profile_fields <= set(execution_profile)
        else "PARTIAL"
        if execution_profile
        else "HOST_CAPABILITY_UNAVAILABLE"
    )

    raw_interaction = _normalized_token(context.get("interaction_profile"))
    api_route = raw_interaction in {
        "API",
        "API_HEADLESS",
        "HEADLESS_API",
        "CODEX_APP_API",
        "CLI_API",
        "DIRECT_CLI_API",
    }
    account_route = "API" if api_route else "HOST_ACCOUNT"

    supplied_capabilities = context.get("native_capabilities")
    require(
        supplied_capabilities is None or isinstance(supplied_capabilities, dict),
        "NATIVE_CAPABILITIES_INVALID",
        "Native capabilities must be a public boolean map when supplied.",
        status="BLOCKED",
    )
    capabilities: dict[str, bool] = {
        "native_mcp": True,
        "host_session_identity": bool(str(host_session_id or "").strip()),
    }
    if isinstance(supplied_capabilities, dict):
        for key, value in supplied_capabilities.items():
            require(
                key in _NATIVE_CAPABILITY_KEYS and isinstance(value, bool),
                "NATIVE_CAPABILITY_ENTRY_INVALID",
                "Native capability entries must use allowlisted names and boolean values.",
                status="BLOCKED",
                capability=str(key),
            )
            capabilities[str(key)] = value
    required_capabilities = context.get("required_native_capabilities") or []
    require(
        isinstance(required_capabilities, list)
        and all(isinstance(value, str) for value in required_capabilities),
        "REQUIRED_NATIVE_CAPABILITIES_INVALID",
        "Required native capabilities must be an ordered list of public names.",
        status="BLOCKED",
    )
    missing_capabilities = [
        value for value in required_capabilities if capabilities.get(value) is not True
    ]
    require(
        not missing_capabilities,
        "REQUIRED_NATIVE_CAPABILITY_UNAVAILABLE",
        "A required native host capability is unavailable.",
        status="BLOCKED",
        missing=missing_capabilities,
    )

    complete = (
        container_channel != "DESKTOP_CHANNEL_UNAVAILABLE"
        and container_version != "UNAVAILABLE"
        and server_has_durable_filesystem is not None
        and profile_status == "COMPLETE"
        and capabilities.get("host_session_identity") is True
    )
    core = {
        "schema": RUNTIME_HOST_CLASSIFIER_SCHEMA,
        "status": "PASS" if complete else "PARTIAL_HOST_CAPABILITY_UNAVAILABLE",
        "host_kind": kind.value,
        "container_channel": container_channel,
        "container_channel_source": container_channel_source,
        "container_version": container_version,
        "active_surface": active_surface,
        "active_surface_evidence": raw_evidence,
        "workspace_class": workspace_class,
        "workspace_class_source": workspace_class_source,
        "evidence_lane_execution_scope": "CODEX_LAYER_ONLY",
        "chatgpt_chat_work_scope": "OUT_OF_SCOPE_DEFERRED",
        "ephemeral": bool(ephemeral),
        "server_has_durable_filesystem": server_has_durable_filesystem,
        "durability_evidence": durability_evidence,
        "execution_profile": execution_profile,
        "execution_profile_status": profile_status,
        "model_compatibility": classify_model_compatibility(
            execution_profile,
            installed_host_tooling_proven=(
                capabilities.get("native_mcp") is True
                and capabilities.get("model_tooling") is True
                and capabilities.get("host_session_identity") is True
            ),
        ),
        "account_route": account_route,
        "account_tier_affects_routing": False,
        "api_billing_affects_routing": False,
        "native_capabilities": dict(sorted(capabilities.items())),
        "required_native_capabilities": list(required_capabilities),
        "missing_native_capabilities": [],
        "storage_connector_surface": "SEPARATE_HOST_CAPABILITY_PERSISTENCE_ROUTE",
        "google_drive_surface": "OPTIONAL_SEALED_ARTIFACT_FALLBACK_OR_MIRROR",
        "additional_plugin_slots_surface": "EIGHT_GOVERNED_PLUGIN_OR_TOOLCHAIN_SLOTS_ONLY",
        "raw_runtime_context_stored": False,
        "raw_process_title_cwd_used_as_authority": False,
        "secrets_stored": False,
    }
    core["classifier_receipt_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def validate_runtime_host_classifier(value: dict[str, Any]) -> dict[str, Any]:
    """Validate a current classifier receipt without reclassifying the host."""

    claimed = str(value.get("classifier_receipt_sha256") or "")
    body = {
        key: item
        for key, item in value.items()
        if key != "classifier_receipt_sha256"
    }
    require(
        value.get("schema") == RUNTIME_HOST_CLASSIFIER_SCHEMA
        and bool(claimed)
        and claimed == sha256_bytes(canonical_json_bytes(body)),
        "RUNTIME_HOST_CLASSIFIER_RECEIPT_INVALID",
        "The runtime host classifier receipt is invalid.",
        status="MISMATCH",
    )
    require(
        value.get("active_surface") == "CODEX"
        and value.get("evidence_lane_execution_scope") == "CODEX_LAYER_ONLY"
        and value.get("account_tier_affects_routing") is False
        and value.get("api_billing_affects_routing") is False
        and value.get("raw_process_title_cwd_used_as_authority") is False
        and value.get("secrets_stored") is False,
        "RUNTIME_HOST_CLASSIFIER_BOUNDARY_INVALID",
        "The runtime classifier crossed its Codex-only or secret-safe boundary.",
        status="FAIL",
    )
    return value


def build_runtime_namespace(
    *,
    project_id: str,
    governed_session_id: str,
    workspace_id: str,
    host_session_id: str | None,
    classifier: dict[str, Any],
    plugin_version: str = ENGINE_VERSION,
) -> dict[str, Any]:
    """Bind project/session/version identity without retaining raw host IDs."""

    validate_runtime_host_classifier(classifier)
    require(
        bool(project_id.strip())
        and bool(governed_session_id.strip())
        and bool(workspace_id.strip()),
        "RUNTIME_NAMESPACE_IDENTITY_REQUIRED",
        "Runtime namespace binding requires exact project, governed session, and workspace IDs.",
        status="BLOCKED",
    )
    exact_host_session_id = str(host_session_id or "").strip()
    core = {
        "schema": RUNTIME_NAMESPACE_SCHEMA,
        "project_id": project_id,
        "governed_session_id": governed_session_id,
        "workspace_id_sha256": sha256_bytes(workspace_id.encode("utf-8")),
        "raw_workspace_id_stored": False,
        "host_session_id_sha256": (
            sha256_bytes(exact_host_session_id.encode("utf-8"))
            if exact_host_session_id
            else None
        ),
        "raw_host_session_id_stored": False,
        "plugin_id": "evidence-lane-plugin",
        "plugin_version": plugin_version,
        "container_channel": classifier["container_channel"],
        "active_surface": classifier["active_surface"],
        "workspace_class": classifier["workspace_class"],
        "cross_project_fallback_allowed": False,
        "cross_session_fallback_allowed": False,
        "cross_version_cache_collision_allowed": False,
    }
    core["namespace_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def validate_runtime_namespace(value: dict[str, Any]) -> dict[str, Any]:
    claimed = str(value.get("namespace_sha256") or "")
    body = {key: item for key, item in value.items() if key != "namespace_sha256"}
    require(
        value.get("schema") == RUNTIME_NAMESPACE_SCHEMA
        and bool(claimed)
        and claimed == sha256_bytes(canonical_json_bytes(body))
        and value.get("active_surface") == "CODEX"
        and value.get("cross_project_fallback_allowed") is False
        and value.get("cross_session_fallback_allowed") is False
        and value.get("cross_version_cache_collision_allowed") is False
        and value.get("raw_host_session_id_stored") is False
        and value.get("raw_workspace_id_stored") is False,
        "RUNTIME_NAMESPACE_RECEIPT_INVALID",
        "The runtime project/session/version namespace receipt is invalid.",
        status="MISMATCH",
    )
    return value

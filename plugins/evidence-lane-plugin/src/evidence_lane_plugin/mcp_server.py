"""Universal MCP runtime contract; 3.0.0 is the Codex package release."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Iterable
from functools import wraps
from pathlib import Path
from typing import Any, Literal, cast

import anyio
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, Icon, TextContent, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import AnyHttpUrl, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import (
    READ_SCOPE,
    REMOTE_GIT_SCOPE,
    WRITE_SCOPE,
    OAuthAuthorizationError,
    OAuthJWTConfig,
    OAuthJWTVerifier,
    OAuthToolAuthorizationPolicy,
    StaticBearerVerifier,
)
from .canon_task_graph import CanonTaskDispatcher
from .codex_turn_control import (
    TurnControlError,
    package_surface_inventory,
    requires_per_delta_local_verification,
    seal_active_task_acceptance_checkpoint,
    seal_exact_task_project_session_binding,
    seal_per_delta_local_verification_checkpoint,
    verify_codex_fallback_prewarmer,
)
from .constants import (
    ENGINE_VERSION,
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)
from .errors import EvidenceLaneError, require
from .github_automation_governance import (
    apply_fastmcp_tool_filter,
)
from .hashing import canonical_json_bytes, sha256_bytes
from .internal_sdk import build_live_local_sdk_context
from .lane_engine import prewarm_native_dependencies
from .mcp_apps import (
    GOVERNED_PANEL_URI,
    MCP_APP_MIME_TYPE,
    build_project_panel_snapshot,
    build_runtime_panel_snapshot,
    governed_panel_html,
    governed_panel_resource_meta,
    governed_panel_tool_meta,
)
from .mcp_stdio_compat import (
    install_tool_namespace_compat,
    run_discovery_compatible_stdio,
)
from .public_surface_registry import (
    CODEX_READ_TOOL_NAMES,
    PublicSurfaceRegistryError,
    derive_public_surface_registry,
    resolve_public_surface_plugin_root,
)
from .service import EvidenceLaneService, inspect_service_route_parity

_PUBLIC_SITE_URL = "https://evidencelane.org"
_PLUGIN_ROOT_ENV = "EVIDENCE_LANE_PLUGIN_ROOT"
NATIVE_MCP_SERVER_IDENTITY = "evidence-lane"
NATIVE_MCP_TOOL_NAMESPACE = "mcp__evidence_lane__"
SKILL_MCP_ROUTING_SCHEMA = "evidence-lane.skill-mcp-routing.v1"

_RUNTIME_GLOBAL_TOOL_NAMES = frozenset(
    {
        "lane_catalog",
        "lifecycle_transition_law",
        "render_runtime_panel",
        "runtime_activation_status",
        "runtime_doctor",
        "session_flash_status",
    }
)

FULL_LIFECYCLE_EXPOSURE_PROFILE = "FULL_LIFECYCLE"


def resolve_skill_mcp_plugin_root() -> Path:
    """Resolve the exact plugin bundle that owns the governed skill routes."""

    try:
        return resolve_public_surface_plugin_root()
    except PublicSurfaceRegistryError as exc:
        raise EvidenceLaneError(
            code="SKILL_MCP_ROUTING_INVALID",
            message=(
                "The configured skill/MCP root does not own the currently "
                "imported Evidence Lane runtime."
            ),
            details={
                "environment_variable": _PLUGIN_ROOT_ENV,
                "reason": str(exc),
                "cross_package_root_allowed": False,
            },
        ) from exc


SDK_NATIVE_ACTIONS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    (
        "canon_inspect",
        "Inspect Canon authority",
        "Inspect the project-isolated Canon Input authority, immutable envelopes, decisions, task graph, and continuity receipts without changing any authority.",
        "canon_input",
        "inspect",
        True,
    ),
    (
        "canon_inbox",
        "Inspect Canon inbox",
        "Read a bounded Canon inbox slice for one task or state set. Canon remains separate from Project Truth, Agent Learning, and State Travel.",
        "canon_input",
        "inbox",
        True,
    ),
    (
        "canon_graph",
        "Inspect Canon task graph",
        "Read the bounded upstream, downstream, and lateral Canon task graph with exact task UUID/deep-link bindings and no task creation.",
        "canon_input",
        "graph",
        True,
    ),
    (
        "canon_register_contract",
        "Register expected Canon contract",
        "Register one immutable receiver-side Canon contract. This admits no input and cannot promote Project Truth or Learning.",
        "canon_input",
        "register_contract",
        False,
    ),
    (
        "canon_seal_envelope",
        "Seal Canon envelope",
        "Seal one bounded upstream, downstream, or lateral Canon envelope with immutable source, destination, schema, contract, dependency, and return identities.",
        "canon_input",
        "seal_envelope",
        False,
    ),
    (
        "canon_receive",
        "Receive Canon envelope",
        "Receive one exact Canon envelope into the destination inbox without accepting it, executing work, or propagating any HIL or pointer decision.",
        "canon_input",
        "receive",
        False,
    ),
    (
        "canon_classify",
        "Classify Canon envelope",
        "Classify one received Canon envelope against the receiver's exact expected contract and determine whether its receiver-owned three-way Canon HIL is required.",
        "canon_input",
        "classify",
        False,
    ),
    (
        "canon_decide",
        "Decide Canon input",
        "Record exactly ACCEPT, REJECT, or MORE_RESEARCH at the receiver-owned Canon HIL. This never decides Project HIL, Learning HIL, Fuse, or a pointer.",
        "canon_input",
        "decide",
        False,
    ),
    (
        "canon_supersede",
        "Supersede Canon input",
        "Append an immutable supersession link for one Canon input while preserving the original envelope and decision history.",
        "canon_input",
        "supersede",
        False,
    ),
    (
        "canon_register_edge",
        "Register Canon task edge",
        "Register one dependency-safe Canon task edge without creating a host task or granting source, Git, HIL, install, or deploy authority.",
        "canon_input",
        "register_edge",
        False,
    ),
    (
        "canon_bind_edge",
        "Bind received Canon edge",
        "Bind one received Canon task edge after exact source, destination, contract, and cycle checks.",
        "canon_input",
        "bind_edge",
        False,
    ),
    (
        "canon_dispatch_linked_task",
        "Dispatch linked Canon task",
        "Dispatch exactly one TOP_LEVEL_TASK or explicitly authorized SUBAGENT through a supported host seam, then bind its exact UUID/deep link. Fail with HOST_CAPABILITY_UNAVAILABLE when the host seam is absent.",
        "canon_input",
        "dispatch_linked_task",
        False,
    ),
    (
        "canon_backfire_hil",
        "Raise Canon backfire",
        "Seal a bounded Canon backfire only for execution failure, missing source information, a new source requirement, or another linked-task input; the receiving task owns its three-way Canon HIL.",
        "canon_input",
        "backfire_hil",
        False,
    ),
    (
        "canon_seal_result",
        "Seal Canon task result",
        "Seal one bounded result for an existing Canon edge with exact evidence, schema, expiry, and source-pointer identities.",
        "canon_input",
        "seal_result",
        False,
    ),
    (
        "canon_seal_continuity",
        "Seal Canon State Travel continuity",
        "Seal Canon graph continuity for an independently authorized State Travel handoff; this does not prepare, resume, or authorize State Travel itself.",
        "canon_input",
        "seal_continuity",
        False,
    ),
    (
        "canon_restore_continuity",
        "Restore Canon State Travel continuity",
        "Restore one sealed Canon graph snapshot after exact destination binding. This cannot replay HIL, move a pointer, or create another destination.",
        "canon_input",
        "restore_continuity",
        False,
    ),
    (
        "learning_inspect",
        "Inspect Agent Learning",
        "Inspect the project-isolated Agent Learning authority, candidate ledger, pointer, and decisions without reading it as Project Truth.",
        "agent_learning",
        "inspect",
        True,
    ),
    (
        "learning_retrieve",
        "Retrieve accepted Agent Learning",
        "Retrieve a bounded project-isolated accepted Learning slice with scope, temporal, contradiction, and provenance receipts; never merge-rank it with Project Truth.",
        "agent_learning",
        "retrieve",
        True,
    ),
    (
        "learning_memory_query",
        "Query cross-sector memory",
        "Compatibility action name routed to the independent Project Memory SDK arm. Query one bounded project-isolated FTS5/BM25 locator slice across the governed sectors and authorities; raw databases and Markdown never enter the result.",
        "project_memory",
        "query",
        True,
    ),
    (
        "learning_memory_record_link",
        "Record cross-sector memory link",
        "Compatibility action name routed to the independent Project Memory SDK arm. Append one typed, content-addressed locator edge without storing raw source bytes, promoting a candidate, invoking HIL, or moving Project Truth.",
        "project_memory",
        "record_link",
        False,
    ),
    (
        "learning_record_host_memory_import",
        "Record host-memory provenance",
        "Explicitly seal one nonauthoritative host-memory provenance receipt and link only its bounded locator to the governed Plan context; automatic import and promotion remain forbidden.",
        "agent_learning",
        "record_host_memory_import",
        False,
    ),
    (
        "learning_seal_candidate",
        "Seal Agent Learning candidate",
        "Seal one evidence-backed project-isolated Learning candidate. It remains unaccepted and cannot change Project Truth.",
        "agent_learning",
        "seal_candidate",
        False,
    ),
    (
        "learning_decide_candidate",
        "Decide Agent Learning candidate",
        "Record one exact Learning six-way HIL decision against the Learning pointer only; Project Truth and the Project six-way HIL remain untouched.",
        "agent_learning",
        "decide_candidate",
        False,
    ),
    (
        "learning_revoke",
        "Revoke accepted Agent Learning",
        "Append one immutable revocation event for accepted project-isolated Learning without deleting history or changing Project Truth.",
        "agent_learning",
        "revoke",
        False,
    ),
)

SDK_NATIVE_READ_TOOL_NAMES = tuple(row[0] for row in SDK_NATIVE_ACTIONS if row[5])

if (
    len(SDK_NATIVE_ACTIONS) != len({row[0] for row in SDK_NATIVE_ACTIONS})
    or not set(SDK_NATIVE_READ_TOOL_NAMES).issubset(CODEX_READ_TOOL_NAMES)
    or len(CODEX_READ_TOOL_NAMES) != NATIVE_READ_TOOL_COUNT
    or NATIVE_TOOL_COUNT - NATIVE_READ_TOOL_COUNT != NATIVE_WRITE_TOOL_COUNT
):
    raise RuntimeError("Evidence Lane public-surface count contract drifted.")

_FULL_LIFECYCLE_INSTRUCTIONS = (
    "A prepared exact-work handoff makes /evi-state-travel eligible but "
    "never auto-selects or consumes it. Display and run State Travel only "
    "after an explicit user request or genuine host-context exhaustion. "
    "Otherwise start /evi with atomic /evi-boot plus locked ENV/UOP Flash "
    "as the first normal action, then display "
    "exactly Boot, Rollback, Build, Refresh, Mode, and Source Intake. "
    "Source Intake is one generalized ordered control for all eighteen "
    "lanes and Project Engulf and always includes Chat Lineage. Fuse "
    "requires exact APPROVE through pv_fuse and seals a fresh-window "
    "handoff without rebuilding. When explicitly triggered, State Travel "
    "verifies atomic Boot/Flash, the pointer base, any candidate, live "
    "source, Plan Lane, additive Deltas, and host execution profile in a "
    "fresh task. It resumes unfinished work at the exact row; an "
    "accepted-entry request waits. Codex Plan/Goal/task-panel controls remain "
    "bound to the exact governed task. A booted session remains active until "
    "/evi-exit-boot. "
    "Before every HIL or State Travel stop, visibly render the returned "
    "suggested_next_prompt. The host owns composer suggestions; never "
    "claim the MCP wrote the prompt bar and never auto-submit it. "
    "Never infer HIL approval, store private reasoning, expose connector "
    "secrets, or write remote Git without the exact governed action."
)


class _MCPExposureBoundary:
    """Apply the Codex-native authorization boundary before service invocation."""

    def __init__(
        self,
        application: EvidenceLaneService,
        exposure_profile: str,
        authorization_policy: OAuthToolAuthorizationPolicy | None = None,
    ) -> None:
        self._application = application
        self._exposure_profile = exposure_profile
        self._authorization_policy = authorization_policy

    def __getattr__(self, name: str) -> Any:
        return getattr(self._application, name)

    def invoke(
        self,
        tool_name: str,
        callback: Any,
        *args: Any,
        lifecycle: bool = False,
        authorization_project_id: str | None = None,
        authorization_session_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        callback_arguments: dict[str, Any] = {}
        try:
            callback_arguments = dict(
                inspect.signature(callback).bind_partial(*args, **kwargs).arguments
            )
        except (TypeError, ValueError):
            callback_arguments = {}
        project_id = (
            None
            if tool_name in _RUNTIME_GLOBAL_TOOL_NAMES
            else str(
                authorization_project_id
                if authorization_project_id is not None
                else callback_arguments.get("project_id")
                if callback_arguments.get("project_id") is not None
                else kwargs.get("project_id")
                if kwargs.get("project_id") is not None
                else args[0]
                if args
                else ""
            )
        )
        session_id = str(
            authorization_session_id
            if authorization_session_id is not None
            else callback_arguments.get("session_id")
            if callback_arguments.get("session_id") is not None
            else kwargs.get("session_id")
            if kwargs.get("session_id") is not None
            else ""
        )
        authorization_block, entry_binding = self.preflight(
            tool_name,
            lifecycle=lifecycle,
            project_id=project_id,
            session_id=session_id,
        )
        if authorization_block is not None:
            return cast(dict[str, Any], authorization_block)
        return self.invoke_preflighted(
            tool_name,
            callback,
            *args,
            lifecycle=lifecycle,
            entry_binding=cast(dict[str, Any], entry_binding),
            **kwargs,
        )

    def invoke_preflighted(
        self,
        tool_name: str,
        callback: Any,
        *args: Any,
        lifecycle: bool,
        entry_binding: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Enter one callback using the exact already-authorized receipt."""

        preflight_receipt_sha256 = str(entry_binding.get("receipt_sha256") or "")
        require(
            bool(preflight_receipt_sha256)
            and entry_binding.get("callback_entered") is False,
            "PUBLIC_ENTRY_PREFLIGHT_RECEIPT_REQUIRED",
            "A preflighted invocation requires the exact unentered callback receipt.",
            status="MISMATCH",
        )
        preflight_body = {
            key: value
            for key, value in entry_binding.items()
            if key != "receipt_sha256"
        }
        require(
            sha256_bytes(canonical_json_bytes(preflight_body))
            == preflight_receipt_sha256
            and preflight_body.get("tool_name") == tool_name
            and preflight_body.get("effect_class")
            == ("WRITE" if lifecycle else "READ"),
            "PUBLIC_ENTRY_PREFLIGHT_RECEIPT_INVALID",
            "The pre-callback public-entry receipt failed self or route verification.",
            status="MISMATCH",
        )
        completed_body = {
            **preflight_body,
            "callback_entered": True,
            "preflight_receipt_sha256": preflight_receipt_sha256,
        }
        completed_receipt = {
            **completed_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(completed_body)),
        }
        return self._application.invoke(
            tool_name,
            callback,
            *args,
            lifecycle=lifecycle,
            entry_authorization=completed_receipt,
            **kwargs,
        )

    def preflight(
        self,
        tool_name: str,
        *,
        lifecycle: bool,
        project_id: str | None,
        session_id: str | None,
    ) -> tuple[CallToolResult | None, dict[str, Any] | None]:
        """Authorize and attest exact entry identity before callback work."""

        authorization_block = self.authorize(
            tool_name,
            lifecycle=lifecycle,
            project_id=project_id,
        )
        if authorization_block is not None:
            return authorization_block, None
        receipt = self._application._public_entry_binding_receipt(
            tool_name=tool_name,
            lifecycle=lifecycle,
            project_id=project_id,
            session_id=session_id,
        )
        return None, receipt

    def authorize(
        self,
        tool_name: str,
        *,
        lifecycle: bool,
        project_id: str | None,
    ) -> CallToolResult | None:
        """Authorize before any wrapper-level project or session observation."""

        if self._authorization_policy is not None:
            try:
                self._authorization_policy.authorize_current_request(
                    tool_name=tool_name,
                    lifecycle=lifecycle,
                    project_id=project_id,
                )
            except OAuthAuthorizationError as error:
                return _oauth_authorization_result(
                    error,
                    config=self._authorization_policy.config,
                    tool_name=tool_name,
                    lifecycle=lifecycle,
                )
        return None


def _oauth_authorization_result(
    error: OAuthAuthorizationError,
    *,
    config: OAuthJWTConfig,
    tool_name: str,
    lifecycle: bool,
) -> CallToolResult:
    challenge_meta: dict[str, Any] | None = None
    if error.code == "AUTHENTICATED_OAUTH_CONTEXT_REQUIRED" or error.required_scopes:
        error_name = (
            "invalid_token"
            if error.code == "AUTHENTICATED_OAUTH_CONTEXT_REQUIRED"
            else "insufficient_scope"
        )
        description = (
            "A valid Evidence Lane OAuth access token is required."
            if error_name == "invalid_token"
            else "The access token lacks one or more scopes required by this tool."
        )
        metadata_url = str(build_resource_metadata_url(AnyHttpUrl(config.audience)))
        parameters = [
            f'error="{error_name}"',
            f'error_description="{description}"',
            f'resource_metadata="{metadata_url}"',
        ]
        if error.required_scopes:
            scope_value = " ".join(error.required_scopes)
            parameters.append(f'scope="{scope_value}"')
        challenge_meta = {"mcp/www_authenticate": ["Bearer " + ", ".join(parameters)]}

    structured = {
        "schema": "evidence-lane.oauth-authorization-block.v1",
        "status": "AUTHORIZATION_BLOCKED",
        "code": error.code,
        "requested_tool": tool_name,
        "lifecycle_action": lifecycle,
        "mutation_performed": False,
        "pointer_moved": False,
    }
    return CallToolResult(
        isError=True,
        content=[
            TextContent(
                type="text",
                text="Evidence Lane authorization blocked this tool without mutation.",
            )
        ],
        structuredContent=structured,
        _meta=challenge_meta,
    )


def _normalize_exposure_profile(value: str | None) -> str:
    normalized = (value or FULL_LIFECYCLE_EXPOSURE_PROFILE).strip().upper()
    aliases = {
        "": FULL_LIFECYCLE_EXPOSURE_PROFILE,
        "CODEX_FULL_LIFECYCLE": FULL_LIFECYCLE_EXPOSURE_PROFILE,
        # The version-bound Windows tunnel uses this transport-facing name.
        # It is still the same Codex-only full lifecycle surface; accepting the
        # alias does not revive either removed ChatGPT exposure profile.
        "CODEX_INTERACTIVE_SUPPORT": FULL_LIFECYCLE_EXPOSURE_PROFILE,
        FULL_LIFECYCLE_EXPOSURE_PROFILE: FULL_LIFECYCLE_EXPOSURE_PROFILE,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(
            "Unsupported EVIDENCE_LANE_MCP_EXPOSURE_PROFILE: " + normalized
        ) from exc


def _mcp_instructions(exposure_profile: str) -> str:
    if exposure_profile != FULL_LIFECYCLE_EXPOSURE_PROFILE:
        raise RuntimeError("Evidence Lane 2.0 supports only the Codex full lifecycle.")
    return _FULL_LIFECYCLE_INSTRUCTIONS


_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_HIL_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)
_REMOTE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


def _meta(label: str, done: str) -> dict[str, Any]:
    return {
        "openai/toolInvocation/invoking": label,
        "openai/toolInvocation/invoked": done,
    }


def _evidence_lane_icons(public_site_url: str) -> list[Icon]:
    return [
        Icon(
            src=f"{public_site_url.rstrip('/')}/evidence-lane-icon.png",
            mimeType="image/png",
            sizes=["256x256"],
        )
    ]


def _apply_evidence_lane_tool_icons(mcp: FastMCP, public_site_url: str) -> None:
    """Bind the stable Evidence Lane identity to every advertised tool record."""

    for tool in mcp._tool_manager.list_tools():
        tool.icons = _evidence_lane_icons(public_site_url)


class _EvidenceLaneFastMCP(FastMCP):
    """Expose current top-level tool security schemes plus the legacy mirror."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Keep structured receipts without duplicating their JSON into text."""

        result = await super().call_tool(name, arguments)
        return _compact_fastmcp_structured_result(name, result)

    async def list_tools(self) -> list[MCPTool]:
        listed = await super().list_tools()
        result: list[MCPTool] = []
        for tool in listed:
            payload = tool.model_dump(by_alias=True, exclude_none=True)
            security_schemes = (tool.meta or {}).get("securitySchemes")
            if security_schemes is not None:
                payload["securitySchemes"] = security_schemes
            result.append(MCPTool.model_validate(payload))
        return result


def _compact_fastmcp_structured_result(tool_name: str, result: Any) -> Any:
    """Replace FastMCP's duplicate JSON text with one fixed-size receipt."""

    if not (
        isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict)
    ):
        return result
    structured = cast(dict[str, Any], result[1])
    data = structured.get("data")
    data_status = data.get("status") if isinstance(data, dict) else None
    execution_status = str(
        structured.get("execution_status") or structured.get("status") or "PASS"
    )
    domain_status = str(
        structured.get("domain_status") or data_status or execution_status
    )
    text_receipt = {
        "schema": "evidence-lane.mcp-text-receipt.v1",
        "tool": tool_name,
        "status": execution_status,
        "execution_status": execution_status,
        "domain_status": domain_status,
        "structured_receipt_authoritative": True,
        "duplicate_structured_json_returned": False,
        "raw_payload_returned": False,
    }
    return (
        [
            TextContent(
                type="text",
                text=json.dumps(text_receipt, sort_keys=True, separators=(",", ":")),
            )
        ],
        structured,
    )


def _apply_governed_tool_failure_boundary(
    mcp: FastMCP,
    application: EvidenceLaneService,
) -> dict[str, Any]:
    """Convert every handler exception into the canonical fail-closed envelope."""

    wrapped_names: list[str] = []
    for tool in mcp._tool_manager.list_tools():
        original = tool.fn
        if getattr(original, "_evidence_lane_failure_boundary", False):
            wrapped_names.append(tool.name)
            continue
        lifecycle = bool(
            tool.annotations is not None
            and tool.annotations.readOnlyHint is False
        )

        def governed_handler(
            *args: Any,
            __original: Any = original,
            __tool_name: str = tool.name,
            __lifecycle: bool = lifecycle,
            **kwargs: Any,
        ) -> Any:
            try:
                return __original(*args, **kwargs)
            except Exception as error:  # noqa: BLE001
                return application._error(
                    __tool_name,
                    error,
                    lifecycle=__lifecycle,
                )

        governed_handler = cast(
            Any,
            wraps(cast(Any, original))(cast(Any, governed_handler)),
        )
        governed_handler._evidence_lane_failure_boundary = True  # type: ignore[attr-defined]
        tool.fn = cast(Any, governed_handler)
        wrapped_names.append(tool.name)

    ordered = sorted(wrapped_names)
    return {
        "schema": "evidence-lane.public-handler-failure-boundary.v1",
        "status": "PASS" if len(ordered) == NATIVE_TOOL_COUNT else "BLOCKED",
        "wrapped_handler_count": len(ordered),
        "wrapped_handler_names_sha256": hashlib.sha256(
            json.dumps(ordered, separators=(",", ":")).encode("utf-8")
        ).hexdigest().upper(),
        "uncaught_handler_exception_allowed": False,
        "handler_domain_status_rewritten": False,
    }


def _apply_oauth_tool_security_schemes(
    mcp: FastMCP,
    exposure_profile: str,
) -> None:
    for tool in mcp._tool_manager.list_tools():
        scopes = [READ_SCOPE]
        is_write = bool(
            tool.annotations is not None and tool.annotations.readOnlyHint is False
        )
        if is_write:
            scopes.append(WRITE_SCOPE)
            if tool.name in {"remote_git_prepare_push", "remote_git_execute_push"}:
                scopes.append(REMOTE_GIT_SCOPE)
        security_schemes = [{"type": "oauth2", "scopes": scopes}]
        tool.meta = {
            **(tool.meta or {}),
            "securitySchemes": security_schemes,
        }


def inspect_skill_mcp_routing(
    plugin_root: str | Path,
    registered_tool_names: Iterable[str],
) -> dict[str, Any]:
    """Fail closed unless every bundled skill has one exact MCP route."""

    root = Path(plugin_root).resolve()
    skills_root = root / "skills"
    manifest_path = skills_root / "evi" / "references" / "mcp-tool-routing.v1.json"
    require(
        skills_root.is_dir(),
        "SKILL_MCP_ROUTING_INVALID",
        "The bundled skills directory is missing.",
        skills_root=str(skills_root),
    )
    require(
        manifest_path.is_file(),
        "SKILL_MCP_ROUTING_INVALID",
        "The shared skill MCP routing manifest is missing.",
        manifest_path=str(manifest_path),
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceLaneError(
            code="SKILL_MCP_ROUTING_INVALID",
            message="The shared skill MCP routing manifest is unreadable.",
            details={"manifest_path": str(manifest_path), "error": str(exc)},
        ) from exc

    require(
        isinstance(manifest, dict)
        and manifest.get("schema") == SKILL_MCP_ROUTING_SCHEMA,
        "SKILL_MCP_ROUTING_INVALID",
        "The shared skill MCP routing schema is not supported.",
        expected_schema=SKILL_MCP_ROUTING_SCHEMA,
        observed_schema=(
            manifest.get("schema") if isinstance(manifest, dict) else None
        ),
    )
    manifest_format = "SEALED_WORKFLOW_STEPS_V1"
    flat_owner_snapshot: dict[str, str] | None = None
    if "tool_owners" in manifest:
        manifest_format = "FLAT_OWNER_GROUPS_V1"
        flat_owners = manifest.get("tool_owners")
        flat_workflows = manifest.get("workflows")
        flat_low_level = manifest.get("low_level_tools")
        flat_catalog_contract = manifest.get("catalog_contract")
        require(
            manifest.get("server_identity") == NATIVE_MCP_SERVER_IDENTITY
            and isinstance(flat_owners, dict)
            and isinstance(flat_workflows, dict)
            and isinstance(flat_low_level, dict)
            and isinstance(flat_catalog_contract, dict),
            "SKILL_MCP_ROUTING_INVALID",
            "The flat skill MCP routing manifest is incomplete.",
        )
        flat_owner_snapshot = dict(flat_owners)
        transformed_skills: dict[str, dict[str, Any]] = {}
        transformed_routes: dict[str, set[str]] = {}
        transformed_workflow_tools: dict[tuple[str, str], set[str]] = {}
        for skill_name, workflow in flat_workflows.items():
            groups = (
                workflow.get("ordered_tool_groups")
                if isinstance(workflow, dict)
                else None
            )
            require(
                isinstance(groups, list) and bool(groups),
                "SKILL_MCP_ROUTING_INVALID",
                "A flat skill route has no ordered tool groups.",
                skill=skill_name,
            )
            groups = cast(list[dict[str, Any]], groups)
            transformed_workflows: list[dict[str, Any]] = []
            for expected_order, group in enumerate(groups, start=1):
                tools = group.get("tools") if isinstance(group, dict) else None
                require(
                    group.get("order") == expected_order
                    and isinstance(tools, list)
                    and bool(tools)
                    and all(isinstance(tool, str) for tool in tools),
                    "SKILL_MCP_ROUTING_INVALID",
                    "A flat skill route group is not deterministic.",
                    skill=skill_name,
                    expected_order=expected_order,
                )
                tools = cast(list[str], tools)
                workflow_id = f"ordered-group-{expected_order}"
                step = {"tool": tools[0]} if len(tools) == 1 else {"one_of": tools}
                transformed_workflows.append({"id": workflow_id, "steps": [step]})
                transformed_workflow_tools[(skill_name, workflow_id)] = set(tools)
                for tool_name in tools:
                    transformed_routes.setdefault(tool_name, set()).add(skill_name)
            transformed_skills[skill_name] = {"workflows": transformed_workflows}

        transformed_low_level: dict[str, dict[str, Any]] = {}
        for tool_name, route in flat_low_level.items():
            owner_skill = route.get("owner_skill") if isinstance(route, dict) else None
            owner_workflow = next(
                (
                    workflow_id
                    for (
                        skill_name,
                        workflow_id,
                    ), tools in transformed_workflow_tools.items()
                    if skill_name == owner_skill and tool_name in tools
                ),
                None,
            )
            transformed_low_level[tool_name] = {
                "owner_skill": owner_skill,
                "workflow": owner_workflow,
                "reason": (
                    route.get("reached_via") if isinstance(route, dict) else None
                ),
            }
        transformed_public_tools = set(flat_owners) - set(transformed_low_level)
        transformed_shared_owners = {
            tool_name: flat_owners[tool_name]
            for tool_name in transformed_public_tools
            if len(transformed_routes.get(tool_name, set())) > 1
        }
        transformed_manifest: dict[str, Any] = {
            "schema": SKILL_MCP_ROUTING_SCHEMA,
            "server_dependency": {
                "type": "mcp",
                "value": NATIVE_MCP_SERVER_IDENTITY,
                "description": "Evidence Lane native MCP server",
            },
            "fail_closed_code": "MCP_ROUTING_FAIL_CLOSED",
            "catalog_contract": flat_catalog_contract,
            "shared_operation_owners": transformed_shared_owners,
            "skills": transformed_skills,
            "low_level_operations": transformed_low_level,
        }
        transformed_manifest["contract_sha256"] = (
            hashlib.sha256(
                json.dumps(
                    transformed_manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            .hexdigest()
            .upper()
        )
        manifest = transformed_manifest
    server_dependency = manifest.get("server_dependency")
    require(
        isinstance(server_dependency, dict)
        and server_dependency.get("type") == "mcp"
        and server_dependency.get("value") == NATIVE_MCP_SERVER_IDENTITY
        and server_dependency.get("description") == "Evidence Lane native MCP server",
        "SKILL_MCP_ROUTING_INVALID",
        "The skill MCP dependency does not bind the native server identity.",
        expected_server=NATIVE_MCP_SERVER_IDENTITY,
        observed_dependency=server_dependency,
    )

    registered = sorted(str(name) for name in registered_tool_names)
    require(
        len(registered) == len(set(registered)),
        "SKILL_MCP_ROUTING_INVALID",
        "The registered MCP catalog contains duplicate names.",
    )
    skill_names = sorted(
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    skill_routes = manifest.get("skills")
    low_level_operations = manifest.get("low_level_operations")
    shared_operation_owners = manifest.get("shared_operation_owners")
    catalog_contract = manifest.get("catalog_contract")
    require(
        isinstance(skill_routes, dict)
        and isinstance(low_level_operations, dict)
        and isinstance(shared_operation_owners, dict)
        and isinstance(catalog_contract, dict),
        "SKILL_MCP_ROUTING_INVALID",
        "The routing manifest is missing a required object.",
    )

    registered_set = set(registered)
    require(
        set(skill_routes) == set(skill_names),
        "SKILL_MCP_ROUTING_INVALID",
        "Every bundled skill must have exactly one declared MCP route.",
        missing_skills=sorted(set(skill_names) - set(skill_routes)),
        unexpected_skills=sorted(set(skill_routes) - set(skill_names)),
    )
    require(
        catalog_contract.get("tool_count") == len(registered)
        and catalog_contract.get("missing_tool_behavior")
        == "FAIL_CLOSED_NO_ALIAS_NO_PREFIX_REWRITE"
        and catalog_contract.get("host_display_namespace_is_authority") is False,
        "SKILL_MCP_ROUTING_INVALID",
        "The routing catalog contract does not match the registered package catalog.",
        expected_tool_count=len(registered),
        observed_tool_count=catalog_contract.get("tool_count"),
    )

    observed_contract_sha256 = manifest.get("contract_sha256")
    unsigned_manifest = {
        key: value for key, value in manifest.items() if key != "contract_sha256"
    }
    expected_contract_sha256 = (
        hashlib.sha256(
            json.dumps(
                unsigned_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest()
        .upper()
    )
    require(
        observed_contract_sha256 == expected_contract_sha256,
        "SKILL_MCP_ROUTING_INVALID",
        "The shared skill MCP routing contract seal does not match its content.",
        expected_contract_sha256=expected_contract_sha256,
        observed_contract_sha256=observed_contract_sha256,
    )

    routed_by_tool: dict[str, set[str]] = {}
    workflow_tools: dict[tuple[str, str], set[str]] = {}
    workflows: dict[str, dict[str, Any]] = {}
    for skill_name in skill_names:
        route = skill_routes[skill_name]
        route_workflows = route.get("workflows") if isinstance(route, dict) else None
        require(
            isinstance(route_workflows, list) and bool(route_workflows),
            "SKILL_MCP_ROUTING_INVALID",
            "A skill MCP route has no ordered workflows.",
            skill=skill_name,
        )
        route_workflows = cast(list[dict[str, Any]], route_workflows)
        normalized_groups: list[dict[str, Any]] = []
        workflow_ids: set[str] = set()
        order = 1
        for workflow in route_workflows:
            require(
                isinstance(workflow, dict)
                and isinstance(workflow.get("id"), str)
                and bool(workflow["id"].strip())
                and workflow["id"] not in workflow_ids
                and isinstance(workflow.get("steps"), list)
                and bool(workflow["steps"]),
                "SKILL_MCP_ROUTING_INVALID",
                "A skill MCP workflow must have a unique ID and ordered steps.",
                skill=skill_name,
            )
            workflow_id = cast(str, workflow["id"])
            steps = cast(list[dict[str, Any]], workflow["steps"])
            workflow_ids.add(workflow_id)
            exact_workflow_tools: set[str] = set()
            for step in steps:
                require(
                    isinstance(step, dict)
                    and set(step).issubset({"tool", "one_of"})
                    and ("tool" in step) != ("one_of" in step),
                    "SKILL_MCP_ROUTING_INVALID",
                    "A workflow step must name one exact tool or one alternative set.",
                    skill=skill_name,
                    workflow=workflow_id,
                )
                step_tools = [step["tool"]] if "tool" in step else step["one_of"]
                require(
                    isinstance(step_tools, list)
                    and bool(step_tools)
                    and all(isinstance(tool, str) for tool in step_tools),
                    "SKILL_MCP_ROUTING_INVALID",
                    "A workflow step contains an invalid exact tool name.",
                    skill=skill_name,
                    workflow=workflow_id,
                )
                step_tools = cast(list[str], step_tools)
                normalized_groups.append({"order": order, "tools": step_tools})
                order += 1
                exact_workflow_tools.update(step_tools)
                for tool_name in step_tools:
                    routed_by_tool.setdefault(tool_name, set()).add(skill_name)
            workflow_tools[(skill_name, workflow_id)] = exact_workflow_tools
        workflows[skill_name] = {
            "ordered_tool_groups": normalized_groups,
            "missing_tool_behavior": catalog_contract["missing_tool_behavior"],
        }

    require(
        set(routed_by_tool) == registered_set,
        "SKILL_MCP_ROUTING_INVALID",
        "The declared workflows must route every registered tool.",
        missing_tools=sorted(registered_set - set(routed_by_tool)),
        unexpected_tools=sorted(set(routed_by_tool) - registered_set),
    )
    require(
        set(low_level_operations).issubset(registered_set),
        "SKILL_MCP_ROUTING_INVALID",
        "A documented low-level tool is not registered.",
        missing_tools=sorted(set(low_level_operations) - registered_set),
    )
    low_level_tools: dict[str, dict[str, str]] = {}
    for tool_name, route in low_level_operations.items():
        owner_skill = route.get("owner_skill") if isinstance(route, dict) else None
        owner_workflow = route.get("workflow") if isinstance(route, dict) else None
        require(
            isinstance(route, dict)
            and owner_skill in skill_names
            and isinstance(owner_workflow, str)
            and tool_name in workflow_tools.get((owner_skill, owner_workflow), set())
            and isinstance(route.get("reason"), str)
            and bool(route["reason"].strip()),
            "SKILL_MCP_ROUTING_INVALID",
            "A low-level tool does not document its owning public workflow.",
            tool=tool_name,
            owner_skill=owner_skill,
            owner_workflow=owner_workflow,
        )
        owner_skill = cast(str, owner_skill)
        reached_via = cast(str, route["reason"])
        low_level_tools[tool_name] = {
            "owner_skill": owner_skill,
            "reached_via": reached_via,
        }

    public_tools = registered_set - set(low_level_tools)
    shared_tools = {
        tool_name for tool_name in public_tools if len(routed_by_tool[tool_name]) > 1
    }
    require(
        set(shared_operation_owners) == shared_tools,
        "SKILL_MCP_ROUTING_INVALID",
        "Every shared public operation must declare exactly one primary owner.",
        missing_shared_owners=sorted(shared_tools - set(shared_operation_owners)),
        unexpected_shared_owners=sorted(set(shared_operation_owners) - shared_tools),
    )
    owners: dict[str, str] = {}
    for tool_name in registered:
        if tool_name in low_level_tools:
            owner_skill = low_level_tools[tool_name]["owner_skill"]
        elif tool_name in shared_operation_owners:
            owner_skill = shared_operation_owners[tool_name]
        else:
            routed_skills = routed_by_tool[tool_name]
            require(
                len(routed_skills) == 1,
                "SKILL_MCP_ROUTING_INVALID",
                "A public operation does not have one deterministic owner.",
                tool=tool_name,
                routed_skills=sorted(routed_skills),
            )
            owner_skill = next(iter(routed_skills))
        require(
            owner_skill in routed_by_tool[tool_name],
            "SKILL_MCP_ROUTING_INVALID",
            "A declared primary owner does not route its operation.",
            tool=tool_name,
            owner_skill=owner_skill,
        )
        owners[tool_name] = owner_skill
    if flat_owner_snapshot is not None:
        require(
            owners == flat_owner_snapshot,
            "SKILL_MCP_ROUTING_INVALID",
            "The flat routing manifest must own every registered tool exactly once.",
            missing_tools=sorted(set(owners) - set(flat_owner_snapshot)),
            unexpected_tools=sorted(set(flat_owner_snapshot) - set(owners)),
            mismatched_owners=sorted(
                tool_name
                for tool_name in set(owners) & set(flat_owner_snapshot)
                if owners[tool_name] != flat_owner_snapshot[tool_name]
            ),
        )

    dependency_fragment = (
        "dependencies:\n"
        "  tools:\n"
        '    - type: "mcp"\n'
        '      value: "evidence-lane"\n'
        '      description: "Evidence Lane native MCP server"'
    )
    workflow_tool_counts: dict[str, int] = {}
    for skill_name in skill_names:
        skill_path = skills_root / skill_name / "SKILL.md"
        openai_path = skills_root / skill_name / "agents" / "openai.yaml"
        require(
            openai_path.is_file(),
            "SKILL_MCP_ROUTING_INVALID",
            "A bundled skill is missing agents/openai.yaml.",
            skill=skill_name,
            openai_yaml=str(openai_path),
        )
        try:
            skill_text = skill_path.read_text(encoding="utf-8")
            openai_text = openai_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        except OSError as exc:
            raise EvidenceLaneError(
                code="SKILL_MCP_ROUTING_INVALID",
                message="A bundled skill routing file is unreadable.",
                details={"skill": skill_name, "error": str(exc)},
            ) from exc
        require(
            openai_text.count(dependency_fragment) == 1
            and "transport:" not in openai_text
            and "url:" not in openai_text,
            "SKILL_MCP_ROUTING_INVALID",
            "A skill must declare exactly one bundled evidence-lane MCP dependency.",
            skill=skill_name,
        )
        require(
            "mcp-tool-routing.v1.json" in skill_text
            and "MCP_ROUTING_FAIL_CLOSED" in skill_text,
            "SKILL_MCP_ROUTING_INVALID",
            "A skill does not declare the shared fail-closed routing contract.",
            skill=skill_name,
        )

        workflow = workflows[skill_name]
        skill_groups = (
            workflow.get("ordered_tool_groups") if isinstance(workflow, dict) else None
        )
        require(
            isinstance(skill_groups, list) and bool(skill_groups),
            "SKILL_MCP_ROUTING_INVALID",
            "A skill MCP workflow has no ordered tool groups.",
            skill=skill_name,
        )
        skill_groups = cast(list[dict[str, Any]], skill_groups)
        orders = [group.get("order") for group in skill_groups]
        require(
            len(orders) == len(skill_groups)
            and orders == list(range(1, len(skill_groups) + 1)),
            "SKILL_MCP_ROUTING_INVALID",
            "A skill MCP workflow order is not deterministic.",
            skill=skill_name,
            observed_orders=orders,
        )
        routed: list[str] = []
        for group in skill_groups:
            tools = group.get("tools")
            require(
                isinstance(tools, list)
                and bool(tools)
                and all(isinstance(tool, str) for tool in tools),
                "SKILL_MCP_ROUTING_INVALID",
                "A skill MCP workflow group must name one or more exact tools.",
                skill=skill_name,
                order=group.get("order"),
            )
            tools = cast(list[str], tools)
            routed.extend(tools)
        require(
            set(routed).issubset(registered_set),
            "SKILL_MCP_ROUTING_INVALID",
            "A skill MCP workflow names an unavailable tool.",
            skill=skill_name,
            missing_tools=sorted(set(routed) - registered_set),
        )
        owned = {tool for tool, owner in owners.items() if owner == skill_name}
        require(
            owned.issubset(set(routed)),
            "SKILL_MCP_ROUTING_INVALID",
            "An owning skill workflow does not route all of its tools.",
            skill=skill_name,
            missing_owned_tools=sorted(owned - set(routed)),
        )
        require(
            isinstance(workflow.get("missing_tool_behavior"), str)
            and workflow["missing_tool_behavior"].startswith("FAIL_CLOSED"),
            "SKILL_MCP_ROUTING_INVALID",
            "A skill workflow does not define fail-closed missing-tool behavior.",
            skill=skill_name,
        )
        workflow_tool_counts[skill_name] = len(set(routed))

    require(
        set(low_level_tools).issubset(registered_set),
        "SKILL_MCP_ROUTING_INVALID",
        "A documented low-level tool is not registered.",
        missing_tools=sorted(set(low_level_tools) - registered_set),
    )
    for tool_name, route in low_level_tools.items():
        require(
            isinstance(route, dict)
            and route.get("owner_skill") == owners[tool_name]
            and isinstance(route.get("reached_via"), str)
            and bool(route["reached_via"].strip()),
            "SKILL_MCP_ROUTING_INVALID",
            "A low-level tool does not document its owning public workflow.",
            tool=tool_name,
        )

    receipt: dict[str, Any] = {
        "schema": "evidence-lane.skill-mcp-routing-receipt.v1",
        "status": "PASS",
        "routing_schema": SKILL_MCP_ROUTING_SCHEMA,
        "manifest_format": manifest_format,
        "server_identity": NATIVE_MCP_SERVER_IDENTITY,
        "skill_count": len(skill_names),
        "tool_count": len(registered),
        "owned_tool_count": len(owners),
        "low_level_tool_count": len(low_level_tools),
        "workflow_tool_counts": workflow_tool_counts,
        "missing_tool_behavior": catalog_contract["missing_tool_behavior"],
        "installed_validation": catalog_contract.get("installed_validation"),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes())
        .hexdigest()
        .upper(),
    }
    canonical = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt["receipt_sha256"] = hashlib.sha256(canonical).hexdigest().upper()
    return receipt


_PUBLIC_TOOL_EVALUATION_CASES = (
    "representative",
    "edge",
    "missing",
    "empty",
    "auth",
    "write_confirmation",
    "unsupported",
)


def _tool_schema_case_value(
    schema: dict[str, Any],
    *,
    edge: bool,
) -> Any:
    """Build one deterministic, non-secret argument value from JSON Schema."""

    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[-1 if edge else 0]
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        candidates = [
            row for row in any_of if isinstance(row, dict) and row.get("type") != "null"
        ]
        if candidates:
            return _tool_schema_case_value(
                candidates[-1 if edge else 0],
                edge=edge,
            )
    schema_type = schema.get("type")
    if schema_type == "string":
        return "" if edge else "value"
    if schema_type == "integer":
        return 0 if edge else 1
    if schema_type == "number":
        return 0.0 if edge else 1.0
    if schema_type == "boolean":
        return not edge
    if schema_type == "array":
        if edge:
            return []
        items = schema.get("items")
        return [
            _tool_schema_case_value(
                items if isinstance(items, dict) else {"type": "string"},
                edge=False,
            )
        ]
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(properties, dict) and isinstance(required, list):
            return {
                name: _tool_schema_case_value(
                    cast(dict[str, Any], properties[name]),
                    edge=edge,
                )
                for name in required
                if isinstance(properties.get(name), dict)
            }
        return {} if edge else {"key": "value"}
    return None


def _tool_arguments_validate(tool: Any, arguments: dict[str, Any]) -> bool:
    """Validate arguments without invoking a public tool or causing writes."""

    try:
        tool.fn_metadata.arg_model.model_validate(arguments)
    except ValidationError:
        return False
    return True


def build_public_tool_evaluation_matrix(
    mcp: FastMCP,
    *,
    oauth_enabled: bool,
) -> dict[str, Any]:
    """Evaluate every exposed MCP contract without executing write handlers.

    The full per-tool matrix remains an in-process verification artifact. Native
    receipts expose only counts and hashes so a catalog check cannot unload the
    catalog or its schemas into the model context.
    """

    tools = sorted(mcp._tool_manager.list_tools(), key=lambda item: item.name)
    tool_names = {tool.name for tool in tools}
    records: list[dict[str, Any]] = []
    untested: list[str] = []
    input_schema_records: list[dict[str, str]] = []
    output_schema_records: list[dict[str, str]] = []
    for tool in tools:
        input_schema = cast(dict[str, Any], tool.parameters)
        output_schema = tool.output_schema
        properties = cast(
            dict[str, dict[str, Any]],
            input_schema.get("properties") or {},
        )
        required = [
            str(name) for name in cast(list[Any], input_schema.get("required") or [])
        ]
        representative = {
            name: _tool_schema_case_value(properties[name], edge=False)
            for name in required
        }
        edge = {
            name: _tool_schema_case_value(properties[name], edge=True)
            for name in required
        }
        representative_pass = _tool_arguments_validate(tool, representative)
        edge_pass = _tool_arguments_validate(tool, edge)
        if required:
            missing_pass = all(
                not _tool_arguments_validate(
                    tool,
                    {
                        name: value
                        for name, value in representative.items()
                        if name != omitted
                    },
                )
                for omitted in required
            )
        else:
            missing_pass = _tool_arguments_validate(tool, {})
        empty_pass = _tool_arguments_validate(tool, {}) is (not required)

        annotations = (
            tool.annotations.model_dump(exclude_none=True)
            if tool.annotations is not None
            else {}
        )
        read_only = annotations.get("readOnlyHint") is not False
        expected_scopes = [READ_SCOPE]
        if not read_only:
            expected_scopes.append(WRITE_SCOPE)
            if tool.name in {
                "remote_git_prepare_push",
                "remote_git_execute_push",
            }:
                expected_scopes.append(REMOTE_GIT_SCOPE)
        security_schemes = (tool.meta or {}).get("securitySchemes")
        expected_security = [{"type": "oauth2", "scopes": expected_scopes}]
        auth_pass = (
            security_schemes == expected_security
            if oauth_enabled
            else security_schemes is None
        )
        write_confirmation_pass = (
            annotations.get("readOnlyHint") is True
            if read_only
            else annotations.get("readOnlyHint") is False
            and (not oauth_enabled or WRITE_SCOPE in expected_scopes)
        )
        unsupported_pass = (
            f"{tool.name}__unsupported" not in tool_names
            and mcp._tool_manager.get_tool(f"{tool.name}__unsupported") is None
        )
        cases = {
            "representative": representative_pass,
            "edge": edge_pass,
            "missing": missing_pass,
            "empty": empty_pass,
            "auth": auth_pass,
            "write_confirmation": write_confirmation_pass,
            "unsupported": unsupported_pass,
        }
        input_schema_sha256 = (
            hashlib.sha256(
                json.dumps(
                    input_schema,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            .hexdigest()
            .upper()
        )
        output_schema_sha256 = (
            hashlib.sha256(
                json.dumps(
                    output_schema,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            .hexdigest()
            .upper()
        )
        metadata = {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "annotations": annotations,
            "meta": tool.meta,
            "input_schema_sha256": input_schema_sha256,
            "output_schema_sha256": output_schema_sha256,
        }
        metadata_sha256 = (
            hashlib.sha256(
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            .hexdigest()
            .upper()
        )
        record = {
            "name": tool.name,
            "input_schema_sha256": input_schema_sha256,
            "output_schema_sha256": output_schema_sha256,
            "metadata_sha256": metadata_sha256,
            "read_only": read_only,
            "required_argument_count": len(required),
            "cases": {
                name: "PASS" if cases[name] else "BLOCKED"
                for name in _PUBLIC_TOOL_EVALUATION_CASES
            },
        }
        if not all(cases.values()):
            untested.append(tool.name)
        records.append(record)
        input_schema_records.append({"name": tool.name, "sha256": input_schema_sha256})
        output_schema_records.append(
            {"name": tool.name, "sha256": output_schema_sha256}
        )
    records_canonical = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": "evidence-lane.public-tool-evaluation-matrix.v1",
        "status": "PASS" if not untested else "BLOCKED",
        "tool_count": len(records),
        "schema_metadata_sealed_count": len(records),
        "case_classes": list(_PUBLIC_TOOL_EVALUATION_CASES),
        "case_evaluation_count": len(records) * len(_PUBLIC_TOOL_EVALUATION_CASES),
        "read_tool_count": sum(record["read_only"] for record in records),
        "write_tool_count": sum(not record["read_only"] for record in records),
        "oauth_enabled": oauth_enabled,
        "handler_invocation_mode": "SCHEMA_AND_REGISTRY_ONLY_NO_WRITE_EXECUTION",
        "side_effect_free": True,
        "raw_tool_schemas_returned": False,
        "untested_public_tools": untested,
        "input_schema_inventory_sha256": hashlib.sha256(
            json.dumps(
                input_schema_records,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest()
        .upper(),
        "output_schema_inventory_sha256": hashlib.sha256(
            json.dumps(
                output_schema_records,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest()
        .upper(),
        "matrix_sha256": hashlib.sha256(records_canonical).hexdigest().upper(),
        "records": records,
    }


def _native_route_receipt(
    mcp: FastMCP,
    exposure_profile: str,
    oauth_config: OAuthJWTConfig | None = None,
) -> dict[str, Any]:
    """Seal the exact native catalog without treating a host prefix as identity."""

    tools = sorted(mcp._tool_manager.list_tools(), key=lambda item: item.name)
    names = [tool.name for tool in tools]
    public_surface = derive_public_surface_registry(resolve_skill_mcp_plugin_root())
    registered_read_names = sorted(
        tool.name
        for tool in tools
        if tool.annotations is None or tool.annotations.readOnlyHint is not False
    )
    registered_write_names = sorted(set(names) - set(registered_read_names))
    catalog = [
        {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "input_schema": tool.parameters,
            "output_schema": tool.output_schema,
            "icons": [
                icon.model_dump(by_alias=True, exclude_none=True)
                for icon in (tool.icons or [])
            ],
            "annotations": (
                tool.annotations.model_dump(exclude_none=True)
                if tool.annotations is not None
                else None
            ),
            "meta": tool.meta,
        }
        for tool in tools
    ]
    canonical = json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    project_scoped_tools = [
        tool for tool in tools if tool.name not in _RUNTIME_GLOBAL_TOOL_NAMES
    ]
    missing_project_route = sorted(
        tool.name
        for tool in project_scoped_tools
        if "project_id" not in set(tool.parameters.get("required") or [])
    )
    tool_evaluation = build_public_tool_evaluation_matrix(
        mcp,
        oauth_enabled=oauth_config is not None,
    )
    mcp._evidence_lane_public_tool_evaluation_matrix = tool_evaluation  # type: ignore[attr-defined]
    catalog_valid = (
        len(names) == len(set(names))
        and not missing_project_route
        and tool_evaluation["status"] == "PASS"
        and public_surface["status"] == "PASS"
        and names == public_surface["tools"]["names"]
        and registered_read_names == public_surface["tools"]["read_names"]
        and registered_write_names == public_surface["tools"]["write_names"]
    )
    return {
        "schema": "evidence-lane.native-mcp-route-receipt.v1",
        "status": "PASS" if catalog_valid else "BLOCKED",
        "server_identity": NATIVE_MCP_SERVER_IDENTITY,
        "canonical_tool_namespace": NATIVE_MCP_TOOL_NAMESPACE,
        "exposure_profile": exposure_profile,
        "tool_count": len(names),
        "read_tool_count": len(registered_read_names),
        "write_tool_count": len(registered_write_names),
        "skill_count": public_surface["catalog"]["skills"],
        "command_count": public_surface["catalog"]["commands"],
        "hook_event_count": public_surface["catalog"]["hook_events"],
        "hook_handler_count": public_surface["catalog"]["hook_handlers"],
        "provider_count": public_surface["catalog"]["providers"],
        "public_surface_registry": {
            "schema": public_surface["schema"],
            "status": public_surface["status"],
            "registry_sha256": public_surface["registry_sha256"],
            "route_law": public_surface["route_law"],
            "package_identity": public_surface["package_identity"],
            "release_catalog_matches_derived": public_surface[
                "release_catalog_matches_derived"
            ],
            "routing_catalog_matches_derived": public_surface[
                "routing_catalog_matches_derived"
            ],
        },
        "tool_names_unique": len(names) == len(set(names)),
        "runtime_global_tool_count": len(
            [name for name in names if name in _RUNTIME_GLOBAL_TOOL_NAMES]
        ),
        "project_scoped_tool_count": len(project_scoped_tools),
        "project_route_argument": "project_id",
        "project_route_argument_required": not missing_project_route,
        "project_route_schema_status": (
            "PASS" if not missing_project_route else "BLOCKED"
        ),
        "project_scoped_tools_missing_project_id": missing_project_route,
        "transport_project_binding": (
            "OAUTH_SUBJECT_CLIENT_ENVIRONMENT_ROLE_AND_EXACT_PROJECT"
            if oauth_config is not None
            else "NONE_TRANSPORT_ONLY"
        ),
        "project_resolution": (
            "EXACT_PROJECT_ID_TO_CONFIGURED_ROOT_PROJECTS_SUBDIRECTORY"
        ),
        "cross_project_fallback_allowed": False,
        "oauth_authorization_policy": {
            "enabled": oauth_config is not None,
            "base_scope": READ_SCOPE if oauth_config is not None else None,
            "per_tool_security_schemes": oauth_config is not None,
            "deployment_environment": (
                oauth_config.deployment_environment
                if oauth_config is not None
                else None
            ),
            "allowed_client_count": (
                len(oauth_config.allowed_client_ids) if oauth_config is not None else 0
            ),
            "roles_claim": (
                oauth_config.roles_claim if oauth_config is not None else None
            ),
            "projects_claim": (
                oauth_config.projects_claim if oauth_config is not None else None
            ),
            "environment_claim": (
                oauth_config.environment_claim if oauth_config is not None else None
            ),
            "production_lifecycle_owner_only": oauth_config is not None,
            "remote_git_owner_only": oauth_config is not None,
            "remote_git_scope": (
                REMOTE_GIT_SCOPE if oauth_config is not None else None
            ),
        },
        "tool_catalog_sha256": hashlib.sha256(canonical).hexdigest().upper(),
        "public_tool_evaluation": {
            key: value for key, value in tool_evaluation.items() if key != "records"
        },
        "mcp_apps_resource_uri": GOVERNED_PANEL_URI,
        "host_display_namespace_is_authority": False,
        "accepted_display_namespaces": [
            "evidence_lane",
            "evidence_lane_<8-to-64-lowercase-hex-collision-suffix>",
        ],
        "rejected_lifecycle_surfaces": [
            "codex_apps",
            "google_drive",
            "plugin_runtime",
            "external_connector",
            "network_tunnel",
            "legacy_version_namespace",
        ],
        "surface_placement": {
            "codex": "NATIVE_PLUGIN_FULL_LIFECYCLE_ONLY",
            "external_connector_inside_codex_allowed": False,
        },
        "catalog_reload_required_after_package_change": True,
    }


def create_mcp_server(
    *,
    service: EvidenceLaneService | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    bearer_token: str | None = None,
    base_url: str | None = None,
    oauth_config: OAuthJWTConfig | None = None,
    public_site_url: str | None = None,
    allowed_tool_names: str | tuple[str, ...] | list[str] | None = None,
    exposure_profile: str | None = None,
    canon_dispatcher: CanonTaskDispatcher | None = None,
) -> FastMCP:
    backend_application = service or EvidenceLaneService()
    service_route_review = inspect_service_route_parity()
    release_identity = backend_application.engine.doctor()["engine"]
    exact_exposure_profile = _normalize_exposure_profile(exposure_profile)
    authorization_policy = (
        OAuthToolAuthorizationPolicy(oauth_config) if oauth_config is not None else None
    )
    application = _MCPExposureBoundary(
        backend_application,
        exact_exposure_profile,
        authorization_policy,
    )
    effective_allowed_tool_names = allowed_tool_names
    auth = None
    verifier: TokenVerifier | None = None
    if bearer_token and oauth_config:
        raise ValueError("Choose either static bearer or OAuth JWT authentication.")
    if bearer_token:
        exact_base = (base_url or f"http://{host}:{port}").rstrip("/")
        exact_resource = f"{exact_base}/mcp"
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(f"{exact_base}/"),
            resource_server_url=AnyHttpUrl(exact_resource),
            required_scopes=["evidence-lane:read"],
        )
        verifier = StaticBearerVerifier(bearer_token)
    elif oauth_config:
        exact_base = (base_url or f"http://{host}:{port}").rstrip("/")
        exact_resource = f"{exact_base}/mcp"
        if oauth_config.audience != exact_resource:
            raise ValueError(
                "OAuth audience must exactly equal the externally visible MCP "
                f"resource URL: {exact_resource}"
            )
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(oauth_config.issuer_url),
            resource_server_url=AnyHttpUrl(exact_resource),
            required_scopes=list(oauth_config.required_scopes),
        )
        verifier = OAuthJWTVerifier(oauth_config)
    exact_public_site = (
        public_site_url
        or os.environ.get("EVIDENCE_LANE_PUBLIC_SITE_URL")
        or _PUBLIC_SITE_URL
    ).rstrip("/")
    mcp = _EvidenceLaneFastMCP(
        "Evidence Lane",
        instructions=_mcp_instructions(exact_exposure_profile),
        website_url=exact_public_site,
        icons=_evidence_lane_icons(exact_public_site),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=False,
        auth=auth,
        token_verifier=verifier,
    )
    # FastMCP 1.28.1 exposes website/icons but not its low-level server version.
    # Set the same pinned engine identity that clients read from pyproject.toml
    # instead of allowing the SDK's default 1.0.0 to leak into Codex metadata.
    mcp._mcp_server.version = ENGINE_VERSION

    def route_aware_doctor() -> dict[str, Any]:
        doctor = application.doctor()
        receipt = getattr(mcp, "_evidence_lane_native_route_receipt", None)
        return {
            **doctor,
            "mcp_route_identity": receipt
            or {
                "schema": "evidence-lane.native-mcp-route-receipt.v1",
                "status": "BLOCKED",
                "server_identity": NATIVE_MCP_SERVER_IDENTITY,
                "reason": "NATIVE_TOOL_CATALOG_NOT_FINALIZED",
            },
        }

    @mcp.custom_route(
        "/healthz",
        methods=["GET"],
        name="evidence-lane-health",
        include_in_schema=False,
    )
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "PASS",
                "service": "evidence-lane-plugin",
                "mcp_path": "/mcp",
                "release_sha": release_identity.get("commit"),
                "engine_version": release_identity.get("release"),
                "package_sha256": release_identity.get("package_sha256"),
                "mcp_route_identity": getattr(
                    mcp, "_evidence_lane_native_route_receipt", None
                ),
            }
        )

    @mcp.resource(
        GOVERNED_PANEL_URI,
        name="evidence-lane-governed-console",
        title="Evidence Lane governed console",
        description=(
            "Portable read-only MCP Apps interface for verified runtime, lane, "
            "accepted-pointer, candidate, and HIL facts."
        ),
        mime_type=MCP_APP_MIME_TYPE,
        icons=_evidence_lane_icons(exact_public_site),
        meta=governed_panel_resource_meta(exact_public_site),
    )
    def evidence_lane_governed_console() -> str:
        return governed_panel_html(exact_public_site)

    @mcp.tool(
        name="runtime_doctor",
        title="Check Evidence Lane runtime",
        description=(
            "Check Git, Python, SQLite FTS5, schema availability, durable local "
            "storage, engine identity, and optional Drive configuration. Performs "
            "no repository or pointer mutation."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Checking Evidence Lane runtime", "Runtime check complete"),
        structured_output=True,
    )
    def runtime_doctor() -> dict[str, Any]:
        return application.invoke("runtime_doctor", route_aware_doctor)

    @mcp.tool(
        name="session_flash_status",
        title="Inspect Evidence Lane session flash",
        description=(
            "Verify the exact locked ENV15/UOP15 authority members, Mermaid hashes, "
            "read-only SQLite integrity, source-packet warning, and installation "
            "flash receipt without creating or changing the receipt."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Checking session flash", "Session flash status ready"),
        structured_output=True,
    )
    def session_flash_status() -> dict[str, Any]:
        return application.invoke(
            "session_flash_status", application.session_flash_status
        )

    @mcp.tool(
        name="runtime_activation_status",
        title="Inspect Evidence Lane runtime attachment",
        description=(
            "Read whether ENV/UOP Flash context and visible prompt/response capture "
            "are attached to governed sessions. DETACHED preserves the installed "
            "plugin, Flash verification receipt, immutable store, and pointer."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Checking runtime attachment", "Runtime attachment ready"),
        structured_output=True,
    )
    def runtime_activation_status() -> dict[str, Any]:
        return application.invoke(
            "runtime_activation_status", application.runtime_activation_status
        )

    @mcp.tool(
        name="lifecycle_transition_law",
        title="Read the canonical lifecycle law",
        description=(
            "Return the single executable event/from/to transition table used by "
            "session boot, PV build, task classification, Refresh, six-way HIL, "
            "rollback state travel, and fresh-window exact-work handoff."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading lifecycle law", "Lifecycle law ready"),
        structured_output=True,
    )
    def lifecycle_transition_law() -> dict[str, Any]:
        return application.invoke(
            "lifecycle_transition_law", application.transition_law
        )

    @mcp.tool(
        name="lane_catalog",
        title="List universal Evidence Lane sectors",
        description=(
            "Return the one immutable eighteen-lane registry, aliases, command "
            "mapping, parser/chunker contracts, SQLite names, FTS tables, and "
            "mutation policies. Performs no state mutation."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Loading lane registry", "Lane registry ready"),
        structured_output=True,
    )
    def lane_catalog() -> dict[str, Any]:
        return application.invoke("lane_catalog", application.lane_catalog)

    @mcp.tool(
        name="render_runtime_panel",
        title="Render Evidence Lane runtime panel",
        description=(
            "Render a read-only MCP Apps panel from the authoritative runtime "
            "doctor and canonical lane catalog. Use runtime_doctor or lane_catalog "
            "directly when the host does not support UI."
        ),
        annotations=_READ_ONLY,
        meta=governed_panel_tool_meta(
            "Rendering Evidence Lane runtime", "Runtime panel ready"
        ),
        structured_output=True,
    )
    def render_runtime_panel() -> dict[str, Any]:
        def snapshot() -> dict[str, Any]:
            return build_runtime_panel_snapshot(
                doctor=route_aware_doctor(),
                lane_catalog=application.lane_catalog(),
                public_site_url=exact_public_site,
            )

        return application.invoke("render_runtime_panel", snapshot)

    @mcp.tool(
        name="render_project_panel",
        title="Render governed project and HIL panel",
        description=(
            "Render a read-only MCP Apps panel for one registered project's "
            "accepted pointer, active lifecycle state, pending candidate, and "
            "canonical next/queued HIL queue with Plan connections. This is not "
            "the host Step Task List. The tool never accepts, rejects, rolls "
            "back, or promotes."
        ),
        annotations=_READ_ONLY,
        meta=governed_panel_tool_meta(
            "Rendering governed project status", "Project panel ready"
        ),
        structured_output=True,
    )
    def render_project_panel(project_id: str) -> dict[str, Any]:
        def snapshot() -> dict[str, Any]:
            return build_project_panel_snapshot(
                project_id=project_id,
                project_status=application.status(project_id),
                public_site_url=exact_public_site,
                plan_backlog=application.task_backlog(project_id),
            )

        return application.invoke(
            "render_project_panel",
            snapshot,
            authorization_project_id=project_id,
        )

    @mcp.tool(
        name="source_intake_classify",
        title="Classify generalized Source Intake",
        description=(
            "Auto-detect one or more ordered source pointers across all eighteen "
            "canonical lanes and Project Engulf, apply exact per-source overrides, "
            "always include Chat Lineage, and append a visible classification "
            "receipt. GOVERNED_CONTENT_REGISTRY additionally records deterministic "
            "read-only source identities without copying payloads, building a "
            "candidate, or moving a pointer. REFRESH_WORKING_SECTORS is the explicit "
            "transactional materialization action. Optional turn_entry is a separate "
            "immutable query over an already materialized WORKING projection and "
            "appends its real formula lineage without adding another Plan row."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Classifying Source Intake", "Source Intake classified"),
        structured_output=True,
    )
    def source_intake_classify(
        project_id: str,
        sources: list[str],
        overrides: dict[str, str] | None = None,
        session_id: str | None = None,
        git_mode: str = "AUTO",
        authority_mode: str = "CLASSIFICATION_ONLY",
        source_assertions: dict[str, dict[str, Any]] | None = None,
        turn_entry: dict[str, Any] | None = None,
        working_authority_action: str = "CLASSIFY_ONLY",
    ) -> dict[str, Any]:
        return application.invoke(
            "source_intake_classify",
            application.source_intake,
            project_id,
            sources,
            overrides=overrides,
            session_id=session_id,
            git_mode=git_mode,
            authority_mode=authority_mode,
            source_assertions=source_assertions,
            turn_entry=turn_entry,
            working_authority_action=working_authority_action,
            lifecycle=True,
        )

    @mcp.tool(
        name="adaptive_delta_exit",
        title="Seal one adaptive Delta exit",
        description=(
            "Verify the exact active Delta, targeted PASS receipts, grouped or exact "
            "local-install disposition, and all distinct registered hook states; then "
            "refresh Learning, Canon, Memory, and Universe through the internal SDK, "
            "close the task formula, and seal one replay-safe receipt. The route never "
            "advances the Plan row, creates a candidate, invokes HIL, moves the accepted "
            "pointer, or mutates Git."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Sealing adaptive Delta exit", "Adaptive Delta exit sealed"),
        structured_output=True,
    )
    def adaptive_delta_exit(
        project_id: str,
        session_id: str,
        task_id: str,
        source_event_id: str,
        prior_formula_sha256: str,
        formula: dict[str, Any],
        validator_results: list[dict[str, Any]],
        install_disposition: dict[str, Any],
        actor: str = "ADAPTIVE_DELTA_EXIT",
        hook_progression: list[dict[str, Any]] | None = None,
        fixed_window_task_ids: list[str] | None = None,
        observed_host_plan: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "adaptive_delta_exit",
            application.adaptive_delta_exit,
            project_id,
            session_id,
            task_id=task_id,
            source_event_id=source_event_id,
            prior_formula_sha256=prior_formula_sha256,
            formula=formula,
            validator_results=validator_results,
            install_disposition=install_disposition,
            actor=actor,
            hook_progression=hook_progression,
            fixed_window_task_ids=fixed_window_task_ids,
            observed_host_plan=observed_host_plan,
            event_id=event_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_sqlite_inspect",
        title="Inspect registered SQLite brain authorities",
        description=(
            "Inspect every direct or ZIP-embedded SQLite authority in one governed "
            "Source Intake batch. Exact duplicate bytes are inspected once, ZIPs "
            "with proven extracted counterparts are skipped, independent embedded "
            "databases use bounded in-memory deserialization, and all SQLite reads "
            "remain query-only without executing imported SQL or moving a pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Inspecting SQLite authorities", "SQLite authorities inspected"),
        structured_output=True,
    )
    def source_sqlite_inspect(
        project_id: str,
        batch_id: str,
        session_id: str | None = None,
        max_embedded_member_bytes: int = 768 * 1024 * 1024,
        exact_count_max_database_bytes: int = 32 * 1024 * 1024,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_sqlite_inspect",
            application.source_sqlite_inspect,
            project_id,
            batch_id,
            session_id=session_id,
            max_embedded_member_bytes=max_embedded_member_bytes,
            exact_count_max_database_bytes=exact_count_max_database_bytes,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_custom_schema_compile",
        title="Compile and map a Custom Source Schema",
        description=(
            "Validate one declarative, schema-first custom source contract and "
            "map it deterministically to a governed Source Intake batch. The "
            "compiler allows only pinned dependencies, ordered selectors, typed "
            "fields, and non-executable transforms; it reads sealed registry "
            "metadata only and never executes imported code or SQL, copies source "
            "payloads, builds a candidate, or moves a pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Compiling Custom Source Schema", "Custom Source Schema mapped"),
        structured_output=True,
    )
    def source_custom_schema_compile(
        project_id: str,
        batch_id: str,
        schema_definition: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_custom_schema_compile",
            application.source_custom_schema_compile,
            project_id,
            batch_id,
            schema_definition,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_intake_schema_configure",
        title="Add or modify a Source Intake schema pill",
        description=(
            "ADD one new schema-derived Source Intake pill or MODIFY it by "
            "appending the exact next version pinned to the prior SHA-256. The "
            "declarative compiler maps sealed registry metadata only; it never "
            "rewrites an earlier schema, mutates the canonical eighteen-lane "
            "registry, executes imported code or SQL, builds a candidate, moves "
            "a pointer, or bypasses later HIL authority."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Configuring Source Intake schema", "Source Intake schema configured"
        ),
        structured_output=True,
    )
    def source_intake_schema_configure(
        project_id: str,
        batch_id: str,
        operation: Literal["ADD", "MODIFY"],
        pill_name: str,
        schema_definition: dict[str, Any],
        expected_previous_schema_sha256: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_intake_schema_configure",
            application.source_intake_schema_configure,
            project_id,
            batch_id,
            operation=operation,
            pill_name=pill_name,
            schema_definition=schema_definition,
            expected_previous_schema_sha256=expected_previous_schema_sha256,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_identity_register",
        title="Register distinct source-generation identities",
        description=(
            "Append a complete multi-axis identity matrix for one governed Source "
            "Intake batch. Artifact bytes, producer application/release, model, "
            "architecture generation, internal schema labels, observed filename "
            "markers, and claim authority remain separate. Alias/SAME_AS collapse "
            "is forbidden; unbound versions remain explicitly unclaimed."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Registering source identities", "Source identities registered"),
        structured_output=True,
    )
    def source_identity_register(
        project_id: str,
        batch_id: str,
        entities: list[dict[str, Any]],
        profiles: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_identity_register",
            application.source_identity_register,
            project_id,
            batch_id,
            entities=entities,
            profiles=profiles,
            relations=relations,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_graph_build",
        title="Build a bounded provenance-first source graph",
        description=(
            "Build a deterministic polyglot graph over exact registered Source "
            "Intake bytes. Stable semantic node IDs exclude mutable line numbers, "
            "every edge records EXTRACTED, INFERRED, or AMBIGUOUS provenance, "
            "coverage gaps remain visible, and ZIPs are skipped only with an exact "
            "Delta 067A extracted-counterpart receipt. Finite file, byte, node, and "
            "edge bounds apply; an optional repository-relative path-prefix selection "
            "is sealed and visibly reports omitted registered members. No source, "
            "candidate, or pointer is mutated."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Building bounded source graph", "Source graph built"),
        structured_output=True,
    )
    def source_graph_build(
        project_id: str,
        batch_id: str,
        occurrence_ordinals: list[int] | None = None,
        member_path_prefixes: list[str] | None = None,
        max_files: int = 25_000,
        max_total_bytes: int = 1024 * 1024 * 1024,
        max_file_bytes: int = 8 * 1024 * 1024,
        max_nodes: int = 500_000,
        max_edges: int = 1_000_000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_graph_build",
            application.source_graph_build,
            project_id,
            batch_id,
            occurrence_ordinals=occurrence_ordinals,
            member_path_prefixes=member_path_prefixes,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            max_file_bytes=max_file_bytes,
            max_nodes=max_nodes,
            max_edges=max_edges,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_graph_diff",
        title="Diff exact source-graph snapshots",
        description=(
            "Compare two exact registered graph roots by stable node and edge ID, "
            "separating added, removed, and content-changed entities. Samples are "
            "bounded and the full count projection is sealed without reading or "
            "mutating source bytes, candidates, or pointers."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Diffing source graphs", "Source graphs diffed"),
        structured_output=True,
    )
    def source_graph_diff(
        project_id: str,
        from_graph_id: str,
        to_graph_id: str,
        sample_limit: int = 100,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_graph_diff",
            application.source_graph_diff,
            project_id,
            from_graph_id,
            to_graph_id,
            sample_limit=sample_limit,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_graph_impact",
        title="Traverse a bounded affected source subgraph",
        description=(
            "From exact stable node IDs, traverse upstream dependents, downstream "
            "dependencies, or both across an explicit relation allowlist. Depth "
            "and node caps are mandatory, edge evidence retains source location "
            "and confidence, and the traversal never changes source or lifecycle "
            "state."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Traversing source impact", "Source impact traversed"),
        structured_output=True,
    )
    def source_graph_impact(
        project_id: str,
        graph_id: str,
        seed_node_ids: list[str],
        relations: list[str] | None = None,
        direction: str = "UPSTREAM",
        max_depth: int = 3,
        max_nodes: int = 1000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_graph_impact",
            application.source_graph_impact,
            project_id,
            graph_id,
            seed_node_ids,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_git_history_build",
        title="Seal full bounded Git history evidence",
        description=(
            "For one exact registered directory that still has local .git metadata, "
            "seal all reachable refs, commits, parent edges, objects, per-commit "
            "trees, per-parent file changes, renames, hunk coordinates, and changed-"
            "line hashes. Extracted folders never qualify as history; lazy fetch, "
            "source writes, Git writes, candidate creation, and pointer movement are "
            "forbidden. Finite bounds fail closed without a partial snapshot."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Sealing bounded Git history", "Git history sealed"),
        structured_output=True,
    )
    def source_git_history_build(
        project_id: str,
        batch_id: str,
        occurrence_ordinal: int,
        max_refs: int = 20_000,
        max_commits: int = 100_000,
        max_objects: int = 2_000_000,
        max_tree_entries: int = 5_000_000,
        max_file_changes: int = 2_000_000,
        max_hunks: int = 2_000_000,
        max_changed_lines: int = 5_000_000,
        max_patch_bytes: int = 2 * 1024 * 1024 * 1024,
        max_single_object_bytes: int = 1024 * 1024 * 1024,
        max_total_object_bytes: int = 8 * 1024 * 1024 * 1024,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_git_history_build",
            application.source_git_history_build,
            project_id,
            batch_id,
            occurrence_ordinal,
            max_refs=max_refs,
            max_commits=max_commits,
            max_objects=max_objects,
            max_tree_entries=max_tree_entries,
            max_file_changes=max_file_changes,
            max_hunks=max_hunks,
            max_changed_lines=max_changed_lines,
            max_patch_bytes=max_patch_bytes,
            max_single_object_bytes=max_single_object_bytes,
            max_total_object_bytes=max_total_object_bytes,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_git_commit_impact",
        title="Map an exact Git parent diff to graph impact",
        description=(
            "Bind one indexed commit and exact parent ordinal to FILE nodes from the "
            "same registered source occurrence, then traverse a bounded semantic "
            "impact graph. Removed, excluded, ambiguous, or absent paths stay "
            "explicitly unmapped; no historical semantic state is fabricated and no "
            "source, Git repository, candidate, or pointer is changed."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Mapping Git change impact", "Git change impact mapped"),
        structured_output=True,
    )
    def source_git_commit_impact(
        project_id: str,
        snapshot_id: str,
        graph_id: str,
        commit_sha: str,
        parent_ordinal: int = 0,
        relations: list[str] | None = None,
        direction: str = "UPSTREAM",
        max_depth: int = 3,
        max_nodes: int = 1000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_git_commit_impact",
            application.source_git_commit_impact,
            project_id,
            snapshot_id,
            graph_id,
            commit_sha,
            parent_ordinal=parent_ordinal,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="hil_intent_classify",
        title="Classify tolerant HIL intent",
        description=(
            "Classify visible natural-language continuation or approval intent, "
            "append it to Chat Lineage, and return the safe exact next action. "
            "This tool never decides HIL, promotes a candidate, or moves a pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Classifying HIL intent", "HIL intent classified"),
        structured_output=True,
    )
    def hil_intent_classify(
        project_id: str,
        session_id: str,
        utterance: str,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "hil_intent_classify",
            application.classify_hil_intent,
            project_id,
            session_id,
            utterance,
            event_id=event_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="connector_plugin_catalog",
        title="Inspect governed connector and toolchain plugins",
        description=(
            "Read the append-only connector brain, its active maximum of eight, "
            "dropped history, SQLite integrity, capabilities, lanes, and secret-free "
            "configuration-variable names."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading connector brain", "Connector brain ready"),
        structured_output=True,
    )
    def connector_plugin_catalog(project_id: str) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_catalog",
            application.connector_plugin_catalog,
            project_id,
        )

    @mcp.tool(
        name="connector_plugin_settings",
        title="Open the eight-slot connector settings surface",
        description=(
            "Return eight Codex connector slots, including governed role/schema and "
            "optional backend-runtime metadata. Credential values remain host-managed."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading connector settings", "Connector settings ready"),
        structured_output=True,
    )
    def connector_plugin_settings(
        project_id: str,
        host_profile: Literal["CODEX"],
    ) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_settings",
            application.connector_plugin_settings,
            project_id,
            host_profile=host_profile,
        )

    @mcp.tool(
        name="connector_plugin_register",
        title="Register one bounded persistent connector or toolchain",
        description=(
            "Register one connector or AI toolchain plugin with environment-variable "
            "names only, explicit capabilities, and canonical lanes. At most eight "
            "additional plugins may remain active; no secret value is persisted."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Registering governed plugin", "Governed plugin registered"),
        structured_output=True,
    )
    def connector_plugin_register(
        project_id: str,
        plugin_id: str,
        name: str,
        plugin_kind: Literal["connector", "toolchain"],
        description: str,
        config_env_keys: list[str],
        capabilities: list[str],
        allowed_lanes: list[str],
        registered_by: str,
        purpose: str | None = None,
        allowed_actions: list[str] | None = None,
        write_scope: list[str] | None = None,
        expires_at: str = "NO_EXPIRY",
        role: str | None = None,
        role_schema: dict[str, str] | None = None,
        host_profiles: list[Literal["CODEX"]] | None = None,
        backend_runtime: Literal[
            "python", "java", "kotlin", "go", "rust", "cpp", "external_mcp"
        ] = "python",
    ) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_register",
            application.connector_plugin_register,
            project_id,
            plugin_id=plugin_id,
            name=name,
            plugin_kind=plugin_kind,
            description=description,
            config_env_keys=config_env_keys,
            capabilities=capabilities,
            allowed_lanes=allowed_lanes,
            registered_by=registered_by,
            purpose=purpose,
            allowed_actions=allowed_actions,
            write_scope=write_scope,
            expires_at=expires_at,
            role=role,
            role_schema=role_schema,
            host_profiles=host_profiles,
            backend_runtime=backend_runtime,
            lifecycle=True,
        )

    @mcp.tool(
        name="connector_plugin_drop",
        title="Drop one governed persistent plugin",
        description=(
            "Drop exactly one active connector/toolchain only with DROP:<plugin-id>; "
            "preserve its registration and event history rather than deleting it."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Dropping governed plugin", "Governed plugin dropped"),
        structured_output=True,
    )
    def connector_plugin_drop(
        project_id: str,
        plugin_id: str,
        confirmation: str,
        dropped_by: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_drop",
            application.connector_plugin_drop,
            project_id,
            plugin_id=plugin_id,
            confirmation=confirmation,
            dropped_by=dropped_by,
            lifecycle=True,
        )

    @mcp.tool(
        name="connector_plugin_route",
        title="Route one capability through governed plugin policy",
        description=(
            "Evaluate active grant, capability/action, canonical lane, and host "
            "guards in a fixed order. Select the sole eligible plugin, or require "
            "one exact preferred plugin ID when multiple routes qualify; ambiguity "
            "and failed guards remain fail-closed."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Routing connector capability", "Connector route recorded"),
        structured_output=True,
    )
    def connector_plugin_route(
        project_id: str,
        capability: str,
        canonical_lane_id: str | None = None,
        host_profile: Literal["CODEX"] = "CODEX",
        preferred_plugin_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_route",
            application.connector_plugin_route,
            project_id,
            capability=capability,
            canonical_lane_id=canonical_lane_id,
            host_profile=host_profile,
            preferred_plugin_id=preferred_plugin_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="storage_connector_inspect",
        title="Inspect the primary storage connector route",
        description=(
            "Read the project selection, effective host route, transactional durable "
            "capability, and Google Drive fallback boundary. This never stores a secret "
            "or changes the selected authority."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Inspecting storage route", "Storage route ready"),
        structured_output=True,
    )
    def storage_connector_inspect(
        project_id: str,
        host_kind: str | None = None,
        ephemeral: bool = False,
        server_has_durable_filesystem: bool | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "storage_connector_inspect",
            application.storage_connector_inspect,
            project_id,
            host=host_kind,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
        )

    @mcp.tool(
        name="storage_connector_select",
        title="Select the project primary storage authority",
        description=(
            "Append an exact project storage selection for AUTO, durable local "
            "SQLite, or one configured transactional durable connector. The exact "
            "SELECT_STORAGE token is required; Drive remains an optional mirror."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Selecting storage authority", "Storage authority selected"),
        structured_output=True,
    )
    def storage_connector_select(
        project_id: str,
        mode: Literal["AUTO", "LOCAL_SQLITE", "CONFIGURED_DURABLE_CONNECTOR"],
        selected_by: str,
        reason: str,
        confirmation: str,
        connector_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "storage_connector_select",
            application.storage_connector_select,
            project_id,
            mode=mode,
            selected_by=selected_by,
            reason=reason,
            confirmation=confirmation,
            connector_id=connector_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="mode_classify",
        title="Classify an ENV15 mode intersection and lanes",
        description=(
            "At any lifecycle position, classify one or more locked ENV15 modes "
            "such as Analysis + Planning + Code, map them to canonical Evidence "
            "Lanes, always include Chat Lineage, append only the privacy-minimized "
            "classification receipt when a session is active, and return to the "
            "prior lifecycle position without creating a task, candidate, HIL, or "
            "pointer movement. The result includes visible ENV/UOP formulas, "
            "PCM/MBA operator receipts, controlled CI/CD requirements, and "
            "lane-specific meanings for the universal six HIL tokens; render those "
            "fields visibly and never substitute generic Code-mode HIL semantics."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Classifying operating modes and lanes",
            "Mode intersection classified",
        ),
        structured_output=True,
    )
    def mode_classify(
        project_id: str,
        request: str,
        explicit_modes: list[str] | None = None,
        session_id: str | None = None,
        custom_modes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "mode_classify",
            application.classify_mode,
            project_id,
            request,
            explicit_modes=explicit_modes,
            session_id=session_id,
            custom_modes=custom_modes,
            lifecycle=True,
        )

    @mcp.tool(
        name="lane_status",
        title="Inspect one lane authority",
        description=(
            "Inspect one accepted or explicitly named candidate lane: SQLite/MMD/DOT "
            "hashes, parser/tool capability states, pointer evidence, Refresh "
            "classification, and live-source freshness."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Checking lane authority", "Lane authority ready"),
        structured_output=True,
    )
    def lane_status(
        project_id: str,
        lane: str,
        pv_ref: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "lane_status",
            application.lane_status,
            project_id,
            lane,
            pv_ref=pv_ref,
        )

    @mcp.tool(
        name="lane_search",
        title="Search one lane with BM25 and TF-IDF",
        description=(
            "Search an accepted or explicitly named candidate lane using SQLite "
            "FTS5/BM25, explicit materialized TF-IDF, or deterministic "
            "reciprocal-rank hybrid retrieval. Results include source/chunk hashes, "
            "parser state, PV authority, and live freshness."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Searching lane evidence", "Lane search complete"),
        structured_output=True,
    )
    def lane_search(
        project_id: str,
        lane: str,
        query: str,
        pv_ref: str | None = None,
        limit: int = 20,
        retrieval: str = "hybrid",
    ) -> dict[str, Any]:
        return application.invoke(
            "lane_search",
            application.lane_search,
            project_id,
            lane,
            query,
            pv_ref=pv_ref,
            limit=limit,
            retrieval=retrieval,
        )

    @mcp.tool(
        name="lane_fetch",
        title="Fetch one exact lane source",
        description=(
            "Fetch one exact source registered in a lane with bounded text, hash, "
            "parser state, structured facts, PV authority, and freshness. Binary "
            "source bytes remain inside the immutable SQLite authority."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Fetching lane source", "Lane source ready"),
        structured_output=True,
    )
    def lane_fetch(
        project_id: str,
        lane: str,
        path: str,
        pv_ref: str | None = None,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        return application.invoke(
            "lane_fetch",
            application.lane_fetch,
            project_id,
            lane,
            path,
            pv_ref=pv_ref,
            max_bytes=max_bytes,
        )

    @mcp.tool(
        name="lane_configure_routes",
        title="Grant exact source-to-lane routes",
        description=(
            "Arm one named, one-candidate-only mapping from exact current source "
            "paths to canonical lanes. The next PV build consumes the grant, records "
            "it in lineage and routes.json, then relocks it. Accepted route authority "
            "is inherited by later Refreshes. This never edits source or promotes a PV."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Arming lane route grant", "Lane route grant armed"),
        structured_output=True,
    )
    def lane_configure_routes(
        project_id: str,
        session_id: str,
        overrides: dict[str, str],
        granted_by: str,
        grant_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "lane_configure_routes",
            application.configure_lane_routes,
            project_id,
            session_id,
            overrides=overrides,
            granted_by=granted_by,
            grant_id=grant_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_enroll_project",
        title="Enroll an external Git project",
        description=(
            "Adopt one exact local Git path or clone one credential-free HTTPS Git "
            "URL into the user-owned Evidence Lane store, verify owner/name/branch, "
            "and register it without overwriting lineage. Performs no remote Git "
            "write and builds no PV."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Enrolling governed project", "Project enrollment complete"),
        structured_output=True,
    )
    def pv_enroll_project(
        project_id: str,
        display_name: str,
        source: str,
        expected_owner: str,
        expected_name: str,
        branch: str,
        sensitivity: str = "PRIVATE",
        capture_route: str = "GOVERNED_PROJECT_FULL",
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_enroll_project",
            application.enroll_project,
            project_id=project_id,
            display_name=display_name,
            source=source,
            expected_owner=expected_owner,
            expected_name=expected_name,
            branch=branch,
            sensitivity=sensitivity,
            capture_route=capture_route,
            lifecycle=True,
        )

    @mcp.tool(
        name="git_sync_selected",
        title="Fast-forward one selected Git branch",
        description=(
            "Fetch one explicit local Git source or credential-free HTTPS repository "
            "and one exact branch, verify identity and optional commit, preview "
            "changed paths against any active task, then apply only a clean "
            "fast-forward. In an active governed session, an explicit replacement "
            "flag may narrow authority to the exact already-checked-out branch and "
            "writes a receipt. It never pushes, merges divergent history, switches "
            "branches, or broadens branch authority."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Syncing selected Git branch", "Selected branch synchronized"),
        structured_output=True,
    )
    def git_sync_selected(
        project_id: str,
        source: str,
        branch: str,
        session_id: str | None = None,
        expected_commit: str | None = None,
        replace_registered_branch: bool = False,
    ) -> dict[str, Any]:
        return application.invoke(
            "git_sync_selected",
            application.sync_git_source,
            project_id=project_id,
            source=source,
            branch=branch,
            session_id=session_id,
            expected_commit=expected_commit,
            replace_registered_branch=replace_registered_branch,
            lifecycle=True,
        )

    @mcp.tool(
        name="project_register",
        title="Register one Git project",
        description=(
            "Register one explicitly authorized local Git repository and branch set "
            "against one user-project authority root. Idempotent only when all "
            "authority fields match. An existing legacy combined route may be "
            "relocated only with the exact migration confirmation and pointer "
            "preconditions; the route never creates a candidate, infers HIL, or "
            "moves the accepted pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Registering governed project", "Project registration complete"),
        structured_output=True,
    )
    def project_register(
        project_id: str,
        display_name: str,
        repository_path: str,
        expected_owner: str,
        expected_name: str,
        allowed_branches: list[str],
        sensitivity: str = "PRIVATE",
        capture_route: str = "GOVERNED_PROJECT_FULL",
        project_authority_root: str | None = None,
        project_authority_migration_confirmation: str | None = None,
        expected_accepted_pv: str | None = None,
        expected_pointer_generation: int | None = None,
        selected_by: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "project_register",
            application.register_project,
            project_id=project_id,
            display_name=display_name,
            repository_path=repository_path,
            expected_owner=expected_owner,
            expected_name=expected_name,
            allowed_branches=allowed_branches,
            sensitivity=sensitivity,
            capture_route=capture_route,
            project_authority_root=project_authority_root,
            project_authority_migration_confirmation=(
                project_authority_migration_confirmation
            ),
            expected_accepted_pv=expected_accepted_pv,
            expected_pointer_generation=expected_pointer_generation,
            selected_by=selected_by,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_plan_tasks",
        title="Queue a linear multi-task plan",
        description=(
            "Append one bounded task plan to the project backlog. Every task keeps "
            "its own exact class, outcome, paths, tools, acceptance checks, and stop "
            "condition. Planning normally activates nothing: the one-agent/one-active-task "
            "law still requires task_classify for one queued task at a time. An optional "
            "normalization_transition is the sole exception: it requires exact Plan, "
            "session, and pointer hashes plus an approval receipt, then journal-appends "
            "the approved successor rows and atomically rebinds the same session without "
            "adding another tool, candidate, HIL, pointer move, or Git action. A "
            "correction_of_transition_id contract may journal-restore the exact prior "
            "active row after a mistaken committed normalization; it appends two history "
            "events but no row, preserves the original journal, and may seal a fixed "
            "display offset for dynamically renumbered current-execution rows."
            " An optional atomic_insertion uses an empty top-level task list and "
            "one bounded set of exact insertion targets. It verifies the sealed "
            "backlog-byte and canonical/executable Plan hashes plus physical-final HIL, preserves "
            "structured task metadata, and journals one idempotent multi-target "
            "commit without changing Goal, candidate, HIL, pointer, or Git state."
            " An optional active_contract_rebind also uses an empty top-level task "
            "list. It verifies exact Plan, session, pointer, invoking host task, runtime-task, "
            "and visible user-authority receipts; append-amends only the sole ACTIVE "
            "row; and atomically reseals the same governed session/runtime task. It "
            "does not replace the row, create or complete a Goal, create a candidate, "
            "invoke HIL, move the pointer, run Git, install, launch a helper, or open "
            "a tunnel. An optional existing_task_promotion uses an empty top-level "
            "task list and atomically moves one already-recorded queued Delta before "
            "the sole ACTIVE row, preserving stable task identity and count while "
            "rebinding the same session and persistent 1+9 host projection."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Queuing linear task plan", "Linear task plan queued"),
        structured_output=True,
    )
    def pv_plan_tasks(
        project_id: str,
        tasks: list[dict[str, Any]],
        planned_by: str,
        plan_id: str | None = None,
        host_kind: str | None = None,
        host_mode: str | None = None,
        normalization_transition: dict[str, Any] | None = None,
        atomic_insertion: dict[str, Any] | None = None,
        active_contract_rebind: dict[str, Any] | None = None,
        existing_task_promotion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_plan_tasks",
            application.plan_tasks,
            project_id,
            tasks=tasks,
            planned_by=planned_by,
            plan_id=plan_id,
            host_kind=host_kind,
            host_mode=host_mode,
            normalization_transition=normalization_transition,
            atomic_insertion=atomic_insertion,
            active_contract_rebind=active_contract_rebind,
            existing_task_promotion=existing_task_promotion,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_plan_steer_delta",
        title="Append one canonical steer Delta",
        description=(
            "Record a visible user steer before the next HIL by default. The host "
            "agent must classify it as either linked to one existing Plan Lane task "
            "or unrelated and therefore one complete new task row. Linked steers "
            "never replace the task or change the count; unrelated steers insert "
            "one new numbered row before the next HIL when that gate is present "
            "and increase the persistent task-panel count. A task may declare "
            "panel_role=PHYSICALLY_FINAL_HIL so that row remains physically final."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Recording steer Delta", "Steer Delta recorded"),
        structured_output=True,
    )
    def pv_plan_steer_delta(
        project_id: str,
        delta_text: str,
        actor: str,
        delta_id: str,
        linked_task_id: str | None = None,
        new_task_contract: dict[str, Any] | None = None,
        boundary: str = "BEFORE_NEXT_HIL",
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_plan_steer_delta",
            application.record_steer_delta,
            project_id,
            delta_text=delta_text,
            actor=actor,
            delta_id=delta_id,
            linked_task_id=linked_task_id,
            new_task_contract=new_task_contract,
            boundary=boundary,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_task_backlog",
        title="Read the linear task backlog",
        description=(
            "Read the aligned current Plan window without loading the full ledger "
            "or any PV into model context. Supply one exact task_id for the full "
            "bounded row contract, or query for a bounded live-Plan FTS5 slice. "
            "Performs no classification or state mutation."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading task backlog", "Task backlog ready"),
        structured_output=True,
    )
    def pv_task_backlog(
        project_id: str,
        task_id: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_task_backlog",
            application.task_backlog_window,
            project_id,
            task_id=task_id,
            query=query,
            limit=limit,
        )

    @mcp.tool(
        name="pv_task_transition",
        title="Transition or correct one Delta",
        description=(
            "Append one explicit DROP or SUPERSEDE transition to the immutable "
            "Delta lifecycle ledger. SUPERSEDE requires a different queued "
            "replacement task. CORRECT_DROP is a hash-bound recovery for a "
            "DROP that was persisted before dependency validation failed; it "
            "preserves the failed event and restores only the exact QUEUED state."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Recording Delta transition", "Delta transition recorded"),
        structured_output=True,
    )
    def pv_task_transition(
        project_id: str,
        task_id: str,
        transition: Literal["DROP", "SUPERSEDE", "CORRECT_DROP"],
        decided_by: str,
        reason: str,
        replacement_task_id: str | None = None,
        event_id: str | None = None,
        correction_of_event_id: str | None = None,
        expected_backlog_sha256: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_task_transition",
            application.transition_task,
            project_id,
            task_id=task_id,
            transition_name=transition,
            decided_by=decided_by,
            reason=reason,
            replacement_task_id=replacement_task_id,
            event_id=event_id,
            correction_of_event_id=correction_of_event_id,
            expected_backlog_sha256=expected_backlog_sha256,
            lifecycle=True,
        )

    @mcp.tool(
        name="session_boot",
        title="Boot governed Evidence Lane session",
        description=(
            "Verify and idempotently flash the locked ENV15/UOP15 session authority, "
            "then boot one governed context for one user, workspace, project, host, "
            "agent, and source state. Boot attaches Flash context and visible "
            "prompt/response capture until /evi-exit-boot. Exit detaches the runtime "
            "but preserves the installed plugin, verified Flash receipt, immutable "
            "store, and pointer. Neither runtime context nor ENV/UOP bytes enter a "
            "PV. Remote or ephemeral hosts fail closed unless a transactional "
            "durable connector is configured."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Booting governed session", "Governed session ready"),
        structured_output=True,
    )
    def session_boot(
        project_id: str,
        user_id: str,
        workspace_id: str,
        host_kind: str,
        agent_id: str,
        ephemeral: bool = False,
        sandbox_id: str | None = None,
        runtime_context: dict[str, Any] | None = None,
        host_session_id: str | None = None,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "session_boot",
            application.boot_session,
            project_id=project_id,
            user_id=user_id,
            workspace_id=workspace_id,
            host=host_kind,
            agent_id=agent_id,
            sandbox_id=sandbox_id,
            ephemeral=ephemeral,
            runtime_context=runtime_context,
            host_session_id=host_session_id,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=server_has_durable_filesystem,
            lifecycle=True,
        )

    @mcp.tool(
        name="session_resume",
        title="Resume persistent Evidence Lane session",
        description=(
            "Bind a fresh Codex task to the one already-active "
            "governed session, preserving its accepted entry, pending candidate, "
            "exact HIL follow-up, pointer generation, and prompt-index boundary. "
            "Performs no PV build, promotion, rollback, or source mutation."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Resuming governed session", "Persistent session resumed"),
        structured_output=True,
    )
    def session_resume(
        project_id: str,
        host_kind: str,
        host_session_id: str,
        ephemeral: bool = False,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "session_resume",
            application.resume_session,
            project_id=project_id,
            host=host_kind,
            host_session_id=host_session_id,
            ephemeral=ephemeral,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_build_initial",
        title="Build initial PV1 candidate",
        description=(
            "Run the deterministic whole-source Git/code engine against a clean, "
            "authorized repository to create—but not approve—PV1 candidate. "
            "Mermaid rendering failure remains a warning and never invalidates a "
            "correct SQLite PV."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Building initial PV1 candidate", "PV1 candidate sealed"),
        structured_output=True,
    )
    def pv_build_initial(project_id: str, session_id: str) -> dict[str, Any]:
        return application.invoke(
            "pv_build_initial",
            application.build_initial,
            project_id,
            session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="task_classify",
        title="Classify one bounded code task",
        description=(
            "Create the one-agent/one-task contract: exact class, outcome, paths, "
            "tools, acceptance checks, write boundary, stop condition, and HIL gate. "
            "This tool does not execute or broaden the task. When advancing a "
            "strict per-Delta package-parity row, active_delta_verification is mandatory: "
            "it binds the exact Plan-runtime contract hash, dependency generation, "
            "pre/post worktree chain, live source/test hashes, bounded commands and "
            "PASS outputs, and negative cases. A generic PASS cannot advance such "
            "a row, and the route creates no candidate, HIL, pointer, Git, or install "
            "effect."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Classifying bounded task", "Task contract ready"),
        structured_output=True,
    )
    def task_classify(
        project_id: str,
        session_id: str,
        task_class: str,
        requested_outcome: str,
        permitted_paths: list[str],
        permitted_tools: list[str],
        acceptance_checks: list[str],
        stop_condition: str,
        backlog_task_id: str | None = None,
        active_delta_verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        authorization_block, entry_binding = application.preflight(
            "task_classify",
            lifecycle=True,
            project_id=project_id,
            session_id=session_id,
        )
        if authorization_block is not None:
            return cast(dict[str, Any], authorization_block)
        native_route_receipt = getattr(mcp, "_evidence_lane_native_route_receipt", None)
        active_session = application.sessions.load(project_id, session_id)
        fallback_prewarm_proof: dict[str, Any] | None = None
        task_checkpoint_proof: dict[str, Any] | None = None
        active_backlog_task_id = str(
            active_session.metadata.get("active_backlog_task_id") or ""
        ).strip()
        active_plan_task = next(
            (
                row
                for row in application.task_backlog(project_id).get("tasks", [])
                if row.get("task_id") == active_backlog_task_id
            ),
            None,
        )
        strong_delta_verification_required = isinstance(
            active_plan_task, dict
        ) and requires_per_delta_local_verification(active_plan_task)
        if (
            active_session.metadata.get("active_backlog_task_id")
            == "EL-CODEX-PV11-FALLBACK-SLOT-INSTALL-PREWARM-DELTA-149"
        ):
            try:
                fallback_prewarm_proof = verify_codex_fallback_prewarmer(
                    application.store.root,
                    project_id=project_id,
                    session_id=session_id,
                )
            except TurnControlError as exc:
                fallback_prewarm_proof = {
                    "schema": "evidence-lane.codex-fallback-prewarm-proof.v1",
                    "status": "FAIL",
                    "error": exc.as_dict(),
                }
        if (
            active_backlog_task_id
            == "EL-CODEX-EXACT_TASK_PROJECT_SESSION_BINDING-PROPOSAL-03"
        ):
            try:
                task_checkpoint_proof = seal_exact_task_project_session_binding(
                    application.store.root,
                    project_id=project_id,
                    evidence_session_id=session_id,
                    expected_active_task_id=(
                        "EL-CODEX-EXACT_TASK_PROJECT_SESSION_BINDING-PROPOSAL-03"
                    ),
                )
            except TurnControlError as exc:
                task_checkpoint_proof = {
                    "schema": (
                        "evidence-lane.codex-exact-task-project-session-binding.v1"
                    ),
                    "status": "FAIL",
                    "error": exc.as_dict(),
                }
        elif (
            bool(backlog_task_id)
            and bool(active_backlog_task_id)
            and backlog_task_id != active_backlog_task_id
            and active_backlog_task_id
            != "EL-CODEX-PV11-FALLBACK-SLOT-INSTALL-PREWARM-DELTA-149"
        ):
            if strong_delta_verification_required:
                try:
                    task_checkpoint_proof = (
                        seal_per_delta_local_verification_checkpoint(
                            application.store.root,
                            project_id=project_id,
                            evidence_session_id=session_id,
                            expected_active_task_id=active_backlog_task_id,
                            verification=active_delta_verification or {},
                        )
                    )
                except TurnControlError as exc:
                    task_checkpoint_proof = {
                        "schema": (
                            "evidence-lane.per-delta-local-verification-checkpoint.v1"
                        ),
                        "status": "FAIL",
                        "verification_kind": "PER_DELTA_LOCAL_VERIFICATION",
                        "error": exc.as_dict(),
                    }
            else:
                try:
                    task_checkpoint_proof = seal_active_task_acceptance_checkpoint(
                        application.store.root,
                        project_id=project_id,
                        evidence_session_id=session_id,
                        expected_active_task_id=active_backlog_task_id,
                    )
                except TurnControlError as exc:
                    task_checkpoint_proof = {
                        "schema": (
                            "evidence-lane.active-task-acceptance-checkpoint.v1"
                        ),
                        "status": "FAIL",
                        "verification_kind": "ACTIVE_TASK_ACCEPTANCE",
                        "error": exc.as_dict(),
                    }
        else:
            prior_checkpoint = active_session.metadata.get(
                "last_task_checkpoint_advance"
            )
            if (
                isinstance(prior_checkpoint, dict)
                and prior_checkpoint.get("replacement_backlog_task_id")
                == backlog_task_id
                and isinstance(prior_checkpoint.get("verification_proof"), dict)
            ):
                task_checkpoint_proof = dict(prior_checkpoint["verification_proof"])
        project_panel_snapshot = build_project_panel_snapshot(
            project_id=project_id,
            project_status=application.status(project_id),
            public_site_url=exact_public_site,
            plan_backlog=application.task_backlog(project_id),
        )
        return application.invoke_preflighted(
            "task_classify",
            application.sessions.classify,
            project_id,
            session_id,
            entry_binding=cast(dict[str, Any], entry_binding),
            task_class=task_class,
            requested_outcome=requested_outcome,
            permitted_paths=permitted_paths,
            permitted_tools=permitted_tools,
            acceptance_checks=acceptance_checks,
            stop_condition=stop_condition,
            backlog_task_id=backlog_task_id,
            _native_route_receipt=native_route_receipt,
            _installed_surface_inventory=package_surface_inventory(),
            _project_panel_snapshot=project_panel_snapshot,
            _fallback_prewarm_proof=fallback_prewarm_proof,
            _task_checkpoint_proof=task_checkpoint_proof,
            lifecycle=True,
        )

    @mcp.tool(
        name="task_record_activity",
        title="Append visible Chat Lineage activity",
        description=(
            "Append one visible, operational, reproducible prompt/tool/command/file/"
            "test/build/diff/output/warning/error/usage event to redacted, idempotent "
            "ChatLineage. Private model reasoning and secrets are excluded."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Appending Chat Lineage activity", "Chat Lineage activity appended"),
        structured_output=True,
    )
    def task_record_activity(
        project_id: str,
        session_id: str,
        activity_type: str,
        visible_payload: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "task_record_activity",
            application.sessions.record_activity,
            project_id,
            session_id,
            activity_type=activity_type,
            visible_payload=visible_payload,
            event_id=event_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="task_confirm_source_update",
        title="Confirm final host source state",
        description=(
            "Confirm the exact Codex source boundary before Refresh with "
            "HOST_SANDBOX_FINAL_STATE_CONFIRMED. This tool does not pull or mutate "
            "source itself."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Confirming final source state", "Final source state confirmed"),
        structured_output=True,
    )
    def task_confirm_source_update(
        project_id: str,
        session_id: str,
        confirmation: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "task_confirm_source_update",
            application.sessions.confirm_source_update,
            project_id,
            session_id,
            confirmation=confirmation,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_refresh",
        title="Build PV Refresh candidate",
        description=(
            "Rerun the same deterministic engine against the complete confirmed "
            "final repository state, calculate exact file Delta, append lineage, "
            "and seal the next candidate. Entry and exit slips are automatic internal "
            "artifacts. It never promotes or pushes remotely."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Building PV Refresh candidate", "PV Refresh candidate sealed"),
        structured_output=True,
    )
    def pv_refresh(project_id: str, session_id: str) -> dict[str, Any]:
        return application.invoke(
            "pv_refresh",
            application.refresh,
            project_id,
            session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="task_complete_and_refresh",
        title="Complete task and automatically seal exit PV",
        description=(
            "Confirm the exact host-specific final source boundary and immediately "
            "run deterministic Refresh in one governed operation. Entry and exit "
            "slips are generated automatically, the candidate remains unaccepted, "
            "and the result stops at the six-way HIL. An optional exact ordered "
            "batch can append QUEUED -> ACTIVE -> DONE for every queued Delta only "
            "when each task has bounded implementation and verification evidence. "
            "Users do not need a separate Refresh or exit command."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Completing task and sealing exit candidate",
            "Exit candidate sealed; HIL required",
        ),
        structured_output=True,
    )
    def task_complete_and_refresh(
        project_id: str,
        session_id: str,
        confirmation: str,
        batch_task_evidence: list[dict[str, Any]] | None = None,
        batch_completion_confirmation: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "task_complete_and_refresh",
            application.complete_task_and_refresh,
            project_id,
            session_id,
            confirmation=confirmation,
            batch_task_evidence=batch_task_evidence,
            batch_completion_confirmation=batch_completion_confirmation,
            lifecycle=True,
        )

    @mcp.tool(
        name="hil_decide",
        title="Record exact six-way HIL decision",
        description=(
            "Record APPROVE_WITH_DELTA, MORE_RESEARCH, REJECT, FAIL, or pointer-only "
            "ROLLBACK for one pending candidate. Exact APPROVE is deliberately "
            "rejected here and may promote only through pv_fuse. "
            "ROLLBACK preserves the candidate and accepted history; a bare target "
            "resolves to the current prompt/session entry PV."
        ),
        annotations=_HIL_WRITE,
        meta=_meta("Recording human HIL decision", "HIL decision recorded"),
        structured_output=True,
    )
    def hil_decide(
        project_id: str,
        session_id: str,
        decision: str,
        decided_by: str,
        reason: str | None = None,
        correction_delta: str | None = None,
        research_question: str | None = None,
        rollback_to: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "hil_decide",
            application.record_hil_decision,
            project_id,
            session_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            correction_delta=correction_delta,
            research_question=research_question,
            rollback_to=rollback_to,
            decision_id=decision_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_fuse",
        title="Fuse candidate with exact APPROVE",
        description=(
            "Require the exact case-sensitive token APPROVE, promote the pending "
            "candidate byte-for-byte with compare-and-swap, and seal the exact "
            "accepted-entry handoff. That handoff makes later user-requested or "
            "context-exhaustion State Travel eligible; it never auto-travels. No "
            "rebuild or remake occurs."
        ),
        annotations=_HIL_WRITE,
        meta=_meta(
            "Fusing approved PV candidate",
            "PV fused; accepted-entry handoff sealed",
        ),
        structured_output=True,
    )
    def pv_fuse(
        project_id: str,
        session_id: str,
        approval: str,
        decided_by: str,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_fuse",
            application.fuse,
            project_id,
            session_id,
            approval=approval,
            decided_by=decided_by,
            decision_id=decision_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_state_travel_prepare",
        title="Prepare exact-work State Travel",
        description=(
            "Idempotently seal the exact active governed boundary for a fresh host "
            "task/chat. By default unfinished tasks or candidates preserve state, "
            "source, pointer base, Plan Lane rows, additive Deltas, resume row, and "
            "execution profile; explicit ACCEPTED_ENTRY selects accepted context. "
            "This does not claim the host window or model selector was changed."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Preparing exact-work State Travel",
            "State Travel handoff prepared",
        ),
        structured_output=True,
    )
    def pv_state_travel_prepare(
        project_id: str,
        session_id: str,
        resume_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_state_travel_prepare",
            application.prepare_state_travel,
            project_id,
            session_id,
            resume_contract=resume_contract,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_state_travel_direct_force_same_worktree",
        title="Verify direct same-worktree State Travel",
        description=(
            "Use the no-seal recovery route exactly once for a genuinely new "
            "native Codex task that shares the source worktree. The caller supplies "
            "only source, donor, and destination task identities plus the visible "
            "destination title. The server atomically derives the replay guard, "
            "dirty source, pointer baseline, Plan, plugin, runtime, Flash, profile, "
            "and opaque runtime attestation. Caller-supplied binding payloads, "
            "nonces, hashes, PIDs, runtime IDs, or pointer fields are not accepted. "
            "The fixed 1+9 batch comes from persisted canonical host "
            "Plan authority, never a sliding active-row window. It "
            "atomically verifies source/donor/destination task identities, exact "
            "dirty bytes, PV pointer baseline, live Plan/fixed-batch/HIL anchors, installed "
            "plugin/catalog, Flash/runtime/profile, sole-writer and hooks-off laws; "
            "then binds the existing governed session to the destination. It never "
            "calls or consumes sealed prepare/resume, creates a candidate, infers "
            "HIL, moves a pointer, runs Git, installs, or replays."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Verifying direct same-worktree State Travel",
            "Direct same-worktree State Travel verified",
        ),
        structured_output=True,
    )
    def pv_state_travel_direct_force_same_worktree(
        project_id: str,
        session_id: str,
        authoritative_source_task_id: str,
        runtime_attachment_donor_task_id: str,
        destination_task_id: str,
        destination_task_title: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_state_travel_direct_force_same_worktree",
            application.direct_force_same_worktree_state_travel,
            project_id=project_id,
            session_id=session_id,
            authoritative_source_task_id=authoritative_source_task_id,
            runtime_attachment_donor_task_id=(
                runtime_attachment_donor_task_id
            ),
            destination_task_id=destination_task_id,
            destination_task_title=destination_task_title,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_state_travel_resume",
        title="Verify State Travel in a fresh host window",
        description=(
            "Before binding the fresh host session, fail closed unless its supplied "
            "model/submodel/reasoning/speed selectors match the prepared profile. "
            "Then verify Flash, pointer base, any candidate, Plan Lane, live source, "
            "and exact resume contract. Unfinished work becomes resume-ready at the "
            "same row without clearing state; accepted entry waits for the user."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Verifying State Travel entry",
            "State Travel verified; waiting for user",
        ),
        structured_output=True,
    )
    def pv_state_travel_resume(
        project_id: str,
        session_id: str,
        handoff_id: str,
        host: str,
        host_session_id: str,
        ephemeral: bool,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_state_travel_resume",
            application.resume_state_travel,
            project_id=project_id,
            session_id=session_id,
            handoff_id=handoff_id,
            host=host,
            host_session_id=host_session_id,
            ephemeral=ephemeral,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_rollback",
        title="Travel to an immutable accepted PV",
        description=(
            "Move only the accepted pointer to any immutable accepted PV after a "
            "compare-and-swap check. Target PVn directly, use PROMPT <index> or TURN "
            "<id>, or omit the target to use the current prompt/session entry PV. "
            "Accepted history, candidates, source bytes, lane databases, and the "
            "monotonic next-PV ordinal are preserved. This is an explicit HIL action "
            "and never restores or rewrites the live source."
        ),
        annotations=_HIL_WRITE,
        meta=_meta("Verifying rollback state travel", "Rollback state travel recorded"),
        structured_output=True,
    )
    def pv_rollback(
        project_id: str,
        session_id: str,
        decided_by: str,
        rollback_to: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_rollback",
            application.rollback,
            project_id,
            session_id,
            decided_by=decided_by,
            rollback_to=rollback_to,
            decision_id=decision_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="hil_return_to_accepted",
        title="Return a rejected or failed run to accepted state",
        description=(
            "After REJECT or FAIL, verify that an accepted PV exists, the accepted "
            "pointer did not move, and the live repository was restored to that exact "
            "accepted source. Then clear only the bounded run state and append a "
            "visible return receipt. This never moves the accepted pointer."
        ),
        annotations=_HIL_WRITE,
        meta=_meta(
            "Verifying return to accepted state",
            "Returned to accepted state without pointer movement",
        ),
        structured_output=True,
    )
    def hil_return_to_accepted(
        project_id: str,
        session_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "hil_return_to_accepted",
            application.sessions.return_to_accepted,
            project_id,
            session_id,
            reason=reason,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_begin_next_turn",
        title="Enter next turn from latest accepted PV",
        description=(
            "Enter the next accepted-PV turn. A prepared State Travel handoff "
            "remains blocking by default. When the user explicitly chooses to "
            "continue in the unchanged host, pass continue_same_host=true and the "
            "exact reason EXPLICIT_USER_CONTINUATION; the sealed receipt is "
            "preserved and visibly superseded without pointer movement."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Entering next accepted PV", "Next PV entry ready"),
        structured_output=True,
    )
    def pv_begin_next_turn(
        project_id: str,
        session_id: str,
        continue_same_host: bool = False,
        continuation_reason: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_begin_next_turn",
            application.sessions.begin_next_turn,
            project_id,
            session_id,
            continue_same_host=continue_same_host,
            continuation_reason=continuation_reason,
            lifecycle=True,
        )

    @mcp.tool(
        name="session_close",
        title="Close governed session",
        description=(
            "Close one governed session with a visible reason and release the "
            "one-session gate. Detach Flash context and prompt/response capture while "
            "preserving the installed plugin, locked Flash verification receipt, "
            "PVs, candidates, lineage, backlog, immutable store, and pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Closing governed session", "Governed session closed"),
        structured_output=True,
    )
    def session_close(
        project_id: str,
        session_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "session_close",
            application.sessions.close,
            project_id,
            session_id,
            reason=reason,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_status",
        title="Read bounded project and pointer status",
        description=(
            "Read a compact project, pointer, active-session, count, and current-"
            "accepted-package projection. Accepted history, candidate ID lists, "
            "lane rows, full Plan authority, and PV payloads stay behind bounded "
            "exact query routes. Performs no write."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading PV status", "PV status ready"),
        structured_output=True,
    )
    def pv_status(project_id: str) -> dict[str, Any]:
        return application.invoke(
            "pv_status",
            application.status_window,
            project_id,
        )

    @mcp.tool(
        name="prompt_index_status",
        title="Read prompt-entry rollback index",
        description=(
            "Read bounded prompt indexes, turn IDs, entry PVs, pointer generations, "
            "and record hashes for the currently bound host task. Raw prompt text and "
            "private model reasoning are never stored."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading prompt-entry index", "Prompt-entry index ready"),
        structured_output=True,
    )
    def prompt_index_status(
        project_id: str,
        session_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        return application.invoke(
            "prompt_index_status",
            application.prompt_index_status,
            project_id,
            session_id,
            limit=limit,
        )

    @mcp.tool(
        name="search",
        title="Search accepted PV source intelligence",
        description=(
            "Progressive read-only search across deterministic FTS chunks, symbols, "
            "and paths. Defaults to the current accepted PV; an explicitly named "
            "candidate is labeled UNACCEPTED_CANDIDATE and never presented as truth. "
            "An optional candidate overlay remains separate from accepted results and "
            "requires its exact authorization token."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Searching PV source intelligence", "PV search complete"),
        structured_output=True,
    )
    def search(
        project_id: str,
        query: str,
        pv_ref: str | None = None,
        limit: int = 20,
        candidate_overlay_ref: str | None = None,
        candidate_overlay_authorization: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "search",
            application.reader.search,
            project_id,
            query,
            pv_ref=pv_ref,
            limit=limit,
            candidate_overlay_ref=candidate_overlay_ref,
            candidate_overlay_authorization=candidate_overlay_authorization,
        )

    @mcp.tool(
        name="fetch",
        title="Fetch exact PV file or chunk",
        description=(
            "Standard read-only fetch for file:<path>, chunk:<id>, or symbol:<id>. "
            "Text files support bounded line windows with exact file hash and source "
            "commit provenance; binary output is bounded base64."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Fetching exact PV evidence", "PV evidence fetched"),
        structured_output=True,
    )
    def fetch(
        project_id: str,
        ref_id: str,
        pv_ref: str | None = None,
        max_bytes: int = 256000,
        start_line: int | None = None,
        end_line: int | None = None,
        max_lines: int = 400,
    ) -> dict[str, Any]:
        return application.invoke(
            "fetch",
            application.reader.fetch,
            project_id,
            ref_id,
            pv_ref=pv_ref,
            max_bytes=max_bytes,
            start_line=start_line,
            end_line=end_line,
            max_lines=max_lines,
        )

    @mcp.tool(
        name="pv_summary",
        title="Read deterministic PV summary",
        description=(
            "Read repository identity, file/chunk/symbol/import/dependency/route "
            "counts, and code-family distribution from one validated PV."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading PV summary", "PV summary ready"),
        structured_output=True,
    )
    def pv_summary(
        project_id: str,
        pv_ref: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_summary",
            application.reader.project_summary,
            project_id,
            pv_ref=pv_ref,
        )

    @mcp.tool(
        name="pv_query",
        title="Run focused PV intelligence query",
        description=(
            "Run one allowlisted read-only query kind: files, symbols, imports, "
            "dependencies, routes, or receipts. Arbitrary SQL and multiple "
            "statements are blocked."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Querying PV intelligence", "PV query complete"),
        structured_output=True,
    )
    def pv_query(
        project_id: str,
        query_kind: str,
        pv_ref: str | None = None,
        value: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_query",
            application.reader.query,
            project_id,
            query_kind,
            pv_ref=pv_ref,
            value=value,
            limit=limit,
        )

    @mcp.tool(
        name="pv_diff",
        title="Compare two immutable PVs",
        description=(
            "Read exact added, modified, and deleted file identities between two "
            "validated accepted or candidate PVs. Performs no source or pointer write."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Comparing PVs", "PV comparison complete"),
        structured_output=True,
    )
    def pv_diff(
        project_id: str,
        left_pv: str,
        right_pv: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_diff",
            application.reader.diff,
            project_id,
            left_pv,
            right_pv,
        )

    @mcp.tool(
        name="remote_git_prepare_push",
        title="Prepare automatic exact test-branch push",
        description=(
            "Prepare—but do not execute—one remote branch push bound to the current "
            "accepted PV and pointer generation. The exact sole registered "
            "non-protected test branch is preauthorized without a per-push token."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Preparing remote Git action", "Remote Git action awaiting confirmation"
        ),
        structured_output=True,
    )
    def remote_git_prepare_push(
        project_id: str,
        requested_by: str,
        remote: str,
        local_ref: str,
        remote_branch: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "remote_git_prepare_push",
            application.remote_git.prepare_push,
            project_id,
            requested_by=requested_by,
            remote=remote,
            local_ref=local_ref,
            remote_branch=remote_branch,
            lifecycle=True,
        )

    @mcp.tool(
        name="remote_git_execute_push",
        title="Execute preauthorized exact test-branch push",
        description=(
            "Execute exactly one previously prepared remote branch push only when "
            "the sole registered branch, accepted pointer, commit, and tree remain "
            "exact. Uses host-managed credentials; never pushes main, merges, or "
            "approves a pull request."
        ),
        annotations=_REMOTE_WRITE,
        meta=_meta("Executing confirmed remote Git push", "Remote Git push finished"),
        structured_output=True,
    )
    def remote_git_execute_push(
        project_id: str,
        action_id: str,
        executed_by: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "remote_git_execute_push",
            application.remote_git.execute_push,
            project_id,
            action_id=action_id,
            executed_by=executed_by,
            lifecycle=True,
        )

    def invoke_native_sdk_action(
        project_id: str,
        session_id: str,
        request_id: str,
        module_id: str,
        operation: str,
        payload: dict[str, Any],
        read_only: bool,
    ) -> dict[str, Any]:
        write_scope = () if read_only else (f"{module_id}:{operation}",)
        sdk, binding = build_live_local_sdk_context(
            backend_application,
            project_id=project_id,
            session_id=session_id,
            write_scope=write_scope,
            canon_dispatcher=canon_dispatcher,
        )
        return sdk.invoke(
            module_id=module_id,
            operation=operation,
            binding=binding,
            payload=payload,
            request_id=request_id,
        )

    def sdk_action_callable(
        *,
        tool_name: str,
        module_id: str,
        operation: str,
        read_only: bool,
    ) -> Any:
        def sdk_action(
            project_id: str,
            session_id: str,
            request_id: str,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return application.invoke(
                tool_name,
                invoke_native_sdk_action,
                project_id,
                session_id,
                request_id,
                module_id,
                operation,
                dict(payload or {}),
                read_only,
                lifecycle=not read_only,
            )

        sdk_action.__name__ = tool_name
        sdk_action.__qualname__ = tool_name
        return sdk_action

    for (
        sdk_tool_name,
        sdk_title,
        sdk_description,
        sdk_module_id,
        sdk_operation,
        sdk_read_only,
    ) in SDK_NATIVE_ACTIONS:
        mcp.tool(
            name=sdk_tool_name,
            title=sdk_title,
            description=sdk_description,
            annotations=_READ_ONLY if sdk_read_only else _LOCAL_WRITE,
            meta=_meta(f"Running {sdk_title}", f"{sdk_title} finished"),
            structured_output=True,
        )(
            sdk_action_callable(
                tool_name=sdk_tool_name,
                module_id=sdk_module_id,
                operation=sdk_operation,
                read_only=sdk_read_only,
            )
        )

    failure_boundary = _apply_governed_tool_failure_boundary(
        mcp,
        backend_application,
    )
    if failure_boundary["status"] != "PASS":
        raise RuntimeError("Evidence Lane public handler boundary is incomplete.")
    mcp._evidence_lane_public_handler_failure_boundary = failure_boundary  # type: ignore[attr-defined]
    skill_mcp_routing_review = inspect_skill_mcp_routing(
        resolve_skill_mcp_plugin_root(),
        (tool.name for tool in mcp._tool_manager.list_tools()),
    )
    _apply_evidence_lane_tool_icons(mcp, exact_public_site)
    if oauth_config is not None:
        _apply_oauth_tool_security_schemes(mcp, exact_exposure_profile)
    # Seal the installed-version registry before applying an optional deployment
    # allowlist.  The allowlist intentionally removes tools from the live FastMCP
    # manager, so deriving registry parity after that removal falsely classifies a
    # valid restricted exposure as a stale or duplicate native route.
    route_receipt = _native_route_receipt(
        mcp,
        exact_exposure_profile,
        oauth_config,
    )
    if route_receipt["status"] != "PASS":
        raise RuntimeError("Evidence Lane native MCP tool names are not unique.")
    exposure_receipt = apply_fastmcp_tool_filter(mcp, effective_allowed_tool_names)
    mcp._evidence_lane_tool_exposure_receipt = exposure_receipt  # type: ignore[attr-defined]
    mcp._evidence_lane_exposure_profile = exact_exposure_profile  # type: ignore[attr-defined]
    route_receipt["tool_exposure_policy"] = {
        "schema": exposure_receipt["schema"],
        "mode": exposure_receipt["mode"],
        "registered_tool_count": len(exposure_receipt["registered_tools"]),
        "exposed_tool_count": len(exposure_receipt["exposed_tools"]),
        "removed_tool_count": len(exposure_receipt["removed_tools"]),
        "policy_sha256": exposure_receipt["policy_sha256"],
        "receipt_sha256": exposure_receipt["receipt_sha256"],
    }
    route_receipt["service_route_review"] = {
        "schema": service_route_review["schema"],
        "status": service_route_review["status"],
        "service_public_method_count": service_route_review[
            "service_public_method_count"
        ],
        "mcp_workflow_method_count": service_route_review["mcp_workflow_method_count"],
        "sdk_workflow_method_count": service_route_review["sdk_workflow_method_count"],
        "eligible_unrouted_method_count": service_route_review[
            "eligible_unrouted_method_count"
        ],
        "receipt_sha256": service_route_review["receipt_sha256"],
    }
    route_receipt["skill_mcp_routing"] = {
        "schema": skill_mcp_routing_review["schema"],
        "status": skill_mcp_routing_review["status"],
        "routing_schema": skill_mcp_routing_review["routing_schema"],
        "manifest_format": skill_mcp_routing_review["manifest_format"],
        "server_identity": skill_mcp_routing_review["server_identity"],
        "skill_count": skill_mcp_routing_review["skill_count"],
        "tool_count": skill_mcp_routing_review["tool_count"],
        "owned_tool_count": skill_mcp_routing_review["owned_tool_count"],
        "low_level_tool_count": skill_mcp_routing_review["low_level_tool_count"],
        "missing_tool_behavior": skill_mcp_routing_review["missing_tool_behavior"],
        "manifest_sha256": skill_mcp_routing_review["manifest_sha256"],
        "receipt_sha256": skill_mcp_routing_review["receipt_sha256"],
    }
    mcp._evidence_lane_service_route_review = service_route_review  # type: ignore[attr-defined]
    mcp._evidence_lane_skill_mcp_routing_review = skill_mcp_routing_review  # type: ignore[attr-defined]
    mcp._evidence_lane_native_route_receipt = route_receipt  # type: ignore[attr-defined]
    return install_tool_namespace_compat(mcp)


def run_server(
    *,
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    if transport == "sse":
        raise RuntimeError(
            "SSE transport is disabled; use stdio or authenticated streamable-http."
        )
    bearer = os.environ.get("EVIDENCE_LANE_MCP_BEARER_TOKEN", "").strip()
    base_url = os.environ.get("EVIDENCE_LANE_MCP_BASE_URL", "").strip() or None
    oauth_values = {
        "issuer_url": os.environ.get("EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL", "").strip(),
        "jwks_url": os.environ.get("EVIDENCE_LANE_MCP_OAUTH_JWKS_URL", "").strip(),
        "audience": os.environ.get("EVIDENCE_LANE_MCP_OAUTH_AUDIENCE", "").strip(),
        "deployment_environment": os.environ.get(
            "EVIDENCE_LANE_MCP_OAUTH_ENVIRONMENT", ""
        ).strip(),
        "allowed_client_ids": os.environ.get(
            "EVIDENCE_LANE_MCP_OAUTH_ALLOWED_CLIENT_IDS", ""
        ).strip(),
        "allowed_roles": os.environ.get(
            "EVIDENCE_LANE_MCP_OAUTH_ALLOWED_ROLES", ""
        ).strip(),
    }
    oauth_any = any(oauth_values.values())
    oauth_complete = all(oauth_values.values())
    if oauth_any and not oauth_complete:
        missing = sorted(key for key, value in oauth_values.items() if not value)
        raise RuntimeError(
            "Incomplete OAuth configuration; missing: " + ", ".join(missing)
        )
    if bearer and oauth_complete:
        raise RuntimeError("Configure either static bearer or OAuth, not both.")
    oauth_config = None
    if oauth_complete:
        scopes = tuple(
            item
            for item in os.environ.get(
                "EVIDENCE_LANE_MCP_OAUTH_SCOPES",
                READ_SCOPE,
            ).split()
            if item
        )
        if set(scopes) != {READ_SCOPE}:
            raise RuntimeError(
                "EVIDENCE_LANE_MCP_OAUTH_SCOPES is the base transport gate and "
                f"must contain only {READ_SCOPE}; per-tool policy adds write scopes."
            )
        algorithms = tuple(
            item.strip()
            for item in os.environ.get(
                "EVIDENCE_LANE_MCP_OAUTH_ALGORITHMS", "RS256"
            ).split(",")
            if item.strip()
        )
        oauth_config = OAuthJWTConfig(
            issuer_url=oauth_values["issuer_url"],
            jwks_url=oauth_values["jwks_url"],
            audience=oauth_values["audience"],
            required_scopes=scopes,
            deployment_environment=oauth_values["deployment_environment"],
            allowed_client_ids=tuple(
                item.strip()
                for item in oauth_values["allowed_client_ids"].split(",")
                if item.strip()
            ),
            allowed_roles=tuple(
                item.strip()
                for item in oauth_values["allowed_roles"].split(",")
                if item.strip()
            ),
            algorithms=algorithms,
        )
    if transport == "streamable-http" and host not in {"127.0.0.1", "localhost", "::1"}:
        if not bearer and oauth_config is None:
            raise RuntimeError(
                "Non-loopback HTTP requires static bearer or OAuth authentication."
            )
        if not base_url or not base_url.startswith("https://"):
            raise RuntimeError(
                "Non-loopback HTTP requires an HTTPS EVIDENCE_LANE_MCP_BASE_URL."
            )
    application = EvidenceLaneService()
    application.sessions.ensure_installation()
    # Native OCR/ONNX dependencies must be loaded before FastMCP starts its
    # event loop.  Lane execution remains parallel; the cached engine is only
    # serialized at its documented shared call boundary.
    prewarm_native_dependencies()
    server = create_mcp_server(
        service=application,
        host=host,
        port=port,
        bearer_token=bearer or None,
        base_url=base_url,
        oauth_config=oauth_config,
        allowed_tool_names=os.environ.get("EVIDENCE_LANE_MCP_ALLOWED_TOOLS"),
        exposure_profile=os.environ.get("EVIDENCE_LANE_MCP_EXPOSURE_PROFILE"),
    )
    if transport == "stdio":
        anyio.run(run_discovery_compatible_stdio, server)
    else:
        server.run(transport=transport)

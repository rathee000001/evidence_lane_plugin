"""Host-aware ENV/UOP and Entry/Exit Slip continuity contracts."""

from __future__ import annotations

from typing import Any, cast

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes
from .models import HostKind, normalize_host_kind

RUNTIME_CONTINUITY_SCHEMA = "evidence-lane.runtime-continuity.v1"


def build_runtime_continuity(
    *,
    project_id: str,
    host: HostKind | str,
    host_session_id: str | None,
    ephemeral: bool,
    persistence_route: dict[str, Any],
    flash: dict[str, Any],
    accepted_pv: str | None,
    pointer_generation: int,
    accepted_manifest_sha256: str | None,
    accepted_package_sha256: str | None,
    accepted_promotable_under_current_rules: bool | None = None,
) -> dict[str, Any]:
    """Bind a host route to the locked Flash and exact entry pointer."""

    kind = normalize_host_kind(host)
    require(
        flash.get("status") == "PASS"
        and bool(flash.get("authority_digest"))
        and bool(flash.get("receipt_sha256")),
        "RUNTIME_CONTINUITY_FLASH_REQUIRED",
        "Runtime continuity requires a verified locked ENV/UOP Flash receipt.",
        status="BLOCKED",
    )
    required_route_fields = {
        "mode",
        "server_filesystem",
        "host_profile",
        "primary_runtime_authority",
        "google_drive_policy",
        "mcp_read_policy",
        "mcp_write_policy",
        "env_continuity_policy",
    }
    require(
        required_route_fields <= set(persistence_route),
        "RUNTIME_CONTINUITY_STORAGE_ROUTE_INVALID",
        "Runtime continuity requires the complete host storage route.",
        status="MISMATCH",
        missing=sorted(required_route_fields - set(persistence_route)),
    )
    raw_project_route = persistence_route.get("project_route")
    require(
        isinstance(raw_project_route, dict)
        and raw_project_route.get("project_id") == project_id
        and raw_project_route.get("relative_project_route")
        == f"projects/{project_id}"
        and raw_project_route.get("contained_beneath_store_root") is True
        and persistence_route.get("transport_project_binding")
        == "EXPLICIT_PROJECT_ID_PER_PROJECT_SCOPED_TOOL"
        and persistence_route.get("cross_project_fallback_allowed") is False,
        "RUNTIME_CONTINUITY_PROJECT_ROUTE_INVALID",
        "Runtime continuity requires one exact project route beneath the configured store root.",
        status="MISMATCH",
        project_id=project_id,
    )
    project_route = cast(dict[str, Any], raw_project_route)
    if kind == HostKind.CHATGPT:
        require(
            persistence_route["google_drive_policy"]
            == "FORBIDDEN_FOR_CHATGPT_RUNTIME",
            "CHATGPT_GOOGLE_DRIVE_ROUTE_FORBIDDEN",
            "ChatGPT must use the durable MCP host and never Google Drive runtime state.",
            status="BLOCKED",
        )
    accepted_integrity_validated = bool(
        accepted_pv and accepted_manifest_sha256 and accepted_package_sha256
    )
    accepted_compatibility_state = (
        "NO_ACCEPTED_PV"
        if not accepted_pv
        else "CURRENT_RULES_PROMOTABLE"
        if accepted_promotable_under_current_rules is True
        else "ACCEPTED_IMMUTABLE_HISTORICAL_SCHEMA"
    )
    core = {
        "schema": RUNTIME_CONTINUITY_SCHEMA,
        "host": {
            "kind": kind.value,
            "host_session_id": str(host_session_id or "").strip() or None,
            "ephemeral": bool(ephemeral),
        },
        "project": {
            "project_id": project_id,
            "relative_project_route": project_route["relative_project_route"],
            "resolved_store_root": project_route["resolved_store_root"],
            "resolved_project_root": project_route["resolved_project_root"],
            "transport_project_binding": persistence_route[
                "transport_project_binding"
            ],
            "cross_project_fallback_allowed": False,
        },
        "storage": dict(persistence_route),
        "env_uop": {
            "authority_version": flash.get("authority_version"),
            "authority_digest": flash["authority_digest"],
            "flash_receipt_sha256": flash["receipt_sha256"],
            "bytes_in_pv": False,
            "reverify_on_every_boot_or_resume": True,
            "continuity_carrier": (
                "HASHED_REFERENCE_IN_ENTRY_AND_EXIT_SLIPS_PLUS_RUNTIME_FLASH"
            ),
        },
        "entry_pointer": {
            "accepted_pv": accepted_pv,
            "pointer_generation": pointer_generation,
            "accepted_manifest_sha256": accepted_manifest_sha256,
            "accepted_package_sha256": accepted_package_sha256,
            "accepted_authority_integrity_validated": accepted_integrity_validated,
            "promotability_required_for_boot_or_resume": False,
            "promotable_under_current_rules": (
                accepted_promotable_under_current_rules
            ),
            "compatibility_state": accepted_compatibility_state,
            "successor_candidate_must_pass_current_rules": True,
        },
        "mcp_access": {
            "read": persistence_route["mcp_read_policy"],
            "write": persistence_route["mcp_write_policy"],
            "client_bypasses_mcp_for_runtime_writes": False,
            "env_uop_governs_writes": True,
            "one_writer_required": True,
        },
        "entry_exit_slip": {
            "continuity_reference_required": True,
            "env_uop_bytes_embedded": False,
            "pointer_movement": False,
            "hil_approval_inferred": False,
        },
        "google_drive": {
            "policy": persistence_route["google_drive_policy"],
            "primary_runtime_authority": False,
        },
        "lifecycle_effect": "NONE",
        "candidate_created": False,
        "pointer_moved": False,
        "hil_approval_inferred": False,
    }
    core["continuity_receipt_sha256"] = sha256_bytes(canonical_json_bytes(core))
    return core


def validate_runtime_continuity(value: dict[str, Any]) -> dict[str, Any]:
    require(
        value.get("schema") == RUNTIME_CONTINUITY_SCHEMA,
        "RUNTIME_CONTINUITY_SCHEMA_INVALID",
        "The runtime continuity schema is not supported.",
        status="MISMATCH",
    )
    expected = str(value.get("continuity_receipt_sha256") or "")
    body = {key: item for key, item in value.items() if key != "continuity_receipt_sha256"}
    actual = sha256_bytes(canonical_json_bytes(body))
    require(
        bool(expected) and expected == actual,
        "RUNTIME_CONTINUITY_RECEIPT_INVALID",
        "The runtime continuity receipt does not match its declared fields.",
        status="MISMATCH",
        expected=expected or None,
        actual=actual,
    )
    require(
        value.get("env_uop", {}).get("bytes_in_pv") is False
        and value.get("entry_exit_slip", {}).get("env_uop_bytes_embedded") is False
        and value.get("entry_pointer", {}).get(
            "promotability_required_for_boot_or_resume"
        )
        is False
        and value.get("entry_pointer", {}).get(
            "successor_candidate_must_pass_current_rules"
        )
        is True
        and value.get("mcp_access", {}).get("client_bypasses_mcp_for_runtime_writes")
        is False
        and value.get("pointer_moved") is False
        and value.get("hil_approval_inferred") is False,
        "RUNTIME_CONTINUITY_BOUNDARY_INVALID",
        "Runtime continuity must remain reference-only, MCP-governed, and pointer-neutral.",
        status="FAIL",
    )
    # Receipts created before portable project-route sealing remain immutable
    # accepted evidence. New receipts carry and validate this exact route block.
    project = value.get("project")
    if project is not None:
        project_id = str(project.get("project_id") or "")
        storage_route = value.get("storage", {}).get("project_route", {})
        require(
            bool(project_id)
            and project.get("relative_project_route") == f"projects/{project_id}"
            and project.get("cross_project_fallback_allowed") is False
            and storage_route.get("project_id") == project_id
            and storage_route.get("contained_beneath_store_root") is True,
            "RUNTIME_CONTINUITY_PROJECT_ROUTE_INVALID",
            "The sealed runtime continuity project route is invalid.",
            status="FAIL",
        )
    return value

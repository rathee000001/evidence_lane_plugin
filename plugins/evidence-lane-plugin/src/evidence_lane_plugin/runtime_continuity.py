"""Host-aware ENV/UOP and Entry/Exit Slip continuity contracts."""

from __future__ import annotations

from typing import Any

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes
from .models import HostKind, normalize_host_kind

RUNTIME_CONTINUITY_SCHEMA = "evidence-lane.runtime-continuity.v1"


def build_runtime_continuity(
    *,
    host: HostKind | str,
    host_session_id: str | None,
    ephemeral: bool,
    persistence_route: dict[str, Any],
    flash: dict[str, Any],
    accepted_pv: str | None,
    pointer_generation: int,
    accepted_manifest_sha256: str | None,
    accepted_package_sha256: str | None,
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
    if kind == HostKind.CHATGPT:
        require(
            persistence_route["google_drive_policy"]
            == "FORBIDDEN_FOR_CHATGPT_RUNTIME",
            "CHATGPT_GOOGLE_DRIVE_ROUTE_FORBIDDEN",
            "ChatGPT must use the durable MCP host and never Google Drive runtime state.",
            status="BLOCKED",
        )
    core = {
        "schema": RUNTIME_CONTINUITY_SCHEMA,
        "host": {
            "kind": kind.value,
            "host_session_id": str(host_session_id or "").strip() or None,
            "ephemeral": bool(ephemeral),
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
        and value.get("mcp_access", {}).get("client_bypasses_mcp_for_runtime_writes")
        is False
        and value.get("pointer_moved") is False
        and value.get("hil_approval_inferred") is False,
        "RUNTIME_CONTINUITY_BOUNDARY_INVALID",
        "Runtime continuity must remain reference-only, MCP-governed, and pointer-neutral.",
        status="FAIL",
    )
    return value

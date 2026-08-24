"""Host-aware ENV/UOP and Entry/Exit Slip continuity contracts."""

from __future__ import annotations

from typing import Any, cast

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes
from .models import HostKind, normalize_host_kind
from .runtime_host_classifier import (
    build_runtime_namespace,
    validate_runtime_host_classifier,
    validate_runtime_namespace,
)

RUNTIME_CONTINUITY_SCHEMA = "evidence-lane.runtime-continuity.v1"


def build_runtime_continuity(
    *,
    project_id: str,
    governed_session_id: str,
    workspace_id: str,
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
    accepted_validation_scope: str | None = None,
    accepted_artifact_available: bool | None = None,
    accepted_archive_queried: bool | None = None,
    host_entry_consumption: dict[str, Any] | None = None,
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
    route_is_legacy_contained = bool(
        isinstance(raw_project_route, dict)
        and raw_project_route.get("contained_beneath_store_root") is True
    )
    route_is_external_project_authority = bool(
        isinstance(raw_project_route, dict)
        and raw_project_route.get("project_authority_mode")
        == "EXPLICIT_USER_PROJECT_ROOT"
        and raw_project_route.get("governed_user_project_authority") is True
        and raw_project_route.get("runtime_separated") is True
        and raw_project_route.get("contained_beneath_store_root") is False
    )
    require(
        isinstance(raw_project_route, dict)
        and raw_project_route.get("project_id") == project_id
        and raw_project_route.get("relative_project_route")
        == f"projects/{project_id}"
        and (route_is_legacy_contained or route_is_external_project_authority)
        and persistence_route.get("transport_project_binding")
        == "EXPLICIT_PROJECT_ID_PER_PROJECT_SCOPED_TOOL"
        and persistence_route.get("cross_project_fallback_allowed") is False,
        "RUNTIME_CONTINUITY_PROJECT_ROUTE_INVALID",
        "Runtime continuity requires one exact legacy-contained or external user-project authority route.",
        status="MISMATCH",
        project_id=project_id,
    )
    project_route = cast(dict[str, Any], raw_project_route)
    runtime_classifier = validate_runtime_host_classifier(
        cast(dict[str, Any], persistence_route.get("runtime_classifier") or {})
    )
    runtime_namespace = build_runtime_namespace(
        project_id=project_id,
        governed_session_id=governed_session_id,
        workspace_id=workspace_id,
        host_session_id=host_session_id,
        classifier=runtime_classifier,
    )
    accepted_integrity_validated = bool(
        accepted_pv and accepted_manifest_sha256 and accepted_package_sha256
    )
    exact_validation_scope = str(accepted_validation_scope or "").strip() or (
        "CURRENT_ACCEPTED_ARTIFACT" if accepted_integrity_validated else "NOT_APPLICABLE"
    )
    exact_artifact_available = (
        bool(accepted_artifact_available)
        if accepted_artifact_available is not None
        else None
    )
    exact_archive_queried = (
        bool(accepted_archive_queried)
        if accepted_archive_queried is not None
        else accepted_integrity_validated
    )
    accepted_compatibility_state = (
        "NO_ACCEPTED_PV"
        if not accepted_pv
        else "CURRENT_RULES_PROMOTABLE"
        if accepted_promotable_under_current_rules is True
        else "ACCEPTED_IMMUTABLE_HISTORICAL_SCHEMA"
    )
    interaction_profile = str(
        persistence_route.get("interaction_profile") or "HOST_SURFACE_UNSPECIFIED"
    )
    headless_api = interaction_profile in {"HEADLESS_API", "DIRECT_CLI_API"}
    tunnel_requirement = str(
        persistence_route.get("tunnel_requirement")
        or "HOST_CAPABILITY_UNSPECIFIED"
    )
    if headless_api:
        require(
            tunnel_requirement == "NOT_REQUIRED_FOR_API_LAYER",
            "HEADLESS_API_TUNNEL_ROUTE_INVALID",
            "Headless API continuity must not depend on a tunnel.",
            status="MISMATCH",
        )
    host_entry_required = (
        persistence_route.get("server_filesystem")
        == "EPHEMERAL_OR_UNAVAILABLE"
    )
    host_entry_receipt_sha256: str | None = None
    if host_entry_required:
        receipt = (
            host_entry_consumption.get("receipt")
            if isinstance(host_entry_consumption, dict)
            else None
        )
        require(
            isinstance(host_entry_consumption, dict)
            and host_entry_consumption.get("status") == "PASS"
            and host_entry_consumption.get("state")
            in {"CONSUMED", "CONSUMED_IDEMPOTENT_REUSE"}
            and isinstance(receipt, dict)
            and receipt.get("schema")
            == "evidence-lane.host-entry-consumption.v1"
            and receipt.get("project_id") == project_id
            and receipt.get("evidence_session_id") == governed_session_id
            and receipt.get("accepted_pointer_generation") == pointer_generation
            and receipt.get("pointer_moved") is False
            and receipt.get("hil_inferred") is False,
            "RUNTIME_CONTINUITY_HOST_ENTRY_CONSUMPTION_REQUIRED",
            "An insufficiently durable host must consume one exact host-entry envelope before runtime continuity is issued.",
            status="BLOCKED",
        )
        receipt = cast(dict[str, Any], receipt)
        host_entry_receipt_sha256 = str(receipt["receipt_sha256"])
    host_entry_route = {
        "required_before_governed_work": host_entry_required,
        "state": (
            "CONSUMED_EXACT_ONCE_FROM_TRANSACTIONAL_CONNECTOR"
            if host_entry_required
            else "NOT_REQUIRED_DURABLE_LOCAL_AUTHORITY"
        ),
        "continuity_authority": (
            "HOST_ENTRY_ENVELOPE"
            if host_entry_required
            else persistence_route.get("primary_runtime_authority")
        ),
        "transactional_exact_once_required": host_entry_required,
        "accepted_pointer_generation_must_match": True,
        "worktree_and_four_authority_heads_must_match": True,
        "expiry_and_replay_nonce_required": host_entry_required,
        "consumption_receipt_sha256": host_entry_receipt_sha256,
        "candidate_overlay_requires_exact_authorization": True,
        "project_truth_promotion_allowed": False,
        "learning_promotion_allowed": False,
        "canon_acceptance_allowed": False,
        "hil_replay_allowed": False,
    }
    core = {
        "schema": RUNTIME_CONTINUITY_SCHEMA,
        "host": {
            "kind": kind.value,
            "host_session_id": str(host_session_id or "").strip() or None,
            "ephemeral": bool(ephemeral),
        },
        "runtime_classifier": runtime_classifier,
        "runtime_namespace": runtime_namespace,
        "project": {
            "project_id": project_id,
            "relative_project_route": project_route["relative_project_route"],
            "resolved_store_root": project_route["resolved_store_root"],
            "resolved_project_root": project_route["resolved_project_root"],
            "project_authority_mode": project_route.get(
                "project_authority_mode", "LEGACY_COMBINED_STORE_ROOT"
            ),
            "governed_user_project_authority": project_route.get(
                "governed_user_project_authority", False
            ),
            "runtime_separated": project_route.get("runtime_separated", False),
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
            "accepted_artifact_available": exact_artifact_available,
            "accepted_archive_queried": exact_archive_queried,
            "accepted_artifact_integrity_validated": bool(
                accepted_integrity_validated and exact_artifact_available
            ),
            "validation_scope": exact_validation_scope,
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
        "host_entry": host_entry_route,
        "invocation": {
            "interaction_profile": interaction_profile,
            "container_channel": runtime_classifier["container_channel"],
            "container_version": runtime_classifier["container_version"],
            "active_surface": runtime_classifier["active_surface"],
            "active_surface_evidence": runtime_classifier[
                "active_surface_evidence"
            ],
            "workspace_class": runtime_classifier["workspace_class"],
            "execution_profile": runtime_classifier["execution_profile"],
            "execution_profile_status": runtime_classifier[
                "execution_profile_status"
            ],
            "account_route": runtime_classifier["account_route"],
            "headless_api": headless_api,
            "api_billing_affects_storage_or_tunnel": False,
            "account_tier_affects_storage_or_tunnel": False,
            "tunnel_requirement": tunnel_requirement,
            "tunnel_required_for_api_layer": False if headless_api else None,
            "flash_verification": (
                "VERIFY_LOCKED_ENV_UOP_AT_EVERY_API_INVOCATION_ENTRY"
                if headless_api
                else "VERIFY_LOCKED_ENV_UOP_AT_EVERY_BOOT_OR_RESUME"
            ),
            "prior_state_load": (
                "EXACT_PROJECT_DURABLE_RUNTIME_PLUS_ACCEPTED_OR_PENDING_ENTRY_EXIT_SLIP"
            ),
            "exit_slip_next_prompt_label": "PV_EXIT_SUGGESTED_NEXT_PROMPT",
            "copyable_next_prompt_source": "EXIT_SLIP_NEXT_ACTION",
            "six_way_hil_preserved": True,
            "headless_client_may_end_after_each_invocation": headless_api,
            "durable_runtime_survives_client_process": True,
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
    invocation_present = "invocation" in value
    invocation = value.get("invocation")
    require(
        not invocation_present or isinstance(invocation, dict),
        "RUNTIME_CONTINUITY_INVOCATION_INVALID",
        "The runtime continuity invocation contract must be structured when present.",
        status="FAIL",
    )
    host_entry = value.get("host_entry")
    if host_entry is not None:
        require(
            isinstance(host_entry, dict)
            and host_entry.get("accepted_pointer_generation_must_match") is True
            and host_entry.get("worktree_and_four_authority_heads_must_match")
            is True
            and host_entry.get("candidate_overlay_requires_exact_authorization")
            is True
            and host_entry.get("project_truth_promotion_allowed") is False
            and host_entry.get("learning_promotion_allowed") is False
            and host_entry.get("canon_acceptance_allowed") is False
            and host_entry.get("hil_replay_allowed") is False,
            "RUNTIME_CONTINUITY_HOST_ENTRY_BOUNDARY_INVALID",
            "The runtime host-entry boundary is incomplete or authority-crossing.",
            status="FAIL",
        )
        server_filesystem = value.get("storage", {}).get("server_filesystem")
        remote_required = server_filesystem == "EPHEMERAL_OR_UNAVAILABLE"
        require(
            host_entry.get("required_before_governed_work") is remote_required
            and host_entry.get("transactional_exact_once_required")
            is remote_required
            and host_entry.get("state")
            == (
                "CONSUMED_EXACT_ONCE_FROM_TRANSACTIONAL_CONNECTOR"
                if remote_required
                else "NOT_REQUIRED_DURABLE_LOCAL_AUTHORITY"
            )
            and (
                bool(str(host_entry.get("consumption_receipt_sha256") or ""))
                if remote_required
                else host_entry.get("consumption_receipt_sha256") is None
            ),
            "RUNTIME_CONTINUITY_HOST_ENTRY_ROUTE_MISMATCH",
            "The host-entry requirement does not match measured storage durability.",
            status="MISMATCH",
        )
    base_boundary_valid = (
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
        and value.get("hil_approval_inferred") is False
    )
    require(
        base_boundary_valid,
        "RUNTIME_CONTINUITY_BOUNDARY_INVALID",
        "Runtime continuity must remain reference-only, MCP-governed, and pointer-neutral.",
        status="FAIL",
    )
    # The receipt hash is verified before compatibility is considered. Receipts
    # created before invocation-profile sealing remain immutable accepted
    # evidence when their original reference-only boundary is intact. They are
    # returned byte-for-byte; the next boot/resume emits a fresh current receipt.
    if not invocation_present:
        require(
            value.get("entry_exit_slip", {}).get("continuity_reference_required")
            is True
            and value.get("entry_exit_slip", {}).get("pointer_movement") is False
            and value.get("entry_exit_slip", {}).get("hil_approval_inferred")
            is False
            and value.get("mcp_access", {}).get("env_uop_governs_writes") is True
            and value.get("mcp_access", {}).get("one_writer_required") is True
            and value.get("candidate_created") is False
            and value.get("lifecycle_effect") == "NONE",
            "RUNTIME_CONTINUITY_LEGACY_BOUNDARY_INVALID",
            "A pre-invocation runtime receipt must preserve the complete legacy reference-only boundary.",
            status="FAIL",
        )
        return value

    invocation = cast(dict[str, Any], invocation)
    require(
        invocation.get("api_billing_affects_storage_or_tunnel") is False
        and invocation.get("account_tier_affects_storage_or_tunnel") is False
        and invocation.get("six_way_hil_preserved") is True,
        "RUNTIME_CONTINUITY_INVOCATION_BOUNDARY_INVALID",
        "Runtime invocation continuity must not alter storage, tunnel, or six-way HIL law.",
        status="FAIL",
    )
    runtime_classifier = value.get("runtime_classifier")
    runtime_namespace = value.get("runtime_namespace")
    if runtime_classifier is not None or runtime_namespace is not None:
        require(
            isinstance(runtime_classifier, dict)
            and isinstance(runtime_namespace, dict),
            "RUNTIME_CONTINUITY_CLASSIFIER_NAMESPACE_REQUIRED",
            "Current runtime continuity requires both classifier and namespace receipts.",
            status="FAIL",
        )
        validate_runtime_host_classifier(cast(dict[str, Any], runtime_classifier))
        validate_runtime_namespace(cast(dict[str, Any], runtime_namespace))
        runtime_classifier = cast(dict[str, Any], runtime_classifier)
        runtime_namespace = cast(dict[str, Any], runtime_namespace)
        require(
            runtime_namespace.get("project_id")
            == value.get("project", {}).get("project_id")
            and bool(runtime_namespace.get("governed_session_id"))
            and runtime_namespace.get("active_surface") == "CODEX"
            and invocation.get("active_surface") == "CODEX"
            and invocation.get("container_channel")
            == runtime_classifier.get("container_channel")
            and invocation.get("workspace_class")
            == runtime_namespace.get("workspace_class")
            == runtime_classifier.get("workspace_class"),
            "RUNTIME_CONTINUITY_CLASSIFIER_NAMESPACE_MISMATCH",
            "Runtime classifier, namespace, and continuity identities diverge.",
            status="MISMATCH",
        )
    if invocation.get("headless_api") is True:
        require(
            invocation.get("tunnel_requirement") == "NOT_REQUIRED_FOR_API_LAYER"
            and invocation.get("tunnel_required_for_api_layer") is False
            and invocation.get("flash_verification")
            == "VERIFY_LOCKED_ENV_UOP_AT_EVERY_API_INVOCATION_ENTRY"
            and invocation.get("headless_client_may_end_after_each_invocation")
            is True,
            "HEADLESS_API_INVOCATION_CONTINUITY_INVALID",
            "Headless API continuity must reverify Flash without a tunnel each invocation.",
            status="FAIL",
        )
    # Receipts created before portable project-route sealing remain immutable
    # accepted evidence. New receipts carry and validate this exact route block.
    project = value.get("project")
    if project is not None:
        project_id = str(project.get("project_id") or "")
        storage_route = value.get("storage", {}).get("project_route", {})
        legacy_contained = storage_route.get("contained_beneath_store_root") is True
        external_project_authority = bool(
            storage_route.get("project_authority_mode")
            == "EXPLICIT_USER_PROJECT_ROOT"
            and storage_route.get("governed_user_project_authority") is True
            and storage_route.get("runtime_separated") is True
            and storage_route.get("contained_beneath_store_root") is False
        )
        require(
            bool(project_id)
            and project.get("relative_project_route") == f"projects/{project_id}"
            and project.get("cross_project_fallback_allowed") is False
            and storage_route.get("project_id") == project_id
            and (legacy_contained or external_project_authority),
            "RUNTIME_CONTINUITY_PROJECT_ROUTE_INVALID",
            "The sealed runtime continuity project route is invalid.",
            status="FAIL",
        )
    return value

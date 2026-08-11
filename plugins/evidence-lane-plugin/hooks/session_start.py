"""SessionStart context with one sealed Codex-to-State-Travel host binding."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_FLASH_PROMPT_SHA256 = (
    "2167BBABE80656C24B18544096E725E874D4FB46066B8F4F8364A3BF14A827DB"
)
_ENGINE_VERSION_RE = re.compile(
    r'^ENGINE_VERSION\s*=\s*"(?P<version>[^"]+)"',
    flags=re.MULTILINE,
)
_EXPECTED_HOST_STORAGE_TUNNEL_MATRIX = {
    "routing_axes_independent": True,
    "account_tier_affects_routing": False,
    "api_billing_affects_routing": False,
    "headless_api": {
        "local_or_persistent_pv_storage": "LOCAL_SQLITE_WHEN_DURABLE",
        "ephemeral_pv_storage": (
            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
        ),
        "tunnel_requirement": "NOT_REQUIRED_FOR_API_LAYER",
        "flash_frequency": "EVERY_INVOCATION_ENTRY",
    },
    "interactive_codex_app_local_or_persistent": {
        "pv_storage": "DURABLE_LOCAL_SQLITE",
        "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        "tunnel_key_retention": "HOST_MANAGED_PERSISTENT_PROFILE",
    },
    "interactive_codex_app_ephemeral_vm": {
        "pv_storage": "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR",
        "tunnel_setup_frequency": "ONCE_PER_EPHEMERAL_VM_INSTANCE",
        "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
        "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
    },
}


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _store_root() -> Path:
    return Path(
        os.environ.get("EVIDENCE_LANE_DATA_ROOT")
        or os.environ.get("PLUGIN_DATA")
        or Path.home() / "EvidenceLanePV"
    ).resolve()


def _load_turn_control():
    source_root = _plugin_root() / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.codex_turn_control import (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        persistent_change_system_notice,
        policy_state,
        session_start_control,
    )

    return (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        persistent_change_system_notice,
        policy_state,
        session_start_control,
    )


def _turn_control_context(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    root = _store_root()
    (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        _,
        policy_state,
        session_start_control,
    ) = _load_turn_control()
    raw_policy = policy_state(
        root,
        host_session_id=str(payload.get("session_id") or "").strip(),
        cwd=str(payload.get("cwd") or ""),
        transcript_path=str(
            payload.get("transcript_path")
            or payload.get("agent_transcript_path")
            or ""
        ),
    )
    try:
        normalized_payload, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="SessionStart",
            allow_alias_claim=True,
        )
    except TurnControlError as exc:
        return (
            gap_receipt(
                root,
                host_payload=payload,
                error=exc,
                policy=raw_policy,
            ),
            not bool(raw_policy.get("strict_required")),
        )
    policy = policy_state(
        root,
        host_session_id=str(normalized_payload.get("session_id") or "").strip(),
        cwd=str(normalized_payload.get("cwd") or ""),
        transcript_path=str(
            normalized_payload.get("transcript_path")
            or normalized_payload.get("agent_transcript_path")
            or ""
        ),
    )
    if not policy.get("governed_session"):
        return (
            {
                "state": "NO_BOUND_EVIDENCE_LANE_SESSION",
                "strict_required": False,
                "scrollback_authority": False,
                "transcript_authority": False,
            },
            True,
        )
    if not policy.get("strict_required"):
        return (
            {
                "state": "TURN_CONTROL_NOT_REQUIRED_YET",
                "reason": "SEALED_MODE_PLUS_PLAN_NOT_ACTIVE",
                "project_id": policy.get("project_id"),
                "evidence_session_id": policy.get("evidence_session_id"),
                "scrollback_authority": False,
                "transcript_authority": False,
            },
            True,
        )
    try:
        receipt = session_start_control(root, host_payload=normalized_payload)
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return receipt, True
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized_payload,
            error=exc,
            policy=policy,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return receipt, False


def _plugin_version_context() -> dict[str, object]:
    root = _plugin_root()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    constants_path = root / "src" / "evidence_lane_plugin" / "constants.py"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        match = _ENGINE_VERSION_RE.search(constants_path.read_text(encoding="utf-8"))
        runtime_version = match.group("version") if match else None
        manifest_version = str(manifest.get("version", ""))
        manifest_base = manifest_version.split("+", 1)[0]
        state = (
            "FRESH"
            if runtime_version and manifest_base == runtime_version
            else "MISMATCH"
        )
        release_contract_path = root / "scripts" / "codex-release-channel.json"
        release_contract = json.loads(
            release_contract_path.read_text(encoding="utf-8")
        )
        remote_git_policy = dict(release_contract.get("remote_git_policy") or {})
        stable = dict(release_contract.get("stable") or {})
        promotion = dict(release_contract.get("promotion_gate") or {})
        policy_valid = (
            release_contract.get("schema")
            == "evidence-lane.codex-release-channel.v2"
            and stable.get("release") == runtime_version
            and stable.get("native_server_identity") == "evidence-lane"
            and (
                stable.get("native_tool_count"),
                stable.get("native_read_tool_count"),
                stable.get("native_write_tool_count"),
                stable.get("skill_count"),
            )
            == (62, 21, 41, 15)
            and stable.get("codex_apps_allowed") is False
            and stable.get("generated_namespace_allowed") is False
            and stable.get("direct_stdio_fallback_allowed") is False
            and stable.get("google_drive_bundled") is False
            and release_contract.get("host_storage_tunnel_matrix")
            == _EXPECTED_HOST_STORAGE_TUNNEL_MATRIX
            and remote_git_policy.get("effective_release") == runtime_version
            and remote_git_policy.get("per_push_confirmation_token_required")
            is False
            and remote_git_policy.get("main_push_allowed") is False
            and remote_git_policy.get("merge_allowed") is False
            and remote_git_policy.get("pull_request_acceptance_allowed") is False
            and promotion.get("mode") == "CODE"
            and promotion.get("ci_cd_law") == "CONTROLLED_REQUIRED"
            and promotion.get("explicit_six_way_hil_required") is True
        )
        return {
            "plugin_id": manifest.get("name"),
            "plugin_manifest_version": manifest_version,
            "runtime_engine_version": runtime_version,
            "version_state": state,
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes())
            .hexdigest()
            .upper(),
            "runtime_constants_sha256": hashlib.sha256(constants_path.read_bytes())
            .hexdigest()
            .upper(),
            "release_policy_state": "FRESH" if policy_valid else "MISMATCH",
            "release_policy_sha256": hashlib.sha256(
                release_contract_path.read_bytes()
            )
            .hexdigest()
            .upper(),
            "effective_remote_git_policy": remote_git_policy,
            "host_storage_tunnel_matrix": release_contract.get(
                "host_storage_tunnel_matrix"
            ),
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "plugin_id": "evidence-lane-plugin",
            "version_state": "UNVERIFIED",
            "error_type": type(exc).__name__,
        }


def _runtime_activation() -> dict[str, object]:
    path = _store_root() / "installation" / "runtime_activation.json"
    if not path.is_file():
        return {
            "schema": "evidence-lane.runtime-activation.v1",
            "state": "DETACHED",
            "active_sessions": [],
            "flash_context_attached": False,
            "prompt_capture_active": False,
            "visible_response_capture_active": False,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "schema": "evidence-lane.runtime-activation.v1",
            "state": "DETACHED",
            "active_sessions": [],
            "flash_context_attached": False,
            "prompt_capture_active": False,
            "visible_response_capture_active": False,
            "receipt_state": "INVALID_FAIL_CLOSED",
            "error_type": type(exc).__name__,
        }
    sessions = payload.get("active_sessions")
    valid = (
        payload.get("schema") == "evidence-lane.runtime-activation.v1"
        and payload.get("plugin_id") == "evidence-lane-plugin"
        and payload.get("state") == "ACTIVE"
        and isinstance(sessions, list)
        and bool(sessions)
        and payload.get("flash_context_attached") is True
        and payload.get("prompt_capture_active") is True
        and payload.get("visible_response_capture_active") is True
    )
    if not valid:
        return {
            "schema": "evidence-lane.runtime-activation.v1",
            "state": "DETACHED",
            "active_sessions": [],
            "flash_context_attached": False,
            "prompt_capture_active": False,
            "visible_response_capture_active": False,
            "receipt_state": "DETACHED_OR_INVALID_FAIL_CLOSED",
        }
    return payload


def _host_activation_context(project_id: str | None) -> dict[str, object]:
    """Return secret-free first-use tunnel guidance for the exact bound project."""

    if not project_id:
        return {
            "state": "EXACT_PROJECT_BINDING_REQUIRED",
            "tunnel_mutated": False,
            "secret_read": False,
            "cross_project_disclosure": False,
        }
    project_root = _store_root() / "projects" / project_id
    active_path = project_root / "active_session.json"
    if not active_path.is_file():
        return {
            "state": "NO_ACTIVE_PROJECT_SESSION",
            "project_id": project_id,
            "tunnel_mutated": False,
            "secret_read": False,
            "cross_project_disclosure": False,
        }
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        session_path = project_root / "sessions" / f"{active['session_id']}.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        route = dict(session.get("metadata", {}).get("persistence_route") or {})
        interaction = str(route.get("interaction_profile") or "").strip()
        requirement = str(route.get("tunnel_requirement") or "").strip()
        if interaction in {"HEADLESS_API", "DIRECT_CLI_API"}:
            return {
                "state": "TUNNEL_NOT_REQUIRED_FOR_API_LAYER",
                "project_id": project_id,
                "interaction_profile": interaction,
                "primary_runtime_authority": route.get(
                    "primary_runtime_authority"
                ),
                "local_pv_storage_allowed_when_durable": True,
                "tunnel_mutated": False,
                "secret_read": False,
                "account_tier_affects_routing": False,
                "api_billing_affects_routing": False,
                "cross_project_disclosure": False,
            }
        if requirement != "REQUIRED_FOR_INTERACTIVE_CODEX_APP_ENVIRONMENT":
            return {
                "state": "TUNNEL_NOT_PART_OF_THIS_SURFACE_ROUTE",
                "project_id": project_id,
                "interaction_profile": interaction or "UNSPECIFIED",
                "tunnel_mutated": False,
                "secret_read": False,
                "cross_project_disclosure": False,
            }
        version = str(_plugin_version_context().get("runtime_engine_version") or "")
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
        if match is None:
            raise ValueError("runtime engine version is not exact semver")
        token = f"v{match.group(1)}{match.group(2)}{match.group(3)}"
        expected_ephemeral = (
            str(route.get("vm_lifetime") or "") == "EPHEMERAL_VM"
        )
        configured_runtime_root = str(
            os.environ.get("EVIDENCE_LANE_TUNNEL_RUNTIME_ROOT") or ""
        ).strip()
        runtime_root = (
            Path(configured_runtime_root).resolve()
            if configured_runtime_root
            else (
                Path.home() / "EvidenceLanePV" / f"tunnel-runtime-{token}"
                if expected_ephemeral
                else _store_root() / f"tunnel-runtime-{token}"
            )
        )
        marker_path = runtime_root / "evidence-lane-tunnel-installation.json"
        installer = (
            _plugin_root()
            / "scripts"
            / "windows_tunnel"
            / "Install-EvidenceLaneTunnel.ps1"
        )
        host_lifetime = str(route.get("vm_lifetime") or "LOCAL_OR_PERSISTENT")
        expected_lifetime = (
            "EPHEMERAL" if host_lifetime == "EPHEMERAL_VM" else "PERSISTENT"
        )
        vm_instance_id = str(session.get("sandbox_id") or "").strip()
        vm_instance_id_sha256 = (
            hashlib.sha256(vm_instance_id.encode("utf-8")).hexdigest().upper()
            if vm_instance_id
            else None
        )
        if expected_lifetime == "EPHEMERAL" and vm_instance_id_sha256 is None:
            return {
                "state": "EPHEMERAL_VM_INSTANCE_ID_REQUIRED",
                "project_id": project_id,
                "release": version,
                "interaction_profile": interaction,
                "host_lifetime": expected_lifetime,
                "runtime_root": str(runtime_root),
                "raw_vm_instance_id_stored": False,
                "tunnel_mutated": False,
                "secret_read": False,
                "cross_project_disclosure": False,
            }
        if not marker_path.is_file():
            return {
                "state": "FIRST_USE_TUNNEL_ONBOARDING_REQUIRED",
                "project_id": project_id,
                "release": version,
                "interaction_profile": interaction,
                "host_lifetime": expected_lifetime,
                "account_tier": route.get("account_tier"),
                "runtime_root": str(runtime_root),
                "vm_instance_id_sha256": vm_instance_id_sha256,
                "raw_vm_instance_id_stored": False,
                "durable_pv_storage_reused_for_tunnel_secret": False,
                "installer": str(installer),
                "next_action": (
                    "Run the installer once for this host/VM. It reuses a verified "
                    "prior current-user DPAPI envelope when available; otherwise it "
                    "prompts locally for the Runtime key without logging it."
                ),
                "tunnel_mutated": False,
                "secret_read": False,
                "cross_project_disclosure": False,
            }
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        valid = (
            marker.get("schema")
            == "evidence-lane.versioned-secure-mcp-tunnel-installation.v1"
            and marker.get("release") == version
            and marker.get("interaction_profile") == interaction
            and marker.get("host_lifetime") == expected_lifetime
            and marker.get("runtime_key_plaintext_written") is False
            and marker.get("vm_instance_id_sha256")
            == (
                vm_instance_id_sha256
                if expected_lifetime == "EPHEMERAL"
                else "NOT_APPLICABLE"
            )
        )
        return {
            "state": (
                "TUNNEL_INSTALLATION_PRESENT_HOST_MANAGED"
                if valid
                else "TUNNEL_INSTALLATION_MISMATCH_REONBOARD_REQUIRED"
            ),
            "project_id": project_id,
            "release": version,
            "interaction_profile": interaction,
            "host_lifetime": expected_lifetime,
            "runtime_root": str(runtime_root),
            "vm_instance_id_sha256": vm_instance_id_sha256,
            "raw_vm_instance_id_stored": False,
            "durable_pv_storage_reused_for_tunnel_secret": False,
            "marker_sha256": hashlib.sha256(marker_path.read_bytes())
            .hexdigest()
            .upper(),
            "health_claimed": False,
            "tunnel_mutated": False,
            "secret_read": False,
            "cross_project_disclosure": False,
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "state": "HOST_ACTIVATION_RECEIPT_INVALID_FAIL_CLOSED",
            "project_id": project_id,
            "error_type": type(exc).__name__,
            "tunnel_mutated": False,
            "secret_read": False,
            "cross_project_disclosure": False,
        }


def _flash_context() -> str:
    prompt_path = (
        _plugin_root()
        / "src"
        / "evidence_lane_plugin"
        / "session_flash"
        / "env15"
        / "UNIVERSAL_FLASH_PROMPT.md"
    )
    if not prompt_path.is_file():
        return (
            "SESSION_FLASH_AUTHORITY_UNAVAILABLE: the universal flash prompt is "
            "missing. Do not start Evidence Lane lifecycle work."
        )
    prompt_bytes = prompt_path.read_bytes()
    actual_hash = hashlib.sha256(prompt_bytes).hexdigest().upper()
    if actual_hash != _FLASH_PROMPT_SHA256:
        return (
            "SESSION_FLASH_AUTHORITY_MISMATCH: the universal flash prompt failed "
            "SHA-256 verification. Do not start Evidence Lane lifecycle work."
        )
    return (
        prompt_bytes.decode("utf-8")
        + "\n\nFLASH_PROMPT_SHA256="
        + actual_hash
        + "\nFLASH_CONTEXT_INSIDE_PV=false"
    )


def _persistent_envelope(project_id: str | None) -> dict[str, object]:
    """Read a bounded durable status hint; lifecycle tools remain authoritative."""
    root = _store_root()
    projects_root = root / "projects"
    if not projects_root.is_dir():
        return {
            "state": "NO_PERSISTENT_PROJECT_STORE",
            "store_configured": False,
            "store_path": str(root),
            "persistence_class": "USER_OWNED_LOCAL_STORE",
            "projects": [],
        }
    if not project_id:
        return {
            "state": "EXACT_HOST_SESSION_PROJECT_BINDING_REQUIRED",
            "store_configured": True,
            "store_path": str(root),
            "persistence_class": "USER_OWNED_LOCAL_STORE",
            "project_count_returned": 0,
            "projects": [],
            "cross_project_disclosure": False,
        }
    projects: list[dict[str, object]] = []
    warnings: list[dict[str, str]] = []
    for project_root in [projects_root / project_id]:
        if not project_root.is_dir():
            continue
        try:
            pointer = json.loads(
                (project_root / "active_pointer.json").read_text(encoding="utf-8")
            )
            accepted = sorted(
                (
                    path.name
                    for path in (project_root / "accepted").glob("PV*")
                    if path.is_dir() and path.name[2:].isdigit()
                ),
                key=lambda value: int(value[2:]),
            )
            active_session: dict[str, object] | None = None
            active_path = project_root / "active_session.json"
            if active_path.is_file():
                active = json.loads(active_path.read_text(encoding="utf-8"))
                session_path = (
                    project_root / "sessions" / f"{active.get('session_id', '')}.json"
                )
                if session_path.is_file():
                    session = json.loads(session_path.read_text(encoding="utf-8"))
                    if not session.get("metadata", {}).get("closed_at"):
                        active_session = {
                            "session_id": session.get("session_id"),
                            "state": session.get("state"),
                            "entry_pv": session.get("metadata", {}).get("entry_pv"),
                            "candidate_id": session.get("candidate_id"),
                            "pending_hil": str(session.get("state", "")).endswith(
                                "_CANDIDATE"
                            ),
                            "entry_manifest_sha256": session.get("metadata", {}).get(
                                "entry_manifest_sha256"
                            ),
                            "entry_package_sha256": session.get("metadata", {}).get(
                                "entry_package_sha256"
                            ),
                        }
            universal_statuses = (
                "QUEUED",
                "ACTIVE",
                "DONE",
                "ACCEPTED",
                "REJECTED",
                "DROPPED",
                "SUPERSEDED",
                "FAILED",
                "ROLLED_BACK",
            )
            backlog_counts: dict[str, object] = {
                "queued": 0,
                "active": 0,
                "done_pending_hil": 0,
                "terminal": 0,
                "total": 0,
                "universal": {status: 0 for status in universal_statuses},
            }
            backlog_path = project_root / "task_backlog.json"
            if backlog_path.is_file():
                backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
                tasks = backlog.get("tasks", [])
                universal = {
                    status: sum(1 for task in tasks if task.get("status") == status)
                    for status in universal_statuses
                }
                backlog_counts = {
                    "queued": universal["QUEUED"],
                    "active": universal["ACTIVE"],
                    "done_pending_hil": universal["DONE"],
                    "terminal": sum(
                        universal[status]
                        for status in (
                            "ACCEPTED",
                            "REJECTED",
                            "DROPPED",
                            "SUPERSEDED",
                            "FAILED",
                            "ROLLED_BACK",
                        )
                    ),
                    "total": len(tasks),
                    "universal": universal,
                }
            active_lanes: list[str] = []
            accepted_pv = pointer.get("accepted_pv")
            if accepted_pv:
                routes_path = (
                    project_root
                    / "accepted"
                    / str(accepted_pv)
                    / "lanes"
                    / "routes.json"
                )
                if routes_path.is_file():
                    routes = json.loads(routes_path.read_text(encoding="utf-8"))
                    active_lanes = sorted(set(routes.get("routes", {}).values()))
            projects.append(
                {
                    "project_id": project_root.name,
                    "accepted_pv": accepted_pv,
                    "pointer_generation": pointer.get("generation"),
                    "accepted_manifest_sha256": pointer.get("accepted_manifest_sha256"),
                    "accepted_history": accepted,
                    "highest_accepted_ordinal": max(
                        (int(value[2:]) for value in accepted), default=0
                    ),
                    "next_candidate_pv": (
                        f"PV{max((int(value[2:]) for value in accepted), default=0) + 1}"
                    ),
                    "ancestry_depth": max(len(accepted) - 1, 0),
                    "active_lanes": active_lanes,
                    "live_freshness": "VERIFY_WITH_PV_STATUS",
                    "task_backlog": backlog_counts,
                    "active_session": active_session,
                }
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            warnings.append(
                {
                    "project_id": project_root.name,
                    "error_type": type(exc).__name__,
                }
            )
    return {
        "state": "PERSISTENT_STATE_HINT_ONLY_CALL_PV_STATUS_TO_VERIFY",
        "store_configured": True,
        "store_path": str(root),
        "persistence_class": "USER_OWNED_LOCAL_STORE",
        "project_count_returned": len(projects),
        "projects": projects,
        "warnings": warnings,
        "cross_project_disclosure": False,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    source = str(payload.get("source", "startup"))
    host_session_id = str(payload.get("session_id", "")).strip()
    activation = _runtime_activation()
    try:
        turn_control, turn_control_continue = _turn_control_context(payload)
    except Exception as exc:  # noqa: BLE001 - startup must expose missing control
        turn_control = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "TURN_CONTROL_MODULE_OR_POLICY_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "fail_closed": activation.get("state") == "ACTIVE",
            "source_mutation_authorized": False,
            "private_reasoning_stored": False,
        }
        turn_control_continue = activation.get("state") != "ACTIVE"
    flash_context = (
        _flash_context()
        if activation.get("state") == "ACTIVE"
        else (
            "EVIDENCE_LANE_RUNTIME=DETACHED\n"
            "The plugin remains installed, but ENV/UOP Flash context and visible "
            "prompt/response capture are detached. Run /evi-boot to atomically "
            "verify Flash and activate or resume a governed session. Do not infer "
            "HIL approval, Fuse, pointer movement, or State Travel."
        )
    )
    exact_project_id = str(turn_control.get("project_id") or "").strip() or None
    host_activation = _host_activation_context(exact_project_id)
    persistent_change_display = turn_control.get("persistent_change_display")
    warm_attach_receipt = turn_control.get("warm_attach_receipt")
    context = (
        flash_context
        + "\n\nPLUGIN_RUNTIME_ENVELOPE="
        + json.dumps(
            _plugin_version_context(),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nUNIVERSAL_LIFECYCLE_ADDENDUM="
        + "APPROVE is the only candidate promotion. ROLLBACK may move only "
        + "the accepted pointer among immutable accepted PVs; bare rollback "
        + "uses this session's recorded entry PV. It never accepts a candidate "
        + "or rewrites source. PV1 is the only normal full build; PV2+ uses "
        + "incremental Refresh across the single eighteen-lane registry. Task "
        + "completion automatically confirms source, Refreshes, and seals the "
        + "exit candidate; Exit and Refresh are not user commands."
        + "\n\nExpose exactly six public controls after /evi: Boot, Rollback, "
        + "Build, Refresh, Mode, and Source Intake. A prepared accepted-PV "
        + "handoff makes /evi-state-travel eligible but must not auto-display, "
        + "invoke, or consume it. Route to it only after an explicit user request "
        + "or genuine host-context exhaustion; it must bind a fresh host session, "
        + "atomically verify Boot plus locked ENV/UOP Flash, pointer, and seals, "
        + "then wait for the user's next command. Otherwise /evi starts or resumes with "
        + "/evi-boot as the first normal action and atomically verifies "
        + "Boot/Flash before displaying generalized /evi-source-intake, the "
        + "separate /evi-mode sidecar, Build, bounded work, automatic exit-Refresh, "
        + "six-way HIL, exact-APPROVE Fuse, Rollback, and explicit "
        + "/evi-exit-boot. A booted session persists until that explicit command. "
        + "Before stopping at HIL or State Travel, visibly render the engine's "
        + "suggested_next_prompt. The composer is host-owned; do not claim the "
        + "MCP wrote it and do not auto-submit it. The nonblocking Stop hook "
        + "may index the secret-redacted visible response but must never "
        + "continue past a human gate. "
        + "HOST_SESSION_ID="
        + (host_session_id or "UNAVAILABLE")
        + ". Session source: "
        + source
        + ".\nPERSISTENT_STATE_ENVELOPE="
        + json.dumps(
            _persistent_envelope(exact_project_id),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nRUNTIME_ACTIVATION_ENVELOPE="
        + json.dumps(
            activation,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nHOST_ACTIVATION_ENVELOPE="
        + json.dumps(
            host_activation,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nCODEX_TURN_CONTROL_ENVELOPE="
        + json.dumps(
            turn_control,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nPERSISTENT_CHANGE_DISPLAY="
        + json.dumps(
            persistent_change_display
            if isinstance(persistent_change_display, dict)
            else {
                "state": "UNAVAILABLE_UNTIL_EXACT_STRICT_PROJECT_TASK_BINDING",
                "cross_project_disclosure": False,
                "composer_mutated": False,
                "auto_submit": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nCODEX_WARM_ATTACH_RECEIPT="
        + json.dumps(
            warm_attach_receipt
            if isinstance(warm_attach_receipt, dict)
            else {
                "state": "UNAVAILABLE_UNTIL_EXACT_STRICT_PROJECT_TASK_BINDING",
                "tunnel_provisioning_wait_ns": 0,
                "tunnel_state_queried": False,
                "codex_tunnel_lifecycle_proof_allowed": False,
                "cross_project_disclosure": False,
                "lifecycle_mutated": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nThis envelope is a read-only startup hint. Call pv_status before "
        "relying on it; Never infer HIL approval."
    )
    result: dict[str, Any] = {
        "continue": turn_control_continue,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        },
    }
    if not turn_control_continue:
        result["stopReason"] = (
            "Governed Evidence Lane SessionStart binding failed closed before source mutation."
        )
    if isinstance(persistent_change_display, dict):
        _, _, _, persistent_change_system_notice, _, _ = _load_turn_control()
        notice = persistent_change_system_notice(
            persistent_change_display,
            phase="SESSION_START",
            turn_receipt=turn_control,
        )
        result["systemMessage"] = (
            "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY="
            + json.dumps(
                notice,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

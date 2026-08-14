from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from evidence_lane_plugin.runtime_activation import RuntimeActivation

from .conftest import boot_local


def _run_session_start(
    root: Path,
    store: Path,
    host_session_id: str,
    *,
    tunnel_runtime_root: Path | None = None,
    slot_role: str | None = None,
) -> str:
    hook = (
        root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "session_start.py"
    )
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(store)
    if tunnel_runtime_root is not None:
        environment["EVIDENCE_LANE_TUNNEL_RUNTIME_ROOT"] = str(
            tunnel_runtime_root
        )
    if slot_role is not None:
        environment["EVIDENCE_LANE_CODEX_SLOT_ROLE"] = slot_role
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {"source": "startup", "session_id": host_session_id}
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    return json.loads(completed.stdout)["hookSpecificOutput"]["additionalContext"]


def _context_envelope(context: str, label: str) -> dict[str, object]:
    prefix = f"{label}="
    line = next(line for line in context.splitlines() if line.startswith(prefix))
    return json.loads(line.removeprefix(prefix))


def _sealed_json_sha256(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()


def _write_active_runtime_and_installation(
    root: Path,
    *,
    host_dispatch_events: list[str],
    package_events: list[str] | None,
) -> RuntimeActivation:
    runtime = RuntimeActivation(root)
    runtime.path.parent.mkdir(parents=True, exist_ok=True)
    runtime.path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.runtime-activation.v1",
                "plugin_id": "evidence-lane-plugin",
                "state": "ACTIVE",
                "generation": 1,
                "active_sessions": [
                    {"project_id": "project-a", "session_id": "session-a"}
                ],
                "flash_context_attached": True,
                "prompt_capture_active": True,
                "visible_response_capture_active": True,
                "immutable_store_preserved": True,
                "plugin_installation_preserved": True,
                "hil_approval_inferred": False,
            }
        ),
        encoding="utf-8",
    )
    selector = "evidence-lane-plugin@evidence-lane-github"
    hook_trust: dict[str, object] = {
        "schema": "evidence-lane.codex-hook-trust.v1",
        "status": "PASS",
        "plugin_selector": selector,
        "hook_count": len(host_dispatch_events),
        "registered_events": host_dispatch_events,
        "records": [
            {
                "event_name": event,
                "hook_key": f"{selector}:hooks/hooks.json:{event}:0:0",
                "current_hash": f"sha256:{index:064x}",
                "enabled": True,
                "trust_status": "trusted",
            }
            for index, event in enumerate(host_dispatch_events, start=1)
        ],
        "before_trust_statuses": ["untrusted"],
        "after_trust_statuses": ["trusted"],
    }
    hook_trust["receipt_sha256"] = _sealed_json_sha256(hook_trust)
    installation: dict[str, object] = {
        "schema": "evidence-lane.codex-stable-installation.v2",
        "status": "PASS",
        "activation": {
            "state": "INSTALLED_RESTART_REQUIRED",
            "plugin_add": {"pluginId": selector},
            "hook_trust": hook_trust,
        },
    }
    if package_events is not None:
        surface: dict[str, object] = {
            "schema": "evidence-lane.codex-installed-surface-change-display.v2",
            "hooks": {
                "count": len(package_events),
                "registered_event_count": len(package_events),
                "registered_events": package_events,
            },
        }
        surface["change_display_sha256"] = _sealed_json_sha256(surface)
        installation["surface_change_display"] = surface
    installation["receipt_sha256"] = _sealed_json_sha256(installation)
    current_installation = (
        root
        / "installations"
        / "codex-v200"
        / "CURRENT_INSTALLATION.json"
    )
    current_installation.parent.mkdir(parents=True, exist_ok=True)
    current_installation.write_text(
        json.dumps(installation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return runtime


def test_runtime_hook_status_separates_host_dispatches_from_package_events(
    tmp_path: Path,
) -> None:
    host_events = [
        "postCompact",
        "postToolUse",
        "preCompact",
        "preToolUse",
        "sessionEnd",
        "sessionStart",
        "stop",
        "userPromptSubmit",
    ]
    package_events = [
        "PostCompact",
        "PostToolUse",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
        "SessionStart",
        "Stop",
        "UserPromptSubmit",
    ]
    runtime = _write_active_runtime_and_installation(
        tmp_path,
        host_dispatch_events=host_events,
        package_events=package_events,
    )

    hook_status = runtime.host_hook_status()
    assert hook_status["status"] == "TRUSTED"
    assert hook_status["hook_count"] == 8
    assert hook_status["registered_events"] == host_events
    assert hook_status["host_dispatch_hook_count"] == 8
    assert hook_status["host_dispatch_registered_events"] == host_events
    assert hook_status["host_dispatch_trust_status"] == "SEALED_CONFIG_TRUST"
    assert hook_status["package_hook_event_count"] == 8
    assert hook_status["package_registered_events"] == package_events
    assert hook_status["package_inventory_status"] == "SEALED"
    assert hook_status["installed_host_dispatch_independently_proven"] is False

    projected = runtime.status_with_host_proof()
    assert projected["supported_pre_reasoning_capture_complete"] is True
    assert projected["required_pre_reasoning_capture_complete"] is False
    assert projected["prompt_capture_active"] is False
    assert projected["host_capability_unavailable_surfaces"] == [
        "GOAL_CONTINUATION"
    ]
    goal = next(
        row
        for row in projected["required_pre_reasoning_capture_surfaces"]
        if row["surface"] == "GOAL_CONTINUATION"
    )
    assert goal["state"] == "HOST_CAPABILITY_UNAVAILABLE"
    assert goal["native_hook_event"] is None
    assert goal["per_input_invocation_proven"] is False


def test_runtime_hook_status_rejects_sealed_four_event_package_baseline(
    tmp_path: Path,
) -> None:
    host_events = [
        "postCompact",
        "postToolUse",
        "preCompact",
        "preToolUse",
        "sessionEnd",
        "sessionStart",
        "stop",
        "userPromptSubmit",
    ]
    runtime = _write_active_runtime_and_installation(
        tmp_path,
        host_dispatch_events=host_events,
        package_events=[
            "PostToolUse",
            "SessionStart",
            "Stop",
            "UserPromptSubmit",
        ],
    )

    hook_status = runtime.host_hook_status()
    assert hook_status["status"] == "MISMATCH"
    assert hook_status["trusted"] is False
    assert hook_status["host_dispatch_hook_count"] == 8
    assert hook_status["package_hook_event_count"] == 4
    assert hook_status["package_inventory_status"] == "MISMATCH"
    assert hook_status["installed_host_dispatch_independently_proven"] is False


def test_runtime_hook_status_accepts_eight_event_host_dispatch_claim(
    tmp_path: Path,
) -> None:
    runtime = _write_active_runtime_and_installation(
        tmp_path,
        host_dispatch_events=[
            "postCompact",
            "postToolUse",
            "preCompact",
            "preToolUse",
            "sessionEnd",
            "sessionStart",
            "stop",
            "userPromptSubmit",
        ],
        package_events=[
            "PostCompact",
            "PostToolUse",
            "PreCompact",
            "PreToolUse",
            "SessionEnd",
            "SessionStart",
            "Stop",
            "UserPromptSubmit",
        ],
    )

    hook_status = runtime.host_hook_status()
    assert hook_status["status"] == "TRUSTED"
    assert hook_status["trusted"] is True
    assert hook_status["host_dispatch_trust_status"] == "SEALED_CONFIG_TRUST"
    assert hook_status["installed_host_dispatch_independently_proven"] is False


def test_session_start_exposes_local_codex_native_no_tunnel_route(
    service,
) -> None:
    root = Path(__file__).resolve().parents[1]
    boot_local(service)

    context = _run_session_start(root, service.store.root, "host-session-test")
    plugin = _context_envelope(context, "PLUGIN_RUNTIME_ENVELOPE")
    activation = _context_envelope(context, "HOST_ACTIVATION_ENVELOPE")

    assert plugin["release_policy_state"] == "FRESH"
    assert plugin["effective_remote_git_policy"][
        "per_push_confirmation_token_required"
    ] is False
    assert plugin["effective_remote_git_policy"]["main_push_allowed"] is False
    assert activation["state"] == (
        "TUNNEL_NOT_REQUIRED_FOR_LOCAL_CODEX_NATIVE_LAYER"
    )
    assert activation["interaction_profile"] == "CODEX_APP_INTERACTIVE"
    assert activation["primary_runtime_authority"] == "LOCAL_DURABLE_SQLITE"
    assert activation["active_surface"] == "CODEX"
    assert activation["tunnel_requirement"] == (
        "NOT_REQUIRED_FOR_LOCAL_CODEX_NATIVE_LAYER"
    )
    assert activation["tunnel_mutated"] is False
    assert activation["secret_read"] is False
    assert activation["cross_project_disclosure"] is False


def test_session_start_excludes_tunnel_from_headless_api_layer(service) -> None:
    root = Path(__file__).resolve().parents[1]
    service.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="codex-api",
        sandbox_id="sandbox-local-api",
        ephemeral=False,
        runtime_context={
            "interaction_profile": "HEADLESS_API",
            "account_tier": "API",
        },
        host_session_id="host-session-api",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )

    context = _run_session_start(root, service.store.root, "host-session-api")
    activation = _context_envelope(context, "HOST_ACTIVATION_ENVELOPE")

    assert activation == {
        "account_tier_affects_routing": False,
        "api_billing_affects_routing": False,
        "cross_project_disclosure": False,
        "interaction_profile": "HEADLESS_API",
        "local_pv_storage_allowed_when_durable": True,
        "primary_runtime_authority": "LOCAL_DURABLE_SQLITE",
        "project_id": "book-faires",
        "secret_read": False,
        "state": "TUNNEL_NOT_REQUIRED_FOR_API_LAYER",
        "tunnel_mutated": False,
    }


def test_session_start_does_not_route_local_fallback_hook_to_a_tunnel(service) -> None:
    root = Path(__file__).resolve().parents[1]
    boot_local(service)
    context = _run_session_start(
        root,
        service.store.root,
        "host-session-test",
        slot_role="fallback",
    )
    activation = _context_envelope(context, "HOST_ACTIVATION_ENVELOPE")
    assert activation["state"] == (
        "TUNNEL_NOT_REQUIRED_FOR_LOCAL_CODEX_NATIVE_LAYER"
    )
    assert "slot_role" not in activation
    assert "runtime_root" not in activation


def test_session_start_ignores_local_tunnel_marker_without_claiming_health(
    service,
) -> None:
    root = Path(__file__).resolve().parents[1]
    boot_local(service)
    runtime_root = service.store.root / "tunnel-runtime-v210-stable-build"
    runtime_root.mkdir(parents=True)
    marker = runtime_root / "evidence-lane-tunnel-installation.json"
    marker.write_text(
        json.dumps(
            {
                "schema": (
                    "evidence-lane.versioned-secure-mcp-tunnel-installation.v1"
                ),
                "release": "2.1.0",
                "slot_role": "stable-build",
                "legacy_version_manager_authoritative": False,
                "interaction_profile": "CODEX_APP_INTERACTIVE",
                "host_lifetime": "PERSISTENT",
                "vm_instance_id_sha256": "NOT_APPLICABLE",
                "runtime_key_plaintext_written": False,
            }
        ),
        encoding="utf-8",
    )

    context = _run_session_start(root, service.store.root, "host-session-test")
    activation = _context_envelope(context, "HOST_ACTIVATION_ENVELOPE")

    assert activation["state"] == (
        "TUNNEL_NOT_REQUIRED_FOR_LOCAL_CODEX_NATIVE_LAYER"
    )
    assert "health_claimed" not in activation
    assert "marker_sha256" not in activation
    assert activation["tunnel_mutated"] is False
    assert activation["secret_read"] is False


def test_ephemeral_interactive_tunnel_is_bound_to_one_vm_lifetime(service) -> None:
    root = Path(__file__).resolve().parents[1]
    service.boot_session(
        project_id="book-faires",
        user_id="user-ephemeral",
        workspace_id="workspace-ephemeral",
        host="CODEX_VM",
        agent_id="codex-interactive-vm",
        sandbox_id="vm-instance-001",
        ephemeral=True,
        runtime_context={
            "interaction_profile": "CODEX_APP_INTERACTIVE",
            "account_tier": "BUSINESS",
        },
        host_session_id="host-session-ephemeral",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    runtime_root = service.store.root.parent / "vm-local-tunnel-runtime"

    context = _run_session_start(
        root,
        service.store.root,
        "host-session-ephemeral",
        tunnel_runtime_root=runtime_root,
    )
    activation = _context_envelope(context, "HOST_ACTIVATION_ENVELOPE")

    assert activation["state"] == "FIRST_USE_TUNNEL_ONBOARDING_REQUIRED"
    assert activation["host_lifetime"] == "EPHEMERAL"
    assert activation["runtime_root"] == str(runtime_root.resolve())
    assert len(str(activation["vm_instance_id_sha256"])) == 64
    assert activation["raw_vm_instance_id_stored"] is False
    assert activation["durable_pv_storage_reused_for_tunnel_secret"] is False
    assert activation["account_tier"] == "BUSINESS"


def test_exit_boot_detaches_flash_context_and_later_boot_reattaches(service) -> None:
    root = Path(__file__).resolve().parents[1]
    first = boot_local(service)
    session_id = first["session"]["session_id"]
    active_context = _run_session_start(
        root,
        service.store.root,
        "host-session-test",
    )
    assert "# Evidence Lane universal session flash" in active_context
    assert '"state":"ACTIVE"' in active_context

    closed = service.sessions.close(
        "book-faires",
        session_id,
        reason="USER_REQUESTED_EVI_EXIT_BOOT",
    )
    assert closed["runtime_activation"]["state"] == "DETACHED"
    detached_context = _run_session_start(
        root,
        service.store.root,
        "host-session-after-exit",
    )
    assert "EVIDENCE_LANE_RUNTIME=DETACHED" in detached_context
    assert "# Evidence Lane universal session flash" not in detached_context
    assert '"state":"DETACHED"' in detached_context

    second = boot_local(service)
    assert second["session"]["session_id"] != session_id
    assert second["session_flash"]["flash_action"] == "REUSED"
    assert second["runtime_activation"]["state"] == "ACTIVE"
    reattached_context = _run_session_start(
        root,
        service.store.root,
        "host-session-test",
    )
    assert "# Evidence Lane universal session flash" in reattached_context


def test_prompt_hook_fails_closed_when_bound_runtime_is_detached(
    service,
    source_repository: Path,
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.runtime_activation.detach(
        project_id="book-faires",
        session_id=session_id,
        reason="TEST_ONLY_DETACH_WITH_SESSION_RECORD_RETAINED",
    )
    root = Path(__file__).resolve().parents[1]
    hook = (
        root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "prompt_submit.py"
    )
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "session_id": "host-session-test",
                "turn_id": "turn-detached",
                "cwd": str(source_repository),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "This must not be captured while detached.",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    payload = json.loads(completed.stdout)
    indexed = json.loads(
        payload["hookSpecificOutput"]["additionalContext"].removeprefix(
            "EVIDENCE_LANE_PROMPT_ENTRY="
        )
    )
    assert payload["continue"] is True
    assert indexed["state"] == "TURN_CONTROL_GAP"
    assert indexed["code"] == "EVIDENCE_LANE_RUNTIME_DETACHED"
    assert indexed["fail_closed"] is False
    assert indexed["source_mutation_authorized"] is False
    assert indexed["raw_secret_stored"] is False
    assert indexed["private_reasoning_stored"] is False
    assert indexed["gap_receipt_sha256"]
    assert not (service.store.root / "prompt-index").exists()

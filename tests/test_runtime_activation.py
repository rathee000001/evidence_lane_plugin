from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .conftest import boot_local


def _run_session_start(
    root: Path,
    store: Path,
    host_session_id: str,
    *,
    tunnel_runtime_root: Path | None = None,
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


def test_session_start_exposes_interactive_first_use_tunnel_onboarding(
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
    assert activation["state"] == "FIRST_USE_TUNNEL_ONBOARDING_REQUIRED"
    assert activation["release"] == "2.0.0"
    assert activation["interaction_profile"] == "CODEX_APP_INTERACTIVE"
    assert activation["host_lifetime"] == "PERSISTENT"
    assert Path(str(activation["installer"])).is_file()
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


def test_session_start_validates_marker_without_claiming_tunnel_health(service) -> None:
    root = Path(__file__).resolve().parents[1]
    boot_local(service)
    runtime_root = service.store.root / "tunnel-runtime-v200"
    runtime_root.mkdir(parents=True)
    marker = runtime_root / "evidence-lane-tunnel-installation.json"
    marker.write_text(
        json.dumps(
            {
                "schema": (
                    "evidence-lane.versioned-secure-mcp-tunnel-installation.v1"
                ),
                "release": "2.0.0",
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

    assert activation["state"] == "TUNNEL_INSTALLATION_PRESENT_HOST_MANAGED"
    assert activation["health_claimed"] is False
    assert len(str(activation["marker_sha256"])) == 64
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

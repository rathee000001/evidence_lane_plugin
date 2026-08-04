from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from evidence_lane_plugin.auth import OAuthJWTConfig, OAuthJWTVerifier
from evidence_lane_plugin.constants import ENGINE_VERSION
from evidence_lane_plugin.mcp_server import create_mcp_server, run_server
from evidence_lane_plugin.service import EvidenceLaneService
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .conftest import build_and_approve_pv1


def test_mcp_tool_inventory_and_annotations(tmp_path: Path) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store")
    )
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "runtime_doctor",
        "session_flash_status",
        "runtime_activation_status",
        "lifecycle_transition_law",
        "lane_catalog",
        "mode_classify",
        "source_intake_classify",
        "hil_intent_classify",
        "lane_status",
        "lane_search",
        "lane_fetch",
        "lane_configure_routes",
        "pv_enroll_project",
        "git_sync_selected",
        "project_register",
        "pv_plan_tasks",
        "pv_task_backlog",
        "pv_task_transition",
        "session_boot",
        "session_resume",
        "pv_build_initial",
        "task_classify",
        "task_record_activity",
        "task_confirm_source_update",
        "pv_refresh",
        "task_complete_and_refresh",
        "hil_decide",
        "pv_fuse",
        "pv_state_travel_prepare",
        "pv_state_travel_resume",
        "pv_rollback",
        "hil_return_to_accepted",
        "pv_begin_next_turn",
        "session_close",
        "pv_status",
        "prompt_index_status",
        "search",
        "fetch",
        "pv_summary",
        "pv_query",
        "pv_diff",
        "remote_git_prepare_push",
        "remote_git_execute_push",
        "connector_plugin_register",
        "connector_plugin_drop",
        "connector_plugin_catalog",
        "connector_plugin_settings",
        "connector_plugin_route",
        "storage_connector_inspect",
        "storage_connector_select",
    }
    assert by_name["search"].annotations.readOnlyHint is True
    assert by_name["fetch"].annotations.readOnlyHint is True
    assert by_name["session_flash_status"].annotations.readOnlyHint is True
    assert by_name["runtime_activation_status"].annotations.readOnlyHint is True
    assert by_name["lane_catalog"].annotations.readOnlyHint is True
    assert by_name["mode_classify"].annotations.readOnlyHint is False
    assert by_name["hil_intent_classify"].annotations.readOnlyHint is False
    assert by_name["pv_task_backlog"].annotations.readOnlyHint is True
    assert by_name["pv_task_transition"].annotations.readOnlyHint is False
    assert by_name["pv_task_transition"].annotations.destructiveHint is False
    assert by_name["hil_decide"].annotations.destructiveHint is True
    assert by_name["pv_rollback"].annotations.destructiveHint is True
    assert by_name["pv_fuse"].annotations.destructiveHint is True
    assert by_name["pv_state_travel_prepare"].annotations.destructiveHint is False
    assert by_name["pv_state_travel_resume"].annotations.destructiveHint is False
    assert by_name["hil_return_to_accepted"].annotations.destructiveHint is True
    assert by_name["remote_git_execute_push"].annotations.openWorldHint is True
    assert (
        "preferred_plugin_id"
        in by_name["connector_plugin_route"].inputSchema["properties"]
    )
    for tool in tools:
        assert tool.description
        assert tool.inputSchema["type"] == "object"


def test_mcp_server_advertises_exact_release_and_cube_icon(tmp_path: Path) -> None:
    public_site = "https://preview.example.test"
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store"),
        public_site_url=public_site,
    )
    identity = server._mcp_server
    assert identity.version == ENGINE_VERSION == "1.2.0"
    assert str(identity.website_url) == public_site
    assert identity.icons is not None
    assert len(identity.icons) == 1
    icon = identity.icons[0]
    assert icon.src == f"{public_site}/evidence-lane-icon.png"
    assert icon.mimeType == "image/png"
    assert icon.sizes == ["2048x2048"]


def test_plugin_manifest_has_evidence_lane_identity_only() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "evidence-lane-plugin"
    manifest = json.loads(
        (plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "evidence-lane-plugin"
    assert manifest["interface"]["displayName"] == "Evidence Lane Plugin"
    assert manifest["author"]["name"] == "Praveen Rathee"
    assert manifest["repository"].endswith("/evidence_lane_plugin")
    assert manifest["apps"] == "./.app.json"
    assert manifest["interface"]["logo"] == "./assets/evidence-lane-icon.png"
    assert manifest["interface"]["composerIcon"] == (
        "./assets/evidence-lane-icon.png"
    )
    assert (plugin / "assets" / "evidence-lane-icon.png").is_file()
    assert "hooks" not in manifest
    assert isinstance(manifest["interface"]["defaultPrompt"], list)
    assert 1 <= len(manifest["interface"]["defaultPrompt"]) <= 3
    assert all(len(prompt) <= 128 for prompt in manifest["interface"]["defaultPrompt"])
    assert any(
        "six-way HIL" in prompt for prompt in manifest["interface"]["defaultPrompt"]
    )
    hooks = json.loads((plugin / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert set(hooks["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop"}
    stop_handler = hooks["hooks"]["Stop"][0]["hooks"][0]
    assert "stop_response.py" in stop_handler["command"]
    stop_source = (plugin / "hooks" / "stop_response.py").read_text(encoding="utf-8")
    assert '"decision"' not in stop_source
    assert '"continue": True' in stop_source
    app_manifest = json.loads((plugin / ".app.json").read_text(encoding="utf-8"))
    assert app_manifest == {
        "apps": {
            "google-drive": {
                "id": "connector_5f3c8c41a1e54ad7a76272c89e2554fa",
                "category": "Optional sealed-artifact mirror",
            }
        }
    }
    marketplace = json.loads(
        (root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert marketplace["name"] == "evidence-lane-github"
    assert marketplace["interface"]["displayName"] == "Evidence Lane GitHub"
    scan_roots = [
        root / ".agents",
        root / "docs",
        root / "tests",
        plugin / ".codex-plugin",
        plugin / "commands",
        plugin / "hooks",
        plugin / "scripts",
        plugin / "skills",
        plugin / "src" / "evidence_lane_plugin",
    ]
    source_files = [
        root / ".env.example",
        root / ".gitignore",
        root / "LICENSE.md",
        root / "pyproject.toml",
        root / "README.md",
        root / "requirements.in",
        root / "SECURITY.md",
        plugin / ".mcp.json",
        plugin / ".app.json",
        plugin / "pyproject.toml",
        plugin / "requirements.lock.txt",
    ]
    source_files.extend(
        path
        for scan_root in scan_roots
        for path in scan_root.rglob("*")
        if path.is_file()
    )
    contents = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in source_files
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and path.stat().st_size < 2_000_000
    ).lower()
    forbidden = "data" + "machine"
    assert forbidden not in contents.replace(" ", "")


def test_declared_versions_match_runtime_source_of_truth() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin_manifest = json.loads(
        (
            root / "plugins" / "evidence-lane-plugin" / ".codex-plugin" / "plugin.json"
        ).read_text(encoding="utf-8")
    )
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    plugin_project = tomllib.loads(
        (root / "plugins" / "evidence-lane-plugin" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    assert plugin_manifest["version"].split("+", 1)[0] == ENGINE_VERSION
    assert project["project"]["version"] == ENGINE_VERSION
    assert plugin_project["project"]["version"] == ENGINE_VERSION
    assert (
        plugin_project["project"]["dependencies"] == project["project"]["dependencies"]
    )
    assert plugin_project["tool"]["setuptools"]["package-dir"] == {"": "src"}


def test_session_start_hook_is_advisory(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "session_start.py"
    environment = os.environ.copy()
    store = tmp_path / "persistent-store"
    project = store / "projects" / "hook-plan-test"
    (project / "accepted").mkdir(parents=True)
    (project / "active_pointer.json").write_text(
        json.dumps(
            {
                "accepted_pv": None,
                "accepted_manifest_sha256": None,
                "generation": 0,
            }
        ),
        encoding="utf-8",
    )
    (project / "task_backlog.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"task_id": "queued", "status": "QUEUED"},
                    {"task_id": "done", "status": "DONE"},
                    {"task_id": "accepted", "status": "ACCEPTED"},
                    {"task_id": "dropped", "status": "DROPPED"},
                ]
            }
        ),
        encoding="utf-8",
    )
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(store)
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input='{"source":"startup","session_id":"test"}',
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    payload = json.loads(completed.stdout)
    assert payload["continue"] is True
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert (
        "Never infer HIL approval" in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        "PERSISTENT_STATE_ENVELOPE="
        in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        "PLUGIN_RUNTIME_ENVELOPE=" in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        '"version_state":"FRESH"' in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        "APPROVE is the only candidate promotion"
        in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert "HOST_SESSION_ID=test" in payload["hookSpecificOutput"]["additionalContext"]
    assert (
        "visibly render the engine's suggested_next_prompt"
        in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert "do not auto-submit it" in payload["hookSpecificOutput"]["additionalContext"]
    context_lines = payload["hookSpecificOutput"]["additionalContext"].splitlines()
    persistent = json.loads(
        next(
            line.removeprefix("PERSISTENT_STATE_ENVELOPE=")
            for line in context_lines
            if line.startswith("PERSISTENT_STATE_ENVELOPE=")
        )
    )
    backlog = persistent["projects"][0]["task_backlog"]
    assert backlog["queued"] == 1
    assert backlog["done_pending_hil"] == 1
    assert backlog["terminal"] == 2
    assert backlog["universal"]["ACCEPTED"] == 1
    assert backlog["universal"]["DROPPED"] == 1


def test_prompt_hook_indexes_entry_without_raw_prompt_and_resolves_rollback(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Seal a no-source-change PV2.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=["Human verifies the candidate."],
        stop_condition="Stop at PV2 HIL.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    fused = service.fuse(
        "book-faires",
        session_id,
        approval="APPROVE",
        decided_by="human-test",
        decision_id="decision_prompt_index_pv2",
    )
    handoff = fused["state_travel_handoff"]["state_travel"]
    service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="host-session-prompt-index-pv2",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "fresh-prompt-index-task"},
    )

    root = Path(__file__).resolve().parents[1]
    hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "prompt_submit.py"
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)
    secret_prompt = "Rollback checkpoint token=super-secret-value"
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "session_id": "host-session-test",
                "turn_id": "turn-prompt-index-1",
                "cwd": str(source_repository),
                "prompt": secret_prompt,
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    hook_payload = json.loads(completed.stdout)
    context = hook_payload["hookSpecificOutput"]["additionalContext"]
    indexed = json.loads(context.removeprefix("EVIDENCE_LANE_PROMPT_ENTRY="))
    assert indexed["state"] == "INDEXED"
    assert indexed["prompt_index"] == 1
    assert indexed["entry_pv"] == "PV2"
    records = list((service.store.root / "prompt-index").rglob("*.json"))
    assert len(records) == 1
    stored = records[0].read_text(encoding="utf-8")
    assert secret_prompt not in stored
    assert "super-secret-value" not in stored
    assert "Rollback checkpoint [REDACTED]" in stored
    assert '"redacted_visible_prompt_stored":true' in stored

    stop_hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "stop_response.py"
    secret_response = "Candidate sealed. sk-proj-THIS_IS_A_FAKE_TEST_KEY_1234567890"
    stopped = subprocess.run(
        [sys.executable, str(stop_hook)],
        input=json.dumps(
            {
                "session_id": "host-session-test",
                "turn_id": "turn-prompt-index-1",
                "cwd": str(source_repository),
                "last_assistant_message": secret_response,
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    stop_payload = json.loads(stopped.stdout)
    assert stop_payload == {"continue": True}
    assert "decision" not in stop_payload
    response_records = list((service.store.root / "response-index").rglob("*.json"))
    assert len(response_records) == 1
    response_record = json.loads(response_records[0].read_text(encoding="utf-8"))
    assert secret_response not in json.dumps(response_record)
    assert "THIS_IS_A_FAKE_TEST_KEY" not in json.dumps(response_record)
    assert response_record["visible_assistant_response_after_redaction"] == (
        "Candidate sealed. [REDACTED]"
    )
    assert response_record["prompt_record_sha256"] == indexed["record_sha256"]
    assert response_record["private_reasoning_stored"] is False
    assert response_record["hook_continuation_requested"] is False
    lineage_path = (
        service.store.project_root("book-faires") / "lineage" / f"{session_id}.jsonl"
    )
    lineage_events = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    response_events = [
        event
        for event in lineage_events
        if event["event_type"] == "turn.visible_assistant_response"
    ]
    assert len(response_events) == 1
    assert (
        response_events[0]["visible_payload"]["prompt_record_sha256"]
        == indexed["record_sha256"]
    )
    assert response_events[0]["visible_payload"]["private_reasoning_excluded"] is True
    assert response_events[0]["visible_payload"]["hook_continuation_requested"] is False
    repeated = subprocess.run(
        [sys.executable, str(stop_hook)],
        input=json.dumps(
            {
                "session_id": "host-session-test",
                "turn_id": "turn-prompt-index-1",
                "cwd": str(source_repository),
                "last_assistant_message": secret_response,
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    assert json.loads(repeated.stdout) == {"continue": True}
    assert len(list((service.store.root / "response-index").rglob("*.json"))) == 1
    repeated_events = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert (
        sum(
            event["event_type"] == "turn.visible_assistant_response"
            for event in repeated_events
        )
        == 1
    )

    service.resume_session(
        project_id="book-faires",
        host="codex",
        host_session_id="host-session-second-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "second-host-task"},
    )
    second = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "session_id": "host-session-second-task",
                "turn_id": "turn-prompt-index-2",
                "cwd": str(source_repository),
                "prompt": "Second task checkpoint.",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    second_context = json.loads(second.stdout)["hookSpecificOutput"][
        "additionalContext"
    ]
    second_indexed = json.loads(
        second_context.removeprefix("EVIDENCE_LANE_PROMPT_ENTRY=")
    )
    assert second_indexed["prompt_index"] == 2

    status = service.prompt_index_status("book-faires", session_id)
    assert status["raw_prompt_stored"] is False
    assert status["redacted_visible_prompt_stored"] is True
    assert [row["prompt_index"] for row in status["records"]] == [1, 2]
    assert {row["entry_pv"] for row in status["records"]} == {"PV2"}
    service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to="PV1",
        decision_id="decision_prompt_index_backward",
    )
    forward = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to="PROMPT 1",
        decision_id="decision_prompt_index_forward",
    )
    assert forward["pointer"]["accepted_pv"] == "PV2"
    assert forward["decision"]["resolution_reference"]["kind"] == "PROMPT_INDEX"
    assert forward["decision"]["resolution_reference"]["prompt_index"] == 1


def test_prompt_hook_does_not_index_unbound_chats(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "prompt_submit.py"
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(tmp_path / "empty-store")
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "session_id": "unbound-host-session",
                "turn_id": "unbound-turn",
                "cwd": str(tmp_path),
                "prompt": "This is not an Evidence Lane task.",
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
    assert indexed["state"] == "NOT_INDEXED"
    assert indexed["reason"] == "NO_BOUND_EVIDENCE_LANE_SESSION"
    assert not (tmp_path / "empty-store" / "prompt-index").exists()


def test_accepted_entry_survives_process_restart_without_rebuild(service) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.close(
        "book-faires",
        session_id,
        reason="Recreate the next prompt in a separate disposable process.",
    )
    repository_root = Path(__file__).resolve().parents[1]
    accepted_root = service.store.accepted_path("book-faires", "PV1")

    def member_hashes(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(item for item in root.rglob("*") if item.is_file())
        }

    accepted_before = member_hashes(accepted_root)
    script = """
import json
import sys
from evidence_lane_plugin.service import EvidenceLaneService

service = EvidenceLaneService(data_root=sys.argv[1])
boot = service.boot_session(
    project_id="book-faires",
    user_id="user-test",
    workspace_id="workspace-test",
    host="CODEX_DESKTOP",
    agent_id="codex-single-agent",
    sandbox_id="sandbox-process-restart",
    ephemeral=False,
    runtime_context={"permission_mode": "test-process-restart"},
)
session_id = boot["session"]["session_id"]
print(json.dumps({
    "entry_action": boot["entry_action"],
    "entry_pv": boot["persistent_state_envelope"]["entry_pv"],
    "accepted_pv": boot["persistent_state_envelope"]["accepted_pv"],
    "entry_manifest_sha256": (
        boot["persistent_state_envelope"]["entry_manifest_sha256"]
    ),
    "entry_package_sha256": (
        boot["persistent_state_envelope"]["entry_package_sha256"]
    ),
    "next_candidate_pv": service.store.next_pv_id("book-faires"),
    "accepted_history": service.store.accepted_ids("book-faires"),
}))
service.sessions.close(
    "book-faires",
    session_id,
    reason="Complete the disposable process-restart proof.",
)
"""
    environment = os.environ.copy()
    source_path = repository_root / "plugins" / "evidence-lane-plugin" / "src"
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in [str(source_path), environment.get("PYTHONPATH", "")] if part
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(service.store.root)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=repository_root,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    restarted = json.loads(completed.stdout)
    assert restarted["entry_action"] == "CLASSIFY_ONE_TASK"
    assert restarted["entry_pv"] == "PV1"
    assert restarted["accepted_pv"] == "PV1"
    assert restarted["entry_manifest_sha256"]
    assert restarted["entry_package_sha256"]
    assert restarted["next_candidate_pv"] == "PV2"
    assert restarted["accepted_history"] == ["PV1"]
    assert service.store.accepted_ids("book-faires") == ["PV1"]
    assert member_hashes(accepted_root) == accepted_before


def test_session_start_survives_cachebuster_and_remains_read_only(
    service,
    tmp_path: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.close(
        "book-faires",
        session_id,
        reason="Verify read-only startup after disposable cache replacement.",
    )
    repository_root = Path(__file__).resolve().parents[1]
    source_plugin = repository_root / "plugins" / "evidence-lane-plugin"

    def stage_cache(version: str) -> Path:
        staged = tmp_path / "plugin-cache" / version
        copies = {
            source_plugin / "hooks" / "session_start.py": (
                staged / "hooks" / "session_start.py"
            ),
            source_plugin / ".codex-plugin" / "plugin.json": (
                staged / ".codex-plugin" / "plugin.json"
            ),
            source_plugin / "src" / "evidence_lane_plugin" / "constants.py": (
                staged / "src" / "evidence_lane_plugin" / "constants.py"
            ),
            source_plugin
            / "src"
            / "evidence_lane_plugin"
            / "session_flash"
            / "env15"
            / "UNIVERSAL_FLASH_PROMPT.md": (
                staged
                / "src"
                / "evidence_lane_plugin"
                / "session_flash"
                / "env15"
                / "UNIVERSAL_FLASH_PROMPT.md"
            ),
        }
        for source, target in copies.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        manifest_path = staged / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = version
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        return staged

    def store_hashes() -> dict[str, str]:
        return {
            path.relative_to(service.store.root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(
                item for item in service.store.root.rglob("*") if item.is_file()
            )
        }

    def run_hook(staged: Path) -> tuple[dict, dict]:
        environment = os.environ.copy()
        environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)
        completed = subprocess.run(
            [sys.executable, str(staged / "hooks" / "session_start.py")],
            input='{"source":"cachebuster-persistence-test"}',
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        payload = json.loads(completed.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        lines = context.splitlines()
        runtime = json.loads(
            next(
                line.removeprefix("PLUGIN_RUNTIME_ENVELOPE=")
                for line in lines
                if line.startswith("PLUGIN_RUNTIME_ENVELOPE=")
            )
        )
        persistent = json.loads(
            next(
                line.removeprefix("PERSISTENT_STATE_ENVELOPE=")
                for line in lines
                if line.startswith("PERSISTENT_STATE_ENVELOPE=")
            )
        )
        return runtime, persistent

    first_cache = stage_cache(f"{ENGINE_VERSION}+codex.test-cache-one")
    second_cache = stage_cache(f"{ENGINE_VERSION}+codex.test-cache-two")
    build_root = service.store.project_root("book-faires") / ".build"
    assert not any(build_root.rglob("*"))
    before = store_hashes()
    first_runtime, first_persistent = run_hook(first_cache)
    after_first = store_hashes()
    second_runtime, second_persistent = run_hook(second_cache)
    after_second = store_hashes()

    assert first_runtime["version_state"] == "FRESH"
    assert second_runtime["version_state"] == "FRESH"
    assert first_runtime["plugin_manifest_version"].endswith("test-cache-one")
    assert second_runtime["plugin_manifest_version"].endswith("test-cache-two")
    assert first_persistent["persistence_class"] == "USER_OWNED_LOCAL_STORE"
    assert second_persistent["persistence_class"] == "USER_OWNED_LOCAL_STORE"
    assert first_persistent["projects"] == second_persistent["projects"]
    project = second_persistent["projects"][0]
    assert project["accepted_pv"] == "PV1"
    assert project["accepted_history"] == ["PV1"]
    assert project["highest_accepted_ordinal"] == 1
    assert project["next_candidate_pv"] == "PV2"
    assert project["accepted_manifest_sha256"]
    assert project["pointer_generation"] == 1
    assert before == after_first == after_second


def test_command_surface_covers_lifecycle_and_all_lane_commands() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "evidence-lane-plugin"
    commands = plugin / "commands"
    skills = plugin / "skills"
    public_order = [
        "evi-boot",
        "evi-rollback",
        "evi-build",
        "evi-refresh",
        "evi-mode",
        "evi-source-intake",
    ]
    expected_skills = {
        "evi",
        "evi-state-travel",
        "evi-exit-boot",
        "evi-plugin",
        "evi-storage",
        "evi-change-storage-connector",
        "evi-additional-plugin",
        "evi-drop-additional-plugin",
        *public_order,
    }
    assert not list(commands.glob("*.md"))
    for name in expected_skills:
        skill_file = skills / name / "SKILL.md"
        assert skill_file.is_file(), name
        assert f"name: {name}" in skill_file.read_text(encoding="utf-8")

    root_skill = (skills / "evi" / "SKILL.md").read_text(encoding="utf-8")
    positions = [root_skill.index(f"`/{name}`") for name in public_order]
    assert positions == sorted(positions)
    assert "State Travel" in root_skill
    assert root_skill.index("State Travel") < positions[0]
    assert "eligibility alone must not" in root_skill
    assert "explicitly requests it" in root_skill
    assert "genuinely exhausted" in root_skill
    assert "all eighteen canonical lanes" in root_skill.lower()
    assert "Project Engulf" in root_skill
    assert "`/evi-plugin` is an administrative sidecar" in root_skill
    assert "not a seventh primary" in (skills / "evi-plugin" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    skill_text = "\n".join(
        path.read_text(encoding="utf-8") for path in skills.glob("*/SKILL.md")
    )
    readme_text = (root / "README.md").read_text(encoding="utf-8")
    assert "/pv-" not in skill_text.lower()
    assert "/ev " not in skill_text.lower()
    assert "/git" not in skill_text.lower()
    assert "/local" not in skill_text.lower()
    assert "source-command-evi" not in skill_text.lower()
    assert "/pv-" not in readme_text.lower()
    assert "/ev " not in readme_text.lower()


def test_real_stdio_transport_lists_tools_and_calls_doctor(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    runner = root / "plugins" / "evidence-lane-plugin" / "scripts" / "run_mcp.py"

    async def exercise() -> None:
        environment = os.environ.copy()
        environment["EVIDENCE_LANE_DATA_ROOT"] = str(tmp_path / "stdio-store")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(runner), "--transport", "stdio"],
            env=environment,
        )
        async with (
            stdio_client(parameters) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "task_complete_and_refresh" in names
            assert "pv_state_travel_resume" in names
            result = await session.call_tool("runtime_doctor", {})
            assert result.isError is False
            assert result.structuredContent["status"] == "PASS"

    # A genuinely clean Git/cache snapshot may need the governed, hash-locked
    # plugin-local bootstrap before stdio becomes ready. The shipped MCP
    # manifest permits 900 seconds for that same first start.
    asyncio.run(asyncio.wait_for(exercise(), timeout=900))


def test_non_loopback_http_fails_closed_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BASE_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_AUDIENCE", raising=False)
    with pytest.raises(RuntimeError, match="requires static bearer or OAuth"):
        run_server(
            transport="streamable-http",
            host="0.0.0.0",
            port=8765,
        )
    monkeypatch.setenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", "test-only-token")
    with pytest.raises(RuntimeError, match="requires an HTTPS"):
        run_server(
            transport="streamable-http",
            host="0.0.0.0",
            port=8765,
        )


def test_server_start_installs_but_leaves_flash_and_runtime_detached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "remote-store"
    monkeypatch.setenv("EVIDENCE_LANE_DATA_ROOT", str(store))
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BASE_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_AUDIENCE", raising=False)
    starts: list[dict[str, object]] = []
    lifecycle_events: list[str] = []

    class FakeServer:
        def run(self, *, transport: str) -> None:
            lifecycle_events.append("event_loop")
            starts[-1]["transport"] = transport

    def fake_create_mcp_server(**kwargs: object) -> FakeServer:
        lifecycle_events.append("create_server")
        application = kwargs["service"]
        assert isinstance(application, EvidenceLaneService)
        starts.append(
            {
                "installation": application.sessions.installation_status(),
                "flash": application.session_flash_status(),
            }
        )
        return FakeServer()

    def fake_prewarm_native_dependencies() -> tuple[str, ...]:
        lifecycle_events.append("native_prewarm")
        return ("rapidocr+onnxruntime",)

    monkeypatch.setattr(
        "evidence_lane_plugin.mcp_server.create_mcp_server",
        fake_create_mcp_server,
    )
    monkeypatch.setattr(
        "evidence_lane_plugin.mcp_server.prewarm_native_dependencies",
        fake_prewarm_native_dependencies,
    )

    run_server(transport="stdio")
    run_server(transport="stdio")

    assert len(starts) == 2
    first_installation = starts[0]["installation"]
    second_installation = starts[1]["installation"]
    first_flash = starts[0]["flash"]
    second_flash = starts[1]["flash"]
    assert isinstance(first_installation, dict)
    assert isinstance(second_installation, dict)
    assert isinstance(first_flash, dict)
    assert isinstance(second_flash, dict)
    assert first_installation["state"] == "INSTALLED_UNTIL_USER_REMOVES_PLUGIN"
    assert first_installation["version"] == ENGINE_VERSION
    assert first_installation["hil_approval_inferred"] is False
    assert second_installation["installed_at"] == first_installation["installed_at"]
    assert first_flash["flash_state"] == "NOT_FLASHED"
    assert first_flash["receipt"] is None
    assert first_flash["runtime_activation"]["state"] == "DETACHED"
    assert second_flash["flash_state"] == "NOT_FLASHED"
    assert second_flash["runtime_activation"]["state"] == "DETACHED"
    assert [start["transport"] for start in starts] == ["stdio", "stdio"]
    assert lifecycle_events == [
        "native_prewarm",
        "create_server",
        "event_loop",
        "native_prewarm",
        "create_server",
        "event_loop",
    ]


def test_oauth_jwt_verifier_requires_asymmetric_algorithms_and_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="asymmetric"):
        OAuthJWTConfig(
            issuer_url="https://issuer.example/",
            jwks_url="https://issuer.example/.well-known/jwks.json",
            audience="https://mcp.example",
            required_scopes=("evidence-lane:read",),
            algorithms=("HS256",),
        )

    config = OAuthJWTConfig(
        issuer_url="https://issuer.example/",
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="https://mcp.example",
        required_scopes=("evidence-lane:read", "evidence-lane:write"),
    )
    verifier = OAuthJWTVerifier(config)

    class SigningKey:
        key = object()

    monkeypatch.setattr(
        verifier._jwk_client,
        "get_signing_key_from_jwt",
        lambda _: SigningKey(),
    )
    claims = {
        "iss": config.issuer_url,
        "aud": config.audience,
        "sub": "user-123",
        "azp": "chatgpt",
        "exp": 4_102_444_800,
        "scope": "evidence-lane:read evidence-lane:write",
    }
    monkeypatch.setattr(
        "evidence_lane_plugin.auth.decode",
        lambda *args, **kwargs: claims,
    )
    accepted = asyncio.run(verifier.verify_token("header.payload.signature"))
    assert accepted is not None
    assert accepted.subject == "user-123"
    assert accepted.client_id == "chatgpt"
    assert accepted.resource == config.audience

    claims["scope"] = "evidence-lane:read"
    assert asyncio.run(verifier.verify_token("header.payload.signature")) is None


def test_partial_remote_oauth_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL", "https://issuer.example/")
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_AUDIENCE", raising=False)
    with pytest.raises(RuntimeError, match="Incomplete OAuth configuration"):
        run_server(
            transport="streamable-http",
            host="0.0.0.0",
            port=8765,
        )

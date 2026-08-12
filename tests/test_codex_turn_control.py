from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from evidence_lane_plugin.codex_turn_control import (
    TurnControlError,
    commit_turn,
    policy_state,
    prepare_turn,
    project_task_research_status,
    record_non_strict_visible_input,
    session_start_control,
)
from evidence_lane_plugin.lineage import ChatLineage
from evidence_lane_plugin.prompt_index import PromptIndex

from .conftest import build_and_approve_pv1


def _hook_context_json(payload: dict[str, object], prefix: str) -> dict[str, object]:
    context = str(dict(payload["hookSpecificOutput"])["additionalContext"])
    line = next(row for row in context.splitlines() if row.startswith(prefix))
    return json.loads(line.removeprefix(prefix))


def _hook_change_notice(payload: dict[str, object]) -> dict[str, object]:
    for prefix in (
        "EVIDENCE_LANE_PERSISTENT_CHANGE_NOTICE=",
        "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
        "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION=",
    ):
        try:
            return _hook_context_json(payload, prefix)
        except StopIteration:
            continue
    raise AssertionError("The hook did not return a sealed Current Change projection.")


def _profile() -> dict[str, str]:
    return {
        "model": "gpt-5.6-sol",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
        "service_tier": "standard",
    }


def _strict_state_travel_session(service, before_strict=None) -> tuple[str, str]:
    session_id, _ = build_and_approve_pv1(service)
    if before_strict is not None:
        before_strict(session_id)
    task = {
        "task_id": "turn-control-row",
        "task_class": "modify_code",
        "requested_outcome": (
            "Implement the bounded authoritative Codex memory plus learning turn path."
        ),
        "permitted_paths": [
            "plugins/evidence-lane-plugin/hooks/**",
            "plugins/evidence-lane-plugin/src/evidence_lane_plugin/codex_turn_control.py",
            "tests/**",
        ],
        "permitted_tools": ["repository_read", "repository_write", "test"],
        "acceptance_checks": ["One PREPARE and one COMMIT per visible turn."],
        "stop_condition": "Stop at the final six-way HIL.",
    }
    final_hil = {
        **task,
        "task_id": "turn-control-final-hil",
        "requested_outcome": "Present the physically final six-way HIL.",
        "panel_role": "PHYSICALLY_FINAL_HIL",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[task, final_hil],
        planned_by="human-test",
        plan_id="turn-control-plan",
    )
    service.record_steer_delta(
        "book-faires",
        delta_text=(
            "Show this governed linked change persistently without exposing its raw "
            "correction text."
        ),
        actor="human-test",
        delta_id="turn-control-persistent-change-delta",
        linked_task_id=task["task_id"],
    )
    service.store.claim_backlog_task(
        "book-faires",
        backlog_task_id=task["task_id"],
        session_id=session_id,
        contract={**task, "task_id": "turn-control-runtime-task"},
    )
    service.classify_mode(
        "book-faires",
        "Analyze, research, and plan the bounded turn-control correction.",
        explicit_modes=["AL", "RS", "PL"],
        session_id=session_id,
    )
    prepared = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={"execution_profile": _profile()},
    )["state_travel"]
    host_session_id = "strict-codex-turn-control-host"
    service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=prepared["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id=host_session_id,
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"execution_profile": _profile()},
    )
    return session_id, host_session_id


def test_non_strict_prompt_chain_continues_into_strict_prepare(
    service,
    source_repository: Path,
) -> None:
    compatibility: dict[str, object] = {}

    def before_strict(_: str) -> None:
        compatibility.update(
            record_non_strict_visible_input(
                service.store.root,
                host_payload={
                    "session_id": "host-session-state-travel-pv1",
                    "turn_id": "pre-plan-visible-turn",
                    "cwd": str(source_repository),
                    "source": "mid_turn_steer",
                    "is_steer": True,
                    "prompt": "Preserve this pre-Plan steer.",
                },
            )
        )

    _, host_session_id = _strict_state_travel_session(
        service,
        before_strict=before_strict,
    )
    prepared = prepare_turn(
        service.store.root,
        host_payload={
            "session_id": host_session_id,
            "turn_id": "strict-turn-after-pre-plan-index",
            "cwd": str(source_repository),
            "source": "goal",
            "is_goal": True,
            "prompt": "Continue through the strict governed path.",
        },
    )
    assert compatibility["state"] == "INDEXED"
    assert compatibility["prompt_index"] == 1
    assert prepared["state"] == "PREPARED_NOT_COMMITTED"
    assert prepared["prompt_index"] == 2
    records = PromptIndex(service.store.root)._all_records()
    bounded = [
        row
        for row in records
        if row.get("project_id") == "book-faires"
        and row.get("evidence_session_id") == prepared["evidence_session_id"]
    ]
    assert [row["prompt_index"] for row in bounded] == [1, 2]
    assert bounded[1]["prior_record_sha256"] == bounded[0]["record_sha256"]


def test_authoritative_prepare_commit_is_redacted_idempotent_and_fts_complete(
    service,
    source_repository: Path,
) -> None:
    session_id, host_session_id = _strict_state_travel_session(service)
    prompt_payload = {
        "session_id": host_session_id,
        "turn_id": "turn-authoritative-1",
        "cwd": str(source_repository),
        "source": "goal",
        "prompt": "Implement the Goal token=super-secret-value",
        "attachments": [{"path": str(source_repository / "README.md")}],
    }
    prepared = prepare_turn(service.store.root, host_payload=prompt_payload)
    assert prepared["state"] == "PREPARED_NOT_COMMITTED"
    assert prepared["input_kind"] == "goal"
    assert prepared["attachment_identity_count"] == 1
    assert prepared["persistent_plan_row"]["task_id"] == "turn-control-row"
    assert prepared["persistent_plan_row"]["goal_projection_task_count"] == 2
    replayed = prepare_turn(service.store.root, host_payload=prompt_payload)
    assert replayed["state"] == "PREPARED_IDEMPOTENT_REUSE"
    assert replayed["control_record_sha256"] == prepared["control_record_sha256"]

    prompt_record = json.loads(
        Path(prepared["prompt_projection_path"]).read_text(encoding="utf-8")
    )
    prompt_json = json.dumps(prompt_record, sort_keys=True)
    assert "super-secret-value" not in prompt_json
    assert "Implement the Goal [REDACTED]" in prompt_json
    assert prompt_record["attachment_identities"][0]["content_sha256"]
    prepared_display = prepared["persistent_change_display"]
    assert prepared_display["state"] == "PERSISTENT_CHANGES_PRESENT"
    assert prepared_display["paired_step_task_list"]["task_count"] == 2
    assert prepared_display["paired_step_task_list"]["active_task_id"] == (
        "turn-control-row"
    )
    assert prepared_display["additive_change_summary"] == {
        "count": 1,
        "changes": [
            {
                "delta_id": "turn-control-persistent-change-delta",
                "classification": "LINKED_EXISTING_STEP",
                "boundary": "BEFORE_NEXT_HIL",
                "text_sha256": prepared_display["additive_change_summary"][
                    "changes"
                ][0]["text_sha256"],
            }
        ],
        "raw_change_text_included": False,
    }
    assert len(
        prepared_display["additive_change_summary"]["changes"][0]["text_sha256"]
    ) == 64
    assert "raw correction text" not in json.dumps(prepared_display)
    assert prepared_display["turn_status"]["uncommitted_count"] == 1
    assert prepared_display["composer_mutated"] is False
    package_status = prepared_display["package_change_status"]
    assert package_status["source_plugin_version"].startswith("2.0.0+codex.")
    assert package_status["installed_plugin_version"] is None
    assert package_status["runtime_engine_version"] == "2.0.0"
    assert package_status["version_state"] == (
        "SOURCE_RUNTIME_EXACT_INSTALL_RECEIPT_UNAVAILABLE"
    )
    assert package_status["hooks"]["count"] == 4
    assert package_status["hooks"]["count_semantics"] == (
        "REGISTERED_EVENT_COUNT"
    )
    assert package_status["hooks"]["hook_file_count"] == 5
    assert package_status["hooks"]["registered_events"] == [
        "PostToolUse",
        "SessionStart",
        "Stop",
        "UserPromptSubmit",
    ]
    assert package_status["skills"]["count"] == 15
    assert package_status["catalog"]["tools"] == 62
    assert package_status["catalog"]["read"] == 21
    assert package_status["catalog"]["write"] == 41
    assert package_status["refresh_state"] == "NO_PENDING_CANDIDATE"
    assert package_status["tunnel_channel"] == "stable"
    assert package_status["raw_paths_included"] is False

    change_path = source_repository / "tests" / "persistent-change-status.txt"
    change_path.parent.mkdir(exist_ok=True)
    change_path.write_text("visible governed change\n", encoding="utf-8")
    readme_path = source_repository / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8") + "visible tracked change\n",
        encoding="utf-8",
    )

    response_payload = {
        "session_id": host_session_id,
        "turn_id": "turn-authoritative-1",
        "cwd": str(source_repository),
        "last_assistant_message": (
            "Implemented and verified. sk-proj-THIS_IS_A_FAKE_TEST_KEY_1234567890"
        ),
        "model": "gpt-5.6-sol",
        "submodel": "sol",
        "usage": {"input_tokens": 100, "output_tokens": 25},
        "goal_usage": {
            "goal_id": "corrected-goal-continuity-through-task-019fe9aa",
            "metric_semantics": "GOAL_FINAL_COUNTER",
            "goal_accounted_tokens": 44_198_517,
            "provenance": {
                "source": "USER_CORRECTED_LEDGER",
                "accounting_basis": "GOAL_ACCOUNTED_TOKENS_ONLY",
            },
        },
        "tools": [{"name": "apply_patch", "result": "PASS"}],
        "files": ["plugins/evidence-lane-plugin/hooks/prompt_submit.py"],
        "tests": [{"name": "turn-control-targeted", "status": "PASS"}],
    }
    committed = commit_turn(service.store.root, host_payload=response_payload)
    assert committed["state"] == "COMMITTED"
    assert committed["token_metrics"]["input_tokens"] == 100
    assert committed["operational_links"]["availability"] == "AVAILABLE"
    assert committed["goal_usage"]["availability"] == "AVAILABLE"
    assert committed["goal_usage"]["goal_accounted_tokens"] == 44_198_517
    assert committed["source_change"]["changed_since_prepare"] is True
    committed_display = committed["persistent_change_display"]
    assert committed_display["state"] == "PERSISTENT_CHANGES_PRESENT"
    assert committed_display["turn_status"]["uncommitted_count"] == 0
    assert committed_display["turn_status"]["changed_since_prepare"] is True
    assert committed_display["source_change_status"]["untracked_path_count"] == 1
    source_change_status = committed_display["source_change_status"]
    assert source_change_status["tracked_diff_path_count"] == 1
    assert source_change_status["line_additions"] == 1
    assert source_change_status["line_deletions"] == 0
    assert source_change_status["binary_change_count"] == 0
    assert source_change_status["line_delta_scope"] == (
        "TRACKED_HEAD_DIFF_ONLY_UNTRACKED_EXCLUDED"
    )
    visible_changes = {
        row["path_after_redaction"]: row["status"]
        for row in source_change_status["changed_paths_after_redaction"]
    }
    assert visible_changes == {
        "README.md": " M",
        "tests/persistent-change-status.txt": "??",
    }
    assert committed_display["additive_change_summary"] == prepared_display[
        "additive_change_summary"
    ]
    committed_again = commit_turn(service.store.root, host_payload=response_payload)
    assert committed_again["state"] == "COMMITTED_IDEMPOTENT_REUSE"
    assert committed_again["commit_sha256"] == committed["commit_sha256"]

    response_record = json.loads(
        Path(committed["response_projection_path"]).read_text(encoding="utf-8")
    )
    response_json = json.dumps(response_record, sort_keys=True)
    assert "THIS_IS_A_FAKE_TEST_KEY" not in response_json
    assert response_record["visible_assistant_response_after_redaction"].endswith(
        "[REDACTED]"
    )
    database = (
        service.store.project_root("book-faires")
        / "lineage"
        / "codex_turn_control.sqlite"
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM turn_entry").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM turn_entry_fts").fetchone()[0] == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM turn_commit").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM turn_commit_fts").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM turn_research_question"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM turn_goal_usage").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='turn_research_question_fts'"
            ).fetchone()[0]
            == 0
        )
    research = project_task_research_status(
        service.store.root,
        host_session_id=host_session_id,
        cwd=str(source_repository),
    )
    assert research["research_focus"] == "MEMORY_PLUS_LEARNING"
    assert research["access_scope"] == "PROJECT_TASK_PRIVATE_ANALYSIS"
    assert research["cross_project_retrieval"] is False
    assert research["shared_fts_indexed"] is False
    assert len(research["research_questions"]) == 1
    assert "super-secret-value" not in json.dumps(research)
    assert (
        research["research_questions"][0]["visible_question_after_redaction"]
        == "Implement the Goal [REDACTED]"
    )
    aligned_question = research["research_questions"][0][
        "aligned_research_question"
    ]
    assert aligned_question == (
        "How can this governed project preserve exact task memory and measure learning "
        "continuity across its turns without cross-project disclosure, shared telemetry, "
        "or private-reasoning storage?"
    )
    assert research["research_questions"][0]["autolog_scope"] == (
        "CURRENT_PROJECT_SESSION_TASK_ONLY"
    )
    assert research["research_questions"][0]["alignment_basis"] == (
        "PERSISTENT_PLAN_ROW_AND_LINKED_STEER_DELTAS"
    )
    assert research["research_questions"][0][
        "aligned_research_question_sha256"
    ]
    assert research["final_goal_counters_by_goal_id"] == {
        "corrected-goal-continuity-through-task-019fe9aa": {
            "goal_accounted_tokens": 44_198_517,
            "observation_sha256": committed["goal_usage"]["observation_sha256"],
            "usage_record_sha256": committed["goal_usage"]["usage_record_sha256"],
        }
    }
    started = session_start_control(
        service.store.root,
        host_payload={
            "session_id": host_session_id,
            "cwd": str(source_repository),
            "source": "resume",
        },
    )
    assert started["state"] == "BOUND_NO_UNCOMMITTED_TURNS"
    warm_attach = started["warm_attach_receipt"]
    assert warm_attach["state"] == "WARM_ATTACHED_ZERO_TUNNEL_PROVISIONING_WAIT"
    assert warm_attach["route"] == "PACKAGE_LOCAL_NATIVE_MCP_ONLY"
    assert warm_attach["native_mcp_namespace"] == "mcp__evidence_lane__"
    assert warm_attach["runtime_already_active"] is True
    assert warm_attach["exact_host_session_already_attached"] is True
    assert warm_attach["boot_flash_pointer_plan_verified"] is True
    assert warm_attach["interaction_profile"] == "CODEX_APP_INTERACTIVE"
    assert warm_attach["vm_lifetime"] == "LOCAL_OR_PERSISTENT"
    assert (
        warm_attach["tunnel_requirement"]
        == "REQUIRED_FOR_INTERACTIVE_CODEX_APP_ENVIRONMENT"
    )
    assert (
        warm_attach["tunnel_setup_frequency"]
        == "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE"
    )
    assert (
        warm_attach["tunnel_key_retention"]
        == "HOST_MANAGED_PERSISTENT_PROFILE"
    )
    assert warm_attach["tunnel_action"] == (
        "NONE_IN_WARM_ATTACH_USE_HOST_ACTIVATION_ENVELOPE"
    )
    assert warm_attach["tunnel_onboarding_may_be_required"] is True
    assert warm_attach["tunnel_state_queried"] is False
    assert warm_attach["tunnel_provisioning_wait_ns"] == 0
    assert warm_attach["tunnel_health_check_wait_ns"] == 0
    assert warm_attach["codex_tunnel_lifecycle_proof_allowed"] is False
    assert warm_attach["external_windows_tunnel_health_claimed"] is False
    assert warm_attach["attach_verification_duration_ns"] >= 0
    assert warm_attach["zero_wall_clock_duration_claimed"] is False
    assert warm_attach["execution_profile"] == _profile()
    assert warm_attach["source_mutated"] is False
    assert warm_attach["candidate_created_or_accepted"] is False
    assert warm_attach["pointer_moved"] is False
    assert warm_attach["hil_inferred"] is False
    assert len(warm_attach["receipt_sha256"]) == 64
    assert (
        started["persistent_change_display"]["warm_attach"]["receipt_sha256"]
        == warm_attach["receipt_sha256"]
    )
    assert (
        started["persistent_change_display"]["paired_step_task_list"]["active_task_id"]
        == "turn-control-row"
    )
    assert started["persistent_change_display"]["additive_change_summary"] == (
        prepared_display["additive_change_summary"]
    )
    assert (
        started["persistent_change_display"]["source_change_status"][
            "changed_path_count"
        ]
        == 2
    )
    lineage = ChatLineage(
        service.store.project_root("book-faires") / "lineage" / f"{session_id}.jsonl"
    ).events()
    event_types = [row["event_type"] for row in lineage]
    assert event_types.count("turn.control_prepare") == 1
    assert event_types.count("turn.control_commit") == 1
    assert event_types.count("turn.visible_user_goal") == 1
    assert event_types.count("turn.visible_assistant_response") == 1


def test_stale_host_never_rebinds_from_cwd(service, source_repository: Path) -> None:
    _, host_session_id = _strict_state_travel_session(service)
    policy = policy_state(
        service.store.root,
        host_session_id="colliding-or-stale-host",
        cwd=str(source_repository),
    )
    assert policy["governed_session"] is True
    assert policy["strict_required"] is True
    assert policy["binding_match"] == "CWD_ONLY_STALE_OR_MISSING_HOST"
    with pytest.raises(TurnControlError) as blocked:
        prepare_turn(
            service.store.root,
            host_payload={
                "session_id": "colliding-or-stale-host",
                "turn_id": "turn-stale-host",
                "cwd": str(source_repository),
                "prompt": "Do not inherit the exact writer binding.",
            },
        )
    assert blocked.value.code == "TURN_CONTROL_EXACT_HOST_BINDING_REQUIRED"
    assert host_session_id != "colliding-or-stale-host"
    assert not (
        service.store.project_root("book-faires")
        / "lineage"
        / "codex_turn_control.sqlite"
    ).exists()


def test_native_hooks_claim_and_reuse_one_sealed_codex_host_alias(
    service,
    source_repository: Path,
    tmp_path: Path,
) -> None:
    session_id, governed_host_session_id = _strict_state_travel_session(service)
    observed_host_session_id = "019f-codex-native-host-alias-test"
    assert observed_host_session_id != governed_host_session_id
    transcript = tmp_path / "codex-rollout.jsonl"
    transcript.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    wrong_transcript = tmp_path / "different-codex-rollout.jsonl"
    wrong_transcript.write_text('{"type":"session_meta"}\n', encoding="utf-8")

    repository_root = Path(__file__).resolve().parents[1]
    prompt_hook = (
        repository_root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "prompt_submit.py"
    )
    stop_hook = (
        repository_root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "stop_response.py"
    )
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)

    def run_hook(path: Path, payload: dict[str, object]) -> dict[str, object]:
        process = subprocess.run(
            [sys.executable, str(path)],
            input=json.dumps(payload),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        return json.loads(process.stdout)

    common = {
        "session_id": observed_host_session_id,
        "cwd": str(source_repository),
        "transcript_path": str(transcript),
        "model": "gpt-5.6-sol",
        "permission_mode": "never",
    }
    alias_states: list[str] = []
    for prompt_index in (1, 2):
        turn_id = f"aliased-hook-turn-{prompt_index}"
        prepared_payload = run_hook(
            prompt_hook,
            {
                **common,
                "turn_id": turn_id,
                "source": "user_prompt",
                "prompt": f"Bound visible input {prompt_index}",
            },
        )
        assert prepared_payload["continue"] is True
        prepared = _hook_context_json(
            prepared_payload, "EVIDENCE_LANE_PROMPT_ENTRY="
        )
        assert prepared["state"] == "PREPARED_NOT_COMMITTED"
        assert prepared["prompt_index"] == prompt_index
        alias_states.append(prepared["host_binding"]["state"])
        notice = _hook_change_notice(prepared_payload)
        assert prepared_payload["systemMessage"].startswith(
            "Evidence Lane CURRENT CHANGE | "
        )
        assert notice["host_binding"]["state"] == alias_states[-1]
        assert notice["host_binding"]["raw_host_identity_stored"] is False
        assert notice["host_binding"]["raw_transcript_path_stored"] is False

        committed_payload = run_hook(
            stop_hook,
            {
                **common,
                "turn_id": turn_id,
                "last_assistant_message": f"Bound visible response {prompt_index}",
                "tests": [{"name": "sealed-host-alias", "status": "PASS"}],
            },
        )
        committed_notice = _hook_change_notice(committed_payload)
        assert committed_notice["turn_receipt"]["state"] == "COMMITTED"
        assert committed_notice["host_binding"]["state"] == (
            "SEALED_CODEX_HOST_ALIAS_REUSED"
        )

    assert alias_states == [
        "SEALED_CODEX_HOST_ALIAS_CLAIMED",
        "SEALED_CODEX_HOST_ALIAS_REUSED",
    ]
    alias_policy = policy_state(
        service.store.root,
        host_session_id=observed_host_session_id,
        cwd=str(source_repository),
        transcript_path=str(transcript),
    )
    assert alias_policy["binding_match"] == "SEALED_CODEX_HOST_ALIAS"
    assert alias_policy["reason"] == "SEALED_CODEX_HOST_ALIAS_BINDING"
    assert service.prompt_index_status("book-faires", session_id)[
        "total_resolvable"
    ] == 2

    database = (
        service.store.project_root("book-faires")
        / "lineage"
        / "codex_turn_control.sqlite"
    )
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT record_json FROM host_session_alias"
        ).fetchall()
    assert len(rows) == 1
    sealed_record_json = rows[0][0]
    sealed_record = json.loads(sealed_record_json)
    assert observed_host_session_id not in sealed_record_json
    assert str(transcript) not in sealed_record_json
    assert sealed_record["raw_host_identity_stored"] is False
    assert sealed_record["raw_transcript_path_stored"] is False
    assert len(sealed_record["observed_host_session_id_sha256"]) == 64
    assert len(sealed_record["transcript_path_sha256"]) == 64

    wrong_model = run_hook(
        prompt_hook,
        {
            **common,
            "turn_id": "aliased-hook-wrong-model",
            "model": "gpt-5.6-terra",
            "prompt": "This profile must fail closed.",
        },
    )
    assert wrong_model["continue"] is False
    wrong_model_gap = _hook_context_json(
        wrong_model, "EVIDENCE_LANE_PROMPT_ENTRY="
    )
    assert wrong_model_gap["code"] == "TURN_CONTROL_HOST_ALIAS_MODEL_MISMATCH"

    wrong_path = run_hook(
        prompt_hook,
        {
            **common,
            "turn_id": "aliased-hook-wrong-transcript",
            "transcript_path": str(wrong_transcript),
            "prompt": "This transcript must fail closed.",
        },
    )
    assert wrong_path["continue"] is False
    wrong_path_gap = _hook_context_json(
        wrong_path, "EVIDENCE_LANE_PROMPT_ENTRY="
    )
    assert wrong_path_gap["code"] == "TURN_CONTROL_HOST_ALIAS_CONFLICT"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM host_session_alias"
        ).fetchone()[0] == 1


def test_post_tool_hook_claims_prepared_exact_task_outside_repository(
    service,
    source_repository: Path,
    tmp_path: Path,
) -> None:
    session_id, governed_host_session_id = _strict_state_travel_session(service)
    repository_root = Path(__file__).resolve().parents[1]
    post_tool_hook = (
        repository_root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "post_tool_use.py"
    )
    plugin_version = json.loads(
        (
            repository_root
            / "plugins"
            / "evidence-lane-plugin"
            / ".codex-plugin"
            / "plugin.json"
        ).read_text(encoding="utf-8")
    )["version"]
    installation_root = (
        service.store.root / "installations" / "codex-v200"
    )
    install_path = installation_root / "INSTALL_TEST_EXACT_TASK.json"
    installation = {
        "schema": "evidence-lane.codex-stable-installation.v2",
        "status": "PASS",
        "plugin": {"version": plugin_version},
    }
    install_bytes = (
        json.dumps(installation, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    install_path.parent.mkdir(parents=True, exist_ok=True)
    install_path.write_bytes(install_bytes)
    (installation_root / "CURRENT_INSTALLATION.json").write_bytes(install_bytes)
    install_sha256 = hashlib.sha256(install_bytes).hexdigest().upper()

    observed_task_id = "019fedc7-cb86-7b40-94ce-1784a999f12b"
    preparation_path = installation_root / "restart-test" / (
        "CODEX_RESTART_PREPARATION.json"
    )
    preparation = {
        "schema": "evidence-lane.codex-restart-preparation.v2",
        "state": "PREPARED_NOT_RESTARTED",
        "project_id": "book-faires",
        "evidence_session_id": session_id,
        "task_id": observed_task_id,
        "host_session_id": governed_host_session_id,
        "install_receipt_sha256": install_sha256,
        "plugin_version": plugin_version,
    }
    preparation_bytes = (
        json.dumps(preparation, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    preparation_path.parent.mkdir(parents=True, exist_ok=True)
    preparation_path.write_bytes(preparation_bytes)
    preparation_sha256 = hashlib.sha256(preparation_bytes).hexdigest().upper()

    binding_path = (
        installation_root / "task-bindings" / f"{observed_task_id}.json"
    )
    task_binding = {
        "schema": "evidence-lane.codex-task-binding.v1",
        "state": "EXACT_TASK_BINDING_PREPARED",
        "project_id": "book-faires",
        "evidence_session_id": session_id,
        "task_id": observed_task_id,
        "governed_host_session_id": governed_host_session_id,
        "plugin_version": plugin_version,
        "task_uri_sha256": hashlib.sha256(
            f"codex://threads/{observed_task_id}".encode()
        ).hexdigest().upper(),
        "preparation_receipt": str(preparation_path),
        "preparation_receipt_sha256": preparation_sha256,
        "install_receipt": str(install_path),
        "install_receipt_sha256": install_sha256,
        "claim_scope": "EXACT_CODEX_THREAD_ID_ONLY",
        "alias_claim_allowed": True,
        "source_mutated": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(
        json.dumps(task_binding, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    task_binding_sha256 = hashlib.sha256(binding_path.read_bytes()).hexdigest().upper()

    task_workspace = tmp_path / "separate-codex-task-shell"
    task_workspace.mkdir()
    transcript = tmp_path / "exact-task-rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)
    payload = {
        "session_id": observed_task_id,
        "turn_id": "resumed-running-goal",
        "cwd": str(task_workspace),
        "transcript_path": str(transcript),
        "model": "gpt-5.6-sol",
        "permission_mode": "dontAsk",
        "tool_name": "mcp__evidence_lane__pv_plan_steer_delta",
        "tool_use_id": "exact-task-projection",
    }

    first = subprocess.run(
        [sys.executable, str(post_tool_hook)],
        input=json.dumps(payload),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    first_payload = json.loads(first.stdout)
    first_notice = _hook_change_notice(first_payload)
    assert first_notice["phase"] == "POST_TOOL_USE"
    assert first_notice["host_binding"]["state"] == (
        "SEALED_CODEX_HOST_ALIAS_CLAIMED"
    )
    assert first_notice["host_binding"]["raw_host_identity_stored"] is False
    assert first_notice["paired_step_task_list"]["active_task_id"] == (
        "turn-control-row"
    )
    assert service.prompt_index_status("book-faires", session_id)[
        "total_resolvable"
    ] == 0

    database = (
        service.store.project_root("book-faires")
        / "lineage"
        / "codex_turn_control.sqlite"
    )
    with sqlite3.connect(database) as connection:
        record = json.loads(
            connection.execute(
                "SELECT record_json FROM host_session_alias"
            ).fetchone()[0]
        )
    assert record["task_binding_receipt_sha256"] == task_binding_sha256
    assert record["binding_basis"].startswith(
        "INSTALLER_PREPARED_EXACT_CODEX_TASK"
    )

    second = subprocess.run(
        [sys.executable, str(post_tool_hook)],
        input=json.dumps(payload),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    second_notice = _hook_change_notice(json.loads(second.stdout))
    assert second_notice["host_binding"]["state"] == (
        "SEALED_CODEX_HOST_ALIAS_REUSED"
    )
    rebound = policy_state(
        service.store.root,
        host_session_id=observed_task_id,
        cwd=str(task_workspace),
        transcript_path=str(transcript),
    )
    assert rebound["binding_match"] == "SEALED_CODEX_HOST_ALIAS"
    assert rebound["reason"] == "SEALED_CODEX_HOST_ALIAS_BINDING"


def test_native_hook_adapters_prepare_commit_chain_and_fail_closed(
    service,
    source_repository: Path,
) -> None:
    session_id, host_session_id = _strict_state_travel_session(service)
    repository_root = Path(__file__).resolve().parents[1]
    prompt_hook = (
        repository_root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "prompt_submit.py"
    )
    stop_hook = (
        repository_root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "stop_response.py"
    )
    post_tool_hook = (
        repository_root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "post_tool_use.py"
    )
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)

    projected_process = subprocess.run(
        [sys.executable, str(post_tool_hook)],
        input=json.dumps(
            {
                "session_id": host_session_id,
                "turn_id": "ongoing-goal-without-fresh-prompt",
                "cwd": str(source_repository),
                "model": "gpt-5.6-sol",
                "tool_name": "mcp__evidence_lane__pv_plan_steer_delta",
                "tool_use_id": "tool-use-mid-goal-projection",
                "tool_input": {"secret": "must-not-be-stored"},
                "tool_response": {"secret": "must-not-be-stored"},
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    projected_payload = json.loads(projected_process.stdout)
    assert projected_payload["continue"] is True
    projected_notice = _hook_change_notice(projected_payload)
    assert projected_notice["phase"] == "POST_TOOL_USE"
    assert projected_notice["paired_step_task_list"]["active_task_id"] == (
        "turn-control-row"
    )
    assert projected_notice["tool_projection"] == {
        "tool_name": "mcp__evidence_lane__pv_plan_steer_delta",
        "tool_use_id_sha256": hashlib.sha256(
            b"tool-use-mid-goal-projection"
        ).hexdigest().upper(),
        "read_only_projection": True,
        "tool_input_stored": False,
        "tool_response_stored": False,
    }
    assert projected_notice["package_change_status"]["hooks"]["count"] == 4
    assert projected_notice["package_change_status"]["hooks"][
        "count_semantics"
    ] == "REGISTERED_EVENT_COUNT"
    assert projected_notice["package_change_status"]["hooks"][
        "hook_file_count"
    ] == 5
    assert service.prompt_index_status("book-faires", session_id)[
        "total_resolvable"
    ] == 0

    prepared_receipts: list[dict[str, object]] = []
    for prompt_index in (1, 2):
        turn_id = f"hook-turn-{prompt_index}"
        prepared_process = subprocess.run(
            [sys.executable, str(prompt_hook)],
            input=json.dumps(
                {
                    "session_id": host_session_id,
                    "turn_id": turn_id,
                    "cwd": str(source_repository),
                    "source": "steer" if prompt_index == 2 else "user_prompt",
                    "prompt": f"Visible input {prompt_index} token=hidden-{prompt_index}",
                }
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        prepared_payload = json.loads(prepared_process.stdout)
        assert prepared_payload["continue"] is True
        prepared = _hook_context_json(
            prepared_payload, "EVIDENCE_LANE_PROMPT_ENTRY="
        )
        assert prepared["state"] == "PREPARED_NOT_COMMITTED"
        assert prepared["prompt_index"] == prompt_index
        assert prepared["record_sha256"] == prepared["prompt_record_sha256"]
        assert prepared["entry_pv"] == prepared["accepted_pv"] == "PV1"
        prepared_notice = _hook_change_notice(prepared_payload)
        assert prepared_notice["phase"] == "TURN_PREPARE"
        assert prepared_notice["paired_step_task_list"]["active_task_id"] == (
            "turn-control-row"
        )
        assert prepared_notice["linked_delta_status"]["active_delta"][
            "delta_id"
        ] == "turn-control-persistent-change-delta"
        assert prepared_notice["linked_delta_status"]["raw_change_text_included"] is False
        assert prepared_notice["exact_above_prompt_bar_placement_claimed"] is False
        assert prepared_notice["host_rendering_authority"] == "CODEX_HOST_OWNED"
        assert prepared_notice["package_change_status"]["hooks"]["count"] == 4
        assert prepared_notice["package_change_status"]["hooks"][
            "hook_file_count"
        ] == 5
        assert prepared_notice["package_change_status"]["skills"]["count"] == 15
        assert prepared_notice["package_change_status"]["catalog"] == {
            "tools": 62,
            "read": 21,
            "write": 41,
            "skills": 15,
            "changed_from_previous": None,
        }
        assert prepared_notice["package_change_status"]["refresh_state"] == (
            "NO_PENDING_CANDIDATE"
        )
        prepared_receipts.append(prepared)

        committed_process = subprocess.run(
            [sys.executable, str(stop_hook)],
            input=json.dumps(
                {
                    "session_id": host_session_id,
                    "turn_id": turn_id,
                    "cwd": str(source_repository),
                    "last_assistant_message": (
                        f"Visible response {prompt_index} "
                        "sk-proj-THIS_IS_A_FAKE_TEST_KEY_1234567890"
                    ),
                    "model": "gpt-5.6-sol",
                    "submodel": "sol",
                    "tests": [{"name": "hook-adapter", "status": "PASS"}],
                }
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        committed_payload = json.loads(committed_process.stdout)
        assert committed_payload["continue"] is True
        committed_notice = _hook_change_notice(committed_payload)
        assert committed_notice["phase"] == "TURN_COMMIT"
        assert committed_notice["turn_receipt"]["state"] == "COMMITTED"
        assert committed_notice["turn_receipt"]["prompt_index"] == prompt_index
        assert committed_notice["private_reasoning_stored"] is False

    prompt_records = PromptIndex(service.store.root)._all_records()
    assert [row["prompt_index"] for row in prompt_records] == [1, 2]
    assert prompt_records[0]["prior_record_sha256"] is None
    assert (
        prompt_records[1]["prior_record_sha256"]
        == prompt_records[0]["record_sha256"]
        == prepared_receipts[0]["prompt_record_sha256"]
    )
    assert (
        service.prompt_index_status("book-faires", session_id)["total_resolvable"] == 2
    )

    session_start_hook = (
        repository_root
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "session_start.py"
    )
    resumed_process = subprocess.run(
        [sys.executable, str(session_start_hook)],
        input=json.dumps(
            {
                "session_id": host_session_id,
                "cwd": str(source_repository),
                "source": "compact",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    resumed_payload = json.loads(resumed_process.stdout)
    persistent_display = _hook_change_notice(resumed_payload)
    assert persistent_display["phase"] == "SESSION_START"
    assert persistent_display["paired_step_task_list"]["task_count"] == 2
    assert persistent_display["paired_step_task_list"]["active_task_id"] == (
        "turn-control-row"
    )
    assert persistent_display["requested_ui_surface"] == (
        "CODEX_SYSTEM_MESSAGE_WARNING_NEAR_COMPOSER"
    )
    assert persistent_display["source_change_status"]["changed_path_count"] >= 0
    assert persistent_display["private_research_question_included"] is False
    assert persistent_display["composer_mutated"] is False
    context_lines = resumed_payload["hookSpecificOutput"]["additionalContext"].splitlines()
    warm_attach_line = next(
        line for line in context_lines if line.startswith("CODEX_WARM_ATTACH_RECEIPT=")
    )
    hook_warm_attach = json.loads(
        warm_attach_line.removeprefix("CODEX_WARM_ATTACH_RECEIPT=")
    )
    assert hook_warm_attach["receipt_sha256"] == persistent_display[
        "warm_attach_receipt_sha256"
    ]
    assert hook_warm_attach["tunnel_provisioning_wait_ns"] == 0
    assert hook_warm_attach["tunnel_state_queried"] is False
    assert hook_warm_attach["zero_wall_clock_duration_claimed"] is False

    stale_process = subprocess.run(
        [sys.executable, str(prompt_hook)],
        input=json.dumps(
            {
                "session_id": "stale-hook-host",
                "turn_id": "stale-hook-turn",
                "cwd": str(source_repository),
                "prompt": "Never bind this turn from cwd alone.",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    stale_payload = json.loads(stale_process.stdout)
    assert stale_payload["continue"] is False
    stale_gap = _hook_context_json(
        stale_payload, "EVIDENCE_LANE_PROMPT_ENTRY="
    )
    assert stale_gap["state"] == "TURN_CONTROL_GAP"
    assert stale_gap["code"] == "TURN_CONTROL_EXACT_HOST_BINDING_REQUIRED"
    assert stale_gap["fail_closed"] is True
    with pytest.raises(TurnControlError) as private_blocked:
        project_task_research_status(
            service.store.root,
            host_session_id="stale-hook-host",
            cwd=str(source_repository),
        )
    assert private_blocked.value.code == "TURN_CONTROL_EXACT_HOST_BINDING_REQUIRED"


def test_failed_prepare_remains_uncommitted_and_session_start_recovers(
    service,
    source_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, host_session_id = _strict_state_travel_session(service)
    original_append = ChatLineage.append
    failed = {"done": False}

    def fail_prepare_once(self, **kwargs):
        if kwargs.get("event_type") == "turn.control_prepare" and not failed["done"]:
            failed["done"] = True
            raise RuntimeError("forced prepare projection interruption")
        return original_append(self, **kwargs)

    monkeypatch.setattr(ChatLineage, "append", fail_prepare_once)
    with pytest.raises(RuntimeError, match="forced prepare projection interruption"):
        prepare_turn(
            service.store.root,
            host_payload={
                "session_id": host_session_id,
                "turn_id": "turn-interrupted-prepare",
                "cwd": str(source_repository),
                "prompt": "Prepare this bounded interrupted turn.",
            },
        )
    monkeypatch.setattr(ChatLineage, "append", original_append)
    recovered = session_start_control(
        service.store.root,
        host_payload={
            "session_id": host_session_id,
            "cwd": str(source_repository),
            "source": "resume",
        },
    )
    assert recovered["state"] == "RECOVERED_PREPARED_NOT_COMMITTED"
    assert recovered["uncommitted_count"] == 1
    assert recovered["uncommitted"][0]["state"] == "PREPARED_NOT_COMMITTED"
    database = (
        service.store.project_root("book-faires")
        / "lineage"
        / "codex_turn_control.sqlite"
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM turn_entry").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM turn_commit").fetchone()[0] == 0

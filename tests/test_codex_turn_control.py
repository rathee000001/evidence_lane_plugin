from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from evidence_lane_plugin.canon_runtime_continuity import (
    seal_host_exit_continuity_packet,
    seal_observed_experience_packet,
    validate_host_exit_continuity_packet,
    validate_observed_experience_packet,
)
from evidence_lane_plugin.codex_turn_control import (
    TurnControlError,
    commit_turn,
    policy_state,
    prepare_turn,
    project_task_research_status,
    record_lifecycle_boundary_event,
    record_non_strict_visible_input,
    record_tool_event,
    resolve_codex_hook_store_root,
    seal_exact_task_project_session_binding,
    seal_lifecycle_exit_slip,
    session_start_control,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.lineage import ChatLineage
from evidence_lane_plugin.models import SessionState
from evidence_lane_plugin.persistence import route_persistence
from evidence_lane_plugin.prompt_index import PromptIndex
from evidence_lane_plugin.service import EvidenceLaneService

from .conftest import (
    build_and_approve_pv1,
    state_travel_destination_creation,
)


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


def _host_shaped_user_prompt_submit_payload(
    *,
    host_session_id: str,
    turn_id: str,
    cwd: Path,
    prompt: str,
    **extra: object,
) -> dict[str, object]:
    """Model the payload passed by the installed ``UserPromptSubmit`` adapter.

    This proves only that the adapter was invoked against the native payload
    shape.  It deliberately cannot claim independent installed-host dispatch
    proof; that requires separate host/log correlation.
    """

    return {
        "session_id": host_session_id,
        "turn_id": turn_id,
        "cwd": str(cwd),
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "evidence_lane_capture_dispatch": {
            "surface": "PENDING_VISIBLE_USER_INPUT",
            "host_route": "inspect_pending_input(TurnInput::UserInput)",
            "native_hook_event": "UserPromptSubmit",
            "host_payload_hook_event_name": "UserPromptSubmit",
            "adapter_invocation_observed": True,
            "installed_host_dispatch_independently_proven": False,
            "input_kind_derived_from_sealed_state": True,
            "caller_input_kind_authority": False,
        },
        **extra,
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
        runtime_context={
            "execution_profile": _profile(),
            "state_travel_destination_creation": (
                state_travel_destination_creation(
                    "host-session-state-travel-pv1",
                    host_session_id,
                )
            ),
        },
    )
    session = service.sessions.load("book-faires", session_id)
    session.metadata["active_backlog_task_id"] = task["task_id"]
    session.metadata["active_backlog_task_status"] = "ACTIVE"
    service.sessions._save(session)
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
        host_payload=_host_shaped_user_prompt_submit_payload(
            host_session_id=host_session_id,
            turn_id="strict-turn-after-pre-plan-index",
            cwd=source_repository,
            prompt="Continue through the strict governed path.",
        ),
    )
    assert compatibility["state"] == "INDEXED"
    assert compatibility["prompt_index"] == 1
    assert prepared["state"] == "PREPARED_NOT_COMMITTED"
    assert prepared["input_kind"] == "user_prompt"
    assert prepared["capture_dispatch"]["adapter_invocation_observed"] is True
    assert prepared["capture_dispatch"][
        "installed_host_dispatch_independently_proven"
    ] is False
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
    prompt_payload = _host_shaped_user_prompt_submit_payload(
        host_session_id=host_session_id,
        turn_id="turn-authoritative-1",
        cwd=source_repository,
        prompt="Implement the prompt token=super-secret-value",
        host_kind="CODEX_DESKTOP",
        host_profile="CODEX_LOCAL_PC_OR_LAPTOP",
        host_app="CODEX",
        model="gpt-5.6-sol",
        submodel="sol",
        attachments=[{"path": str(source_repository / "README.md")}],
    )
    prepared = prepare_turn(service.store.root, host_payload=prompt_payload)
    assert prepared["state"] == "PREPARED_NOT_COMMITTED"
    assert prepared["input_kind"] == "user_prompt"
    assert prepared["capture_dispatch"]["state"] == (
        "USERPROMPTSUBMIT_ADAPTER_INVOKED"
    )
    assert prepared["capture_dispatch"]["adapter_invocation_observed"] is True
    assert prepared["capture_dispatch"][
        "installed_host_dispatch_independently_proven"
    ] is False
    assert prepared["capture_dispatch"][
        "independent_installed_host_proof_required"
    ] is True
    assert prepared["attachment_identity_count"] == 1
    assert prepared["persistent_plan_row"]["task_id"] == "turn-control-row"
    assert prepared["persistent_plan_row"]["goal_projection_task_count"] == 2
    replayed = prepare_turn(service.store.root, host_payload=prompt_payload)
    assert replayed["state"] == "PREPARED_IDEMPOTENT_REUSE"
    assert replayed["control_record_sha256"] == prepared["control_record_sha256"]

    tool_payload = {
        **prompt_payload,
        "tool_name": "pv_status",
        "tool_use_id": "tool-use-authoritative-1",
        "tool_input": {"project_id": "book-faires"},
    }
    tool_record = record_tool_event(
        service.store.root,
        host_payload=tool_payload,
        phase="before",
    )
    assert tool_record["state"] == "RECORDED"
    lineage_path = (
        service.store.project_root("book-faires") / "lineage" / f"{session_id}.jsonl"
    )
    lineage_count = len(lineage_path.read_text(encoding="utf-8").splitlines())
    database = (
        service.store.project_root("book-faires")
        / "lineage"
        / "codex_turn_control.sqlite"
    )
    with sqlite3.connect(database) as connection:
        existing_tool = json.loads(
            connection.execute(
                "SELECT event_json FROM turn_tool_event "
                "WHERE tool_use_id=? AND phase=?",
                ("tool-use-authoritative-1", "before"),
            ).fetchone()[0]
        )
        existing_tool["event_payload"].pop("host_identity")
        legacy_sha256 = hashlib.sha256(
            (
                json.dumps(
                    existing_tool["event_payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest().upper()
        existing_tool["tool_event_sha256"] = legacy_sha256
        connection.execute(
            "UPDATE turn_tool_event SET tool_event_sha256=?, event_json=? "
            "WHERE tool_use_id=? AND phase=?",
            (
                legacy_sha256,
                json.dumps(existing_tool, sort_keys=True, separators=(",", ":")),
                "tool-use-authoritative-1",
                "before",
            ),
        )
        connection.commit()
    legacy_replay = record_tool_event(
        service.store.root,
        host_payload=tool_payload,
        phase="before",
    )
    assert legacy_replay["state"] == "RECORDED_IDEMPOTENT_REUSE"
    assert legacy_replay["tool_event_sha256"] == legacy_sha256
    assert len(lineage_path.read_text(encoding="utf-8").splitlines()) == lineage_count

    precompact = record_lifecycle_boundary_event(
        service.store.root,
        host_payload={**prompt_payload, "event_id": "compact-boundary-1"},
        event_name="PreCompact",
    )
    assert precompact["state"] == "SEALED"
    assert precompact["receipt"]["phase"] == "COMPACTION_SEAL"
    assert precompact["receipt"]["raw_prompt_stored"] is False
    assert precompact["receipt"]["raw_tool_payload_stored"] is False
    assert precompact["receipt"]["private_reasoning_stored"] is False
    assert precompact["receipt"]["plan_or_delta_mutated"] is False
    precompact_replay = record_lifecycle_boundary_event(
        service.store.root,
        host_payload={**prompt_payload, "event_id": "compact-boundary-1"},
        event_name="PreCompact",
    )
    assert precompact_replay["state"] == "SEALED_IDEMPOTENT_REUSE"
    assert precompact_replay["receipt"] == precompact["receipt"]
    postcompact = record_lifecycle_boundary_event(
        service.store.root,
        host_payload={**prompt_payload, "event_id": "compact-boundary-2"},
        event_name="PostCompact",
    )
    assert postcompact["receipt"]["phase"] == "COMPACTION_REHYDRATION"
    session_end = record_lifecycle_boundary_event(
        service.store.root,
        host_payload={**prompt_payload, "event_id": "session-end-1"},
        event_name="SessionEnd",
    )
    assert session_end["receipt"]["phase"] == (
        "BEST_EFFORT_SESSION_BOUNDARY_FLUSH"
    )

    prompt_record = json.loads(
        Path(prepared["prompt_projection_path"]).read_text(encoding="utf-8")
    )
    prompt_json = json.dumps(prompt_record, sort_keys=True)
    assert "super-secret-value" not in prompt_json
    assert "Implement the prompt [REDACTED]" in prompt_json
    assert prompt_record["attachment_identities"][0]["content_sha256"]
    prepared_display = prepared["persistent_change_display"]
    assert prepared_display["state"] == "PERSISTENT_CHANGES_PRESENT"
    assert prepared_display["paired_step_task_list"]["task_count"] == 2
    assert prepared_display["paired_step_task_list"]["active_task_id"] == (
        "turn-control-row"
    )
    assert "host_step_task_list_projection" not in prepared_display
    assert "update_plan" not in json.dumps(prepared_display)
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
    assert package_status["source_plugin_version"].startswith("2.2.0+codex.")
    assert package_status["installed_plugin_version"] is None
    assert package_status["runtime_engine_version"] == "2.2.0"
    assert package_status["version_state"] == (
        "SOURCE_RUNTIME_EXACT_INSTALL_RECEIPT_UNAVAILABLE"
    )
    assert package_status["hooks"]["count"] == 8
    assert package_status["hooks"]["count_semantics"] == (
        "REGISTERED_EVENT_COUNT"
    )
    assert package_status["hooks"]["hook_file_count"] == 9
    assert package_status["hooks"]["registered_events"] == [
        "PostCompact",
        "PostToolUse",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
        "SessionStart",
        "Stop",
        "UserPromptSubmit",
    ]
    assert package_status["skills"]["count"] == 15
    assert package_status["catalog"]["tools"] == 62
    assert package_status["catalog"]["read"] == 21
    assert package_status["catalog"]["write"] == 41
    assert package_status["refresh_state"] == "NO_PENDING_CANDIDATE"
    assert package_status["tunnel_channel"] == "stable-build"
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
        "host_kind": "CODEX_DESKTOP",
        "host_profile": "CODEX_LOCAL_PC_OR_LAPTOP",
        "host_app": "CODEX",
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
            "components": {
                "input_tokens": 30_000_000,
                "output_tokens": 10_000_000,
                "cached_input_tokens": 8_000_000,
                "reasoning_tokens": 4_000_000,
                "main_agent_tokens": 40_000_000,
                "subagent_tokens": 4_198_517,
            },
            "provenance": {
                "source": "USER_CORRECTED_LEDGER",
                "accounting_basis": "GOAL_ACCOUNTED_TOKENS_ONLY",
                "operator_note": "api_key=FAKE_GOAL_USAGE_SECRET_1234567890",
            },
            "profile_observations": [
                {"observed_on": "2026-08-12", "raw": 902_300_000},
                {"observed_on": "2026-08-13", "raw": 549_600_000},
            ],
            "user_exclusive_attribution": (
                "The governed task exclusively produced both displayed daily totals."
            ),
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
    accounting = committed["goal_usage"]["component_accounting"]
    assert accounting["components"]["cached_input_tokens"]["raw"] == 8_000_000
    assert accounting["components"]["reasoning_tokens"]["raw"] == 4_000_000
    assert accounting["components"]["subagent_tokens"]["raw"] == 4_198_517
    assert accounting["final_aggregate"]["raw"] == 44_198_517
    assert accounting["final_aggregate"]["basis"] == "HOST_EXPOSED_FINAL_TOTAL"
    assert accounting["main_and_subagent_double_count_prevented"] is True
    assert accounting["binding"]["project_id"] == "book-faires"
    assert accounting["binding"]["evidence_session_id"]
    assert accounting["binding"]["task_id"] == "turn-control-row"
    assert len(accounting["binding"]["host_session_id_sha256"]) == 64
    assert "FAKE_GOAL_USAGE_SECRET" not in json.dumps(committed["goal_usage"])
    profile_context = committed["goal_usage"]["profile_observed_context"]
    assert profile_context["arithmetic_sum"]["raw"] == 1_451_900_000
    assert profile_context["arithmetic_sum"]["display"] == "1.4519B"
    assert profile_context["user_attestation"][
        "proven_by_profile_screenshots"
    ] is False
    assert profile_context["machine_telemetry"] == (
        "NOT_ESTABLISHED_BY_PROFILE_SCREENSHOTS"
    )
    assert profile_context["binding"] == accounting["binding"]
    assert profile_context["cross_project_retrieval"] is False
    assert profile_context["public_output_included"] is False
    assert committed["source_change"]["changed_since_prepare"] is True
    assert committed["lifecycle_exit_slip_emitted"] is False
    assert committed["historical_exit_slip_alias_reused"] is False
    assert committed["ordinary_turn_commit_receipt"]["receipt_role"] == (
        "ORDINARY_TURN_COMMIT_NOT_LIFECYCLE_EXIT"
    )
    paused = seal_lifecycle_exit_slip(
        service.store.root,
        host_payload={
            **response_payload,
            "last_assistant_message": "",
        },
        reason="EXPLICIT_PAUSE",
        visible_reason="Pause only this exact task and resume the same Plan row.",
    )
    assert paused["state"] == "SEALED"
    assert paused["receipt"]["reason"] == "EXPLICIT_PAUSE"
    assert paused["receipt"]["resume_same_plan_task_id"] == "turn-control-row"
    assert paused["receipt"]["resume_same_row_required"] is True
    assert paused["receipt"]["active_task_transitioned"] is False
    assert paused["receipt"]["candidate_created_or_accepted"] is False
    assert paused["receipt"]["pointer_moved"] is False
    paused_replay = seal_lifecycle_exit_slip(
        service.store.root,
        host_payload=response_payload,
        reason="EXPLICIT_PAUSE",
        visible_reason="Pause only this exact task and resume the same Plan row.",
    )
    assert paused_replay["state"] == "SEALED_IDEMPOTENT_REUSE"
    assert paused_replay["receipt"] == paused["receipt"]
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
    assert research["shared_global_telemetry"] is False
    assert research["public_output_included"] is False
    assert len(research["research_questions"]) == 1
    assert "super-secret-value" not in json.dumps(research)
    assert (
        research["research_questions"][0]["visible_question_after_redaction"]
        == "Implement the prompt [REDACTED]"
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
        == "NOT_REQUIRED_FOR_LOCAL_CODEX_NATIVE_LAYER"
    )
    assert warm_attach["tunnel_setup_frequency"] == "NONE"
    assert warm_attach["tunnel_key_retention"] == "NOT_APPLICABLE"
    assert warm_attach["tunnel_action"] == (
        "NONE_IN_WARM_ATTACH_USE_HOST_ACTIVATION_ENVELOPE"
    )
    assert warm_attach["tunnel_onboarding_may_be_required"] is False
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
    assert event_types.count("turn.visible_user_prompt") == 1
    assert event_types.count("turn.visible_assistant_response") == 1
    visible_events = {
        row["event_type"]: row
        for row in lineage
        if row["event_type"]
        in {"turn.visible_user_prompt", "turn.visible_assistant_response"}
    }
    for event in visible_events.values():
        host_identity = event["visible_payload"]["host_identity"]
        assert host_identity["host_kind"] == "CODEX_DESKTOP"
        assert host_identity["host_profile"] == "CODEX_LOCAL_PC_OR_LAPTOP"
        assert host_identity["host_app"] == "CODEX"
        assert len(host_identity["host_session_id_sha256"]) == 64
        assert host_identity["raw_host_session_id_stored"] is False
        assert host_session_id not in json.dumps(event, sort_keys=True)
        assert event["model"] == "gpt-5.6-sol"
        assert event["submodel"] == "sol"


def test_lifecycle_exit_boundary_matrix_is_idempotent_and_row_bound(
    service,
    source_repository: Path,
) -> None:
    session_id, host_session_id = _strict_state_travel_session(service)
    prompt_payload = _host_shaped_user_prompt_submit_payload(
        host_session_id=host_session_id,
        turn_id="turn-before-lifecycle-boundary",
        cwd=source_repository,
        prompt="Prepare the exact row before a bounded lifecycle exit.",
    )
    prepared = prepare_turn(service.store.root, host_payload=prompt_payload)
    committed = commit_turn(
        service.store.root,
        host_payload={
            **prompt_payload,
            "last_assistant_message": "The exact ordinary turn is committed.",
        },
    )
    assert committed["lifecycle_exit_slip_emitted"] is False

    boundary_reasons = [
        "HIL_WAIT",
        "EXPLICIT_PAUSE",
        "GENUINE_BLOCK",
        "GOVERNED_ERROR",
        "EXIT_BOOT",
        "STATE_TRAVEL_HANDOFF",
        "STATELESS_EPHEMERAL_END",
    ]
    sealed: dict[str, dict[str, object]] = {}
    for reason in boundary_reasons:
        payload: dict[str, object] = {
            "session_id": host_session_id,
            "turn_id": f"lifecycle-{reason.lower()}",
            "cwd": str(source_repository),
        }
        if reason == "STATELESS_EPHEMERAL_END":
            payload["runtime_context"] = {
                "ephemeral": True,
                "stateless_invocation": True,
                "interaction_profile": "HEADLESS_API",
            }
        result = seal_lifecycle_exit_slip(
            service.store.root,
            host_payload=payload,
            reason=reason,
            visible_reason=f"Bounded lifecycle boundary: {reason}.",
        )
        receipt = result["receipt"]
        sealed[reason] = result
        assert result["status"] == "PASS"
        assert result["state"] == "SEALED"
        assert receipt["reason"] == reason
        assert receipt["project_id"] == "book-faires"
        assert receipt["evidence_session_id"] == session_id
        assert receipt["task_id"] == "turn-control-row"
        assert receipt["plan_task_id"] == "turn-control-row"
        assert receipt["active_row"] == 1
        assert receipt["resume_same_plan_task_id"] == "turn-control-row"
        assert receipt["resume_same_row_required"] is True
        assert receipt["latest_control_record_sha256"] == prepared[
            "control_record_sha256"
        ]
        assert receipt["latest_turn_state"] == "COMMITTED"
        assert receipt["active_task_transitioned"] is False
        assert receipt["candidate_created_or_accepted"] is False
        assert receipt["pointer_moved"] is False
        assert receipt["hil_inferred"] is False
        assert receipt["private_reasoning_stored"] is False
        if reason == "STATELESS_EPHEMERAL_END":
            assert receipt["stateless_ephemeral_proof"] == {
                "ephemeral": True,
                "stateless": True,
                "interaction_profile": "HEADLESS_API",
                "proof_source": "EXPLICIT_HOST_OR_RUNTIME_CONTEXT",
            }
        else:
            assert receipt["stateless_ephemeral_proof"] is None

        replay = seal_lifecycle_exit_slip(
            service.store.root,
            host_payload=payload,
            reason=reason,
            visible_reason=f"Bounded lifecycle boundary: {reason}.",
        )
        assert replay["state"] == "SEALED_IDEMPOTENT_REUSE"
        assert replay["receipt"] == receipt
        assert replay["receipt"]["emitted_at"] == receipt["emitted_at"]
        assert replay["receipt"]["exit_slip_sha256"] == receipt[
            "exit_slip_sha256"
        ]
        receipt_body = {
            key: value
            for key, value in receipt.items()
            if key != "exit_slip_sha256"
        }
        expected_receipt_sha256 = hashlib.sha256(
            (
                json.dumps(
                    receipt_body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest().upper()
        assert receipt["exit_slip_sha256"] == expected_receipt_sha256
        assert str(receipt["emitted_at"]).endswith("Z")

    with pytest.raises(TurnControlError) as ordinary_turn_blocked:
        seal_lifecycle_exit_slip(
            service.store.root,
            host_payload={
                "session_id": host_session_id,
                "cwd": str(source_repository),
            },
            reason="ORDINARY_TURN",
            visible_reason="Ordinary completion is not an Exit Slip.",
        )
    assert ordinary_turn_blocked.value.code == (
        "TURN_CONTROL_LIFECYCLE_EXIT_REASON_INVALID"
    )

    for insufficient_proof in (
        {"ephemeral": True},
        {
            "ephemeral": True,
            "stateless_invocation": True,
            "interaction_profile": "CODEX_APP_INTERACTIVE",
        },
    ):
        with pytest.raises(TurnControlError) as stateless_blocked:
            seal_lifecycle_exit_slip(
                service.store.root,
                host_payload={
                    "session_id": host_session_id,
                    "cwd": str(source_repository),
                    "runtime_context": insufficient_proof,
                },
                reason="STATELESS_EPHEMERAL_END",
                visible_reason="Insufficient stateless exit proof.",
            )
        assert stateless_blocked.value.code == (
            "TURN_CONTROL_STATELESS_EPHEMERAL_PROOF_REQUIRED"
        )

    resumed = session_start_control(
        service.store.root,
        host_payload={
            "session_id": host_session_id,
            "cwd": str(source_repository),
            "source": "resume",
        },
    )
    assert resumed["state"] == "BOUND_NO_UNCOMMITTED_TURNS"
    assert resumed["persistent_plan_row"]["task_id"] == "turn-control-row"
    assert resumed["persistent_plan_row"]["position"] == 1

    lineage = ChatLineage(
        service.store.project_root("book-faires")
        / "lineage"
        / f"{session_id}.jsonl"
    ).events()
    lifecycle_events = [
        event
        for event in lineage
        if event["event_type"] == "turn.lifecycle_exit_slip"
    ]
    assert len(lifecycle_events) == len(boundary_reasons)
    assert {event["visible_payload"]["reason"] for event in lifecycle_events} == set(
        boundary_reasons
    )
    assert all(event["private_reasoning_stored"] is False for event in lifecycle_events)
    assert sealed["EXPLICIT_PAUSE"]["receipt"]["exit_slip_sha256"]


def test_row177_observed_experience_is_typed_replay_safe_and_pointer_neutral(
    service,
    source_repository: Path,
) -> None:
    session_id, host_session_id = _strict_state_travel_session(service)
    active_session = service.sessions.load("book-faires", session_id)
    active_session.state = SessionState.TASK_CLASSIFIED
    active_session.task = {
        "task_id": "turn-control-runtime-task",
        "task_class": "modify_code",
        "requested_outcome": "Exercise the Row177 observation bridge.",
        "permitted_paths": ["tests/**"],
        "permitted_tools": ["repository_read", "test"],
        "acceptance_checks": ["Observation packets remain pointer-neutral."],
        "stop_condition": "Stop after Row177 proof.",
    }
    active_session.metadata["run_id"] = "run-row177-observation-test"
    service.sessions._save(active_session)
    pointer_before = service.store.pointer("book-faires").as_dict()
    live_plan = service.store.backlog_status("book-faires")["goal_projection"]
    live_active = next(
        row
        for row in live_plan["rows"]
        if row["status"] == "in_progress" and row["lifecycle_status"] == "ACTIVE"
    )
    plan_steer_payload = {
        "event_subject": "Bounded Plan-changing steer",
        "before_plan": {
            "canonical_plan_sha256": "A" * 64,
            "active_row": 1,
        },
        "after_plan": {
            "canonical_plan_sha256": live_plan["canonical_plan_sha256"],
            "active_row": live_active["number"],
            "active_task_id": live_active["task_id"],
        },
        "linked_delta_ids": ["DELTA-ROW177-ONE", "DELTA-ROW177-TWO"],
        "visible_detail": "access_token=secret-plan-steer-value",
    }
    sealed = service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="steer",
        visible_payload=plan_steer_payload,
        event_id="row177-plan-changing-steer",
    )
    observation = sealed["observed_experience"]
    packet = validate_observed_experience_packet(
        observation["packet"], expected_project_id="book-faires"
    )
    assert observation["state"] == "SEALED"
    assert packet["classification"]["classification"] == "PLAN_CHANGING_STEER"
    assert packet["classification"]["plan_change_proven"] is True
    assert packet["classification"]["future_learning_candidate_eligibility"] == (
        "CANDIDATE_EVIDENCE_ONLY_NOT_ACCEPTED"
    )
    assert packet["classification"]["learning_accepted"] is False
    assert packet["source_event"]["raw_visible_payload_copied"] is False
    assert "secret-plan-steer-value" not in json.dumps(packet, sort_keys=True)
    assert packet["authority_effects"] == {
        "project_truth": "NONE",
        "canon_input": "NONE",
        "agent_learning": "NONE",
        "chat_lineage": "HASHED_VISIBLE_SOURCE_EVENT_ONLY",
    }

    replay = service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="steer",
        visible_payload=plan_steer_payload,
        event_id="row177-plan-changing-steer",
    )
    assert replay["observed_experience"]["state"] == "SEALED_IDEMPOTENT_REUSE"
    assert replay["observed_experience"]["packet"] == packet

    minor = service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="steer",
        visible_payload={"event_subject": "Question without a Plan transition"},
        event_id="row177-minor-steer",
    )["observed_experience"]["packet"]
    assert minor["classification"]["classification"] == "ORDINARY_OR_MINOR_STEER"
    assert minor["classification"]["plan_change_proven"] is False
    assert minor["classification"]["future_learning_candidate_eligibility"] == (
        "EXCLUDED_NO_PLAN_CHANGE_PROOF"
    )

    prompt_payload = _host_shaped_user_prompt_submit_payload(
        host_session_id=host_session_id,
        turn_id="row177-ordinary-commit",
        cwd=source_repository,
        prompt="Answer this ordinary governed question.",
    )
    prepare_turn(service.store.root, host_payload=prompt_payload)
    committed = commit_turn(
        service.store.root,
        host_payload={
            **prompt_payload,
            "last_assistant_message": "This is an ordinary visible response.",
        },
    )
    ordinary = committed["observed_experience"]["packet"]
    assert ordinary["classification"]["classification"] == (
        "ORDINARY_VISIBLE_REQUEST_COMMIT"
    )
    assert ordinary["classification"]["future_learning_candidate_eligibility"] == (
        "NOT_CLASSIFIED_AS_PLAN_STEER"
    )
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    with pytest.raises(EvidenceLaneError) as cross_project:
        validate_observed_experience_packet(
            packet, expected_project_id="another-project"
        )
    assert cross_project.value.code == "CANON_RUNTIME_CROSS_PROJECT_ROUTE_DENIED"

    with pytest.raises(EvidenceLaneError) as stale_generation:
        seal_observed_experience_packet(
            service.store.project_root("book-faires"),
            project_id="book-faires",
            evidence_session_id=session_id,
            runtime_task_id=str(packet["runtime_task_id"]),
            plan_task_id=str(packet["plan_task_id"]),
            active_plan=dict(packet["active_plan"]),
            event=sealed["event"],
            expected_accepted_pv=str(pointer_before["accepted_pv"]),
            expected_pointer_generation=int(pointer_before["generation"]) + 1,
            input_kind="steer",
        )
    assert stale_generation.value.code == "CANON_RUNTIME_POINTER_STALE"
    assert service.store.pointer("book-faires").as_dict() == pointer_before


def test_row177_host_exit_locator_routes_by_durability_without_truth_promotion(
    service,
    source_repository: Path,
) -> None:
    session_id, host_session_id = _strict_state_travel_session(service)
    pointer_before = service.store.pointer("book-faires").as_dict()
    durable_exit = seal_lifecycle_exit_slip(
        service.store.root,
        host_payload={
            "session_id": host_session_id,
            "turn_id": "row177-stateless-exit",
            "cwd": str(source_repository),
            "runtime_context": {
                "ephemeral": True,
                "stateless_invocation": True,
                "interaction_profile": "HEADLESS_API",
            },
        },
        reason="STATELESS_EPHEMERAL_END",
        visible_reason="Seal the bounded stateless host exit.",
    )
    assert durable_exit["host_exit_continuity"]["state"] == (
        "NOT_REQUIRED_DURABLE_LOCAL_AUTHORITY"
    )
    assert durable_exit["host_exit_continuity"]["packet"] is None

    lineage_event = next(
        event
        for event in ChatLineage(
            service.store.project_root("book-faires")
            / "lineage"
            / f"{session_id}.jsonl"
        ).events()
        if event["event_sha256"] == durable_exit["lineage_event_sha256"]
    )
    exit_slip = durable_exit["receipt"]
    active_plan = {
        "position": exit_slip["active_row"],
        "task_id": exit_slip["plan_task_id"],
        "status": "in_progress",
        "lifecycle_status": "ACTIVE",
    }
    ephemeral_route = route_persistence(
        "CODEX_VM",
        ephemeral=True,
        server_has_durable_filesystem=False,
        runtime_context={"interaction_profile": "HEADLESS_API"},
    ).as_dict()
    sealed = seal_host_exit_continuity_packet(
        service.store.project_root("book-faires"),
        project_id="book-faires",
        evidence_session_id=session_id,
        runtime_task_id=str(exit_slip["task_id"]),
        plan_task_id=str(exit_slip["plan_task_id"]),
        active_plan=active_plan,
        event=lineage_event,
        exit_slip=exit_slip,
        persistence_route=ephemeral_route,
        expected_accepted_pv=str(pointer_before["accepted_pv"]),
        expected_pointer_generation=int(pointer_before["generation"]),
    )
    packet = validate_host_exit_continuity_packet(
        sealed["packet"], expected_project_id="book-faires"
    )
    assert sealed["state"] == "SEALED"
    assert sealed["interaction_profile"] == "STATELESS_HEADLESS"
    assert packet["opaque_locator"].startswith("evi+host-exit://hexit_")
    assert packet["persistence"] == {
        "state": "AWAITING_LATER_DURABLE_CONNECTOR_PERSISTENCE",
        "durable_persisted": False,
        "storage_receipt_sha256": None,
        "exit_complete": False,
        "continuity_claimed": False,
        "consumer_owner": "INDEPENDENT_HOST_ENTRY_CONTINUITY_ROW",
    }
    assert packet["authority_effects"] == {
        "project_truth": "NONE",
        "canon_input": "NONE",
        "agent_learning": "NONE",
        "host_entry": "PENDING_LATER_EXACT_CONSUMER",
    }
    assert packet["pointer_moved"] is False
    assert packet["candidate_promoted"] is False
    assert packet["hil_inferred"] is False

    replay = seal_host_exit_continuity_packet(
        service.store.project_root("book-faires"),
        project_id="book-faires",
        evidence_session_id=session_id,
        runtime_task_id=str(exit_slip["task_id"]),
        plan_task_id=str(exit_slip["plan_task_id"]),
        active_plan=active_plan,
        event=lineage_event,
        exit_slip=exit_slip,
        persistence_route=ephemeral_route,
        expected_accepted_pv=str(pointer_before["accepted_pv"]),
        expected_pointer_generation=int(pointer_before["generation"]),
    )
    assert replay["state"] == "SEALED_IDEMPOTENT_REUSE"
    assert replay["packet"] == packet

    invalid_route = dict(ephemeral_route)
    invalid_route["mode"] = "local"
    with pytest.raises(EvidenceLaneError) as route_blocked:
        seal_host_exit_continuity_packet(
            service.store.project_root("book-faires"),
            project_id="book-faires",
            evidence_session_id=session_id,
            runtime_task_id=str(exit_slip["task_id"]),
            plan_task_id=str(exit_slip["plan_task_id"]),
            active_plan=active_plan,
            event=lineage_event,
            exit_slip=exit_slip,
            persistence_route=invalid_route,
            expected_accepted_pv=str(pointer_before["accepted_pv"]),
            expected_pointer_generation=int(pointer_before["generation"]),
        )
    assert route_blocked.value.code == "HOST_EXIT_CONTINUITY_ROUTE_INVALID"
    assert service.store.pointer("book-faires").as_dict() == pointer_before


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
            host_payload=_host_shaped_user_prompt_submit_payload(
                host_session_id="colliding-or-stale-host",
                turn_id="turn-stale-host",
                cwd=source_repository,
                prompt="Do not inherit the exact writer binding.",
            ),
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
        "hook_event_name": "UserPromptSubmit",
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
        "plugin": {
            "plugin_id": "evidence-lane-plugin",
            "version": plugin_version,
        },
        "archive_sha256": "A" * 64,
        "activation": {
            "state": "INSTALLED_RESTART_REQUIRED",
            "plugin_add": {
                "pluginId": "evidence-lane-plugin@test-exact-task",
                "version": plugin_version,
                "installedPath": str(
                    repository_root / "plugins" / "evidence-lane-plugin"
                ),
            },
        },
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

    exact_binding = seal_exact_task_project_session_binding(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        expected_active_task_id="turn-control-row",
    )
    exact_binding_body = {
        key: value
        for key, value in exact_binding.items()
        if key != "receipt_sha256"
    }
    assert exact_binding["status"] == "PASS"
    assert exact_binding["codex_thread_id"] == observed_task_id
    assert exact_binding["task_uri"] == f"codex://threads/{observed_task_id}"
    assert exact_binding["project_id"] == "book-faires"
    assert exact_binding["evidence_session_id"] == session_id
    assert exact_binding["governed_host_session_id"] == governed_host_session_id
    assert exact_binding["active_plan_row"]["task_id"] == "turn-control-row"
    assert exact_binding["active_plan_row"]["status"] == "in_progress"
    assert exact_binding["accepted_pointer"] == {
        "accepted_pv": "PV1",
        "generation": 1,
        "manifest_sha256": exact_binding["accepted_pointer"]["manifest_sha256"],
        "package_sha256": exact_binding["accepted_pointer"]["package_sha256"],
    }
    assert exact_binding["running_plugin"]["plugin_version"] == plugin_version
    assert exact_binding["task_title_used"] is False
    assert exact_binding["cwd_used"] is False
    assert exact_binding["task_binding_receipt_sha256"] == task_binding_sha256
    assert exact_binding["receipt_sha256"] == hashlib.sha256(
        (
            json.dumps(
                exact_binding_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()

    duplicate_path = binding_path.with_name(
        "019fedc7-cb86-7b40-94ce-1784a999f12c.json"
    )
    duplicate_path.write_text(
        json.dumps(task_binding, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TurnControlError) as ambiguous:
        seal_exact_task_project_session_binding(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            expected_active_task_id="turn-control-row",
        )
    assert ambiguous.value.code == "CODEX_EXACT_BINDING_AMBIGUOUS_OR_MISSING"
    duplicate_path.unlink()

    original_binding_bytes = binding_path.read_bytes()
    cross_project = {**task_binding, "project_id": "another-project"}
    binding_path.write_text(
        json.dumps(cross_project, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TurnControlError) as cross_project_blocked:
        seal_exact_task_project_session_binding(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            expected_active_task_id="turn-control-row",
        )
    assert (
        cross_project_blocked.value.code
        == "CODEX_EXACT_BINDING_AMBIGUOUS_OR_MISSING"
    )
    binding_path.write_bytes(original_binding_bytes)

    wrong_version = {**task_binding, "plugin_version": "1.9.9"}
    binding_path.write_text(
        json.dumps(wrong_version, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TurnControlError) as wrong_version_blocked:
        seal_exact_task_project_session_binding(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            expected_active_task_id="turn-control-row",
        )
    assert wrong_version_blocked.value.code == "TURN_CONTROL_CODEX_TASK_BINDING_DRIFT"
    binding_path.write_bytes(original_binding_bytes)

    title_only_path = binding_path.with_name("title-only.json")
    binding_path.rename(binding_path.with_suffix(".bak"))
    title_only_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.codex-title-only-binding.v1",
                "project_id": "book-faires",
                "evidence_session_id": session_id,
                "governed_host_session_id": governed_host_session_id,
                "task_title": "Codex Evidence Lane plugin statetravel task 2",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TurnControlError) as title_only_blocked:
        seal_exact_task_project_session_binding(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            expected_active_task_id="turn-control-row",
        )
    assert title_only_blocked.value.code == "CODEX_EXACT_BINDING_AMBIGUOUS_OR_MISSING"
    title_only_path.unlink()
    binding_path.with_suffix(".bak").rename(binding_path)

    pointer_path = service.store.project_root("book-faires") / "active_pointer.json"
    original_pointer_bytes = pointer_path.read_bytes()
    stale_pointer = json.loads(original_pointer_bytes)
    stale_pointer["generation"] += 1
    pointer_path.write_text(
        json.dumps(stale_pointer, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TurnControlError) as stale_blocked:
        seal_exact_task_project_session_binding(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            expected_active_task_id="turn-control-row",
        )
    assert stale_blocked.value.code == "TURN_CONTROL_ENTRY_POINTER_MISMATCH"
    pointer_path.write_bytes(original_pointer_bytes)

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


def test_native_user_prompt_hook_derives_steer_and_rejects_goal_control(
    service,
    source_repository: Path,
) -> None:
    _, host_session_id = _strict_state_travel_session(service)
    prompt_hook = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "prompt_submit.py"
    )
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)

    def invoke(
        *,
        turn_id: str,
        prompt: str,
        expected_continue: bool = True,
        **caller_claims: object,
    ) -> dict[str, object]:
        process = subprocess.run(
            [sys.executable, str(prompt_hook)],
            input=json.dumps(
                {
                    "session_id": host_session_id,
                    "turn_id": turn_id,
                    "cwd": str(source_repository),
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": prompt,
                    **caller_claims,
                }
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        payload = json.loads(process.stdout)
        assert payload["continue"] is expected_continue
        return _hook_context_json(payload, "EVIDENCE_LANE_PROMPT_ENTRY=")

    initial = invoke(
        turn_id="shared-native-turn",
        prompt="Start the governed implementation.",
        source="steer",
        is_steer=True,
    )
    assert initial["input_kind"] == "user_prompt"
    assert initial["retrieval_outcome"] == "PENDING_NATIVE_SKILL_QUERY"
    assert initial["retrieval_candidate_overlay_used"] is False
    assert initial["behavior_query_owner"] == "SKILL"
    assert initial["hook_lookup_performed"] is False
    assert initial["native_behavior_query_required"] is True
    assert initial["native_behavior_query_satisfied"] is False
    assert initial["required_native_read_sequence"] == [
        "pv_status",
        "pv_task_backlog",
        "pv_query",
    ]
    assert initial["host_plan_refresh_owner"] == "SKILL"
    assert initial["host_plan_tool"] == "update_plan"
    assert initial["capture_dispatch"]["classification_basis"] == (
        "FIRST_SEALED_INPUT_FOR_HOST_TURN"
    )
    assert initial["capture_dispatch"]["state"] == (
        "USERPROMPTSUBMIT_ADAPTER_INVOKED"
    )
    assert initial["capture_dispatch"]["adapter_invocation_observed"] is True
    assert initial["capture_dispatch"][
        "installed_host_dispatch_independently_proven"
    ] is False
    assert initial["capture_dispatch"][
        "independent_installed_host_proof_required"
    ] is True
    assert initial["capture_dispatch"]["pre_reasoning_proof_basis"] == (
        "VALIDATED_USERPROMPTSUBMIT_HOST_PAYLOAD_AND_ADAPTER_INVOCATION"
    )

    steer = invoke(
        turn_id="shared-native-turn",
        prompt="Keep PREPARE separate from the Goal panel refresh.",
        source="user_prompt",
    )
    assert steer["input_kind"] == "steer"
    assert steer["capture_dispatch"]["surface"] == "MID_GOAL_STEER"
    assert steer["capture_dispatch"]["classification_basis"] == (
        "PRIOR_SEALED_INPUT_FOR_SAME_HOST_TURN"
    )
    assert steer["pre_reasoning_host_dispatch_proven"] is True
    assert steer["retrieval_outcome"] == "PENDING_NATIVE_SKILL_QUERY"
    assert steer["hook_lookup_performed"] is False
    assert steer["native_behavior_query_satisfied"] is False
    assert len(str(steer["retrieval_receipt_sha256"])) == 64

    replay = invoke(
        turn_id="shared-native-turn",
        prompt="Keep PREPARE separate from the Goal panel refresh.",
        source="goal",
        is_goal=True,
    )
    assert replay["state"] == "PREPARED_IDEMPOTENT_REUSE"
    assert replay["input_kind"] == "steer"
    assert replay["control_record_sha256"] == steer["control_record_sha256"]
    assert replay["retrieval_receipt_sha256"] == steer["retrieval_receipt_sha256"]

    goal = invoke(
        turn_id="native-goal-continuation-turn",
        prompt=(
            '<codex_internal_context source="goal">\n'
            "Continue the persisted Goal from its active Plan row."
        ),
        source="steer",
        is_steer=True,
        expected_continue=False,
    )
    assert goal["state"] == "TURN_CONTROL_GAP"
    assert goal["code"] == "TURN_CONTROL_GOAL_PRE_REASONING_HOOK_UNAVAILABLE"
    assert goal["fail_closed"] is True
    assert goal["source_mutation_authorized"] is False

    records = PromptIndex(service.store.root)._all_records()
    assert [row["input_kind"] for row in records] == [
        "user_prompt",
        "steer",
    ]
    assert [row["prompt_index"] for row in records] == [1, 2]

    with pytest.raises(TurnControlError) as invalid_dispatch:
        prepare_turn(
            service.store.root,
            host_payload={
                "session_id": host_session_id,
                "turn_id": "invalid-native-dispatch-turn",
                "cwd": str(source_repository),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "This must fail before reasoning.",
                "evidence_lane_capture_dispatch": {
                    "surface": "PENDING_VISIBLE_USER_INPUT",
                    "host_route": "after-model-dispatch",
                    "native_hook_event": "UserPromptSubmit",
                    "host_payload_hook_event_name": "UserPromptSubmit",
                    "adapter_invocation_observed": True,
                    "installed_host_dispatch_independently_proven": False,
                    "input_kind_derived_from_sealed_state": True,
                    "caller_input_kind_authority": False,
                },
            },
        )
    assert invalid_dispatch.value.code == (
        "TURN_CONTROL_NATIVE_DISPATCH_RECEIPT_INVALID"
    )

    invalid_independent_claim = _host_shaped_user_prompt_submit_payload(
        host_session_id=host_session_id,
        turn_id="invalid-independent-host-proof-turn",
        cwd=source_repository,
        prompt="The adapter must not self-assert independent host proof.",
    )
    invalid_independent_claim["evidence_lane_capture_dispatch"] = {
        **dict(invalid_independent_claim["evidence_lane_capture_dispatch"]),
        "installed_host_dispatch_independently_proven": True,
    }
    with pytest.raises(TurnControlError) as invalid_independent:
        prepare_turn(
            service.store.root,
            host_payload=invalid_independent_claim,
        )
    assert invalid_independent.value.code == (
        "TURN_CONTROL_NATIVE_DISPATCH_RECEIPT_INVALID"
    )


def test_native_hooks_ignore_plugin_private_data_and_use_durable_user_authority(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    user_home = tmp_path / "codex-user"
    durable_root = user_home / "EvidenceLanePV"
    plugin_private_root = tmp_path / "codex-plugin-private-data"
    application = EvidenceLaneService(data_root=durable_root)
    registered = application.register_project(
        project_id="book-faires",
        display_name="Book Faires",
        repository_path=str(source_repository),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    assert registered["status"] == "PASS"
    session_id, host_session_id = _strict_state_travel_session(application)
    prompt_hook = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "evidence-lane-plugin"
        / "hooks"
        / "prompt_submit.py"
    )
    environment = os.environ.copy()
    environment.pop("EVIDENCE_LANE_DATA_ROOT", None)
    environment["PLUGIN_DATA"] = str(plugin_private_root)
    environment["CLAUDE_PLUGIN_DATA"] = str(plugin_private_root)
    environment["HOME"] = str(user_home)
    environment["USERPROFILE"] = str(user_home)

    assert resolve_codex_hook_store_root(
        environment={"PLUGIN_DATA": str(plugin_private_root)},
        home=user_home,
    ) == durable_root.resolve()
    process = subprocess.run(
        [sys.executable, str(prompt_hook)],
        input=json.dumps(
            {
                "session_id": host_session_id,
                "turn_id": "host-plugin-data-shadow-regression",
                "cwd": str(source_repository),
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Capture this visible Row164 host-shaped probe once.",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    payload = json.loads(process.stdout)
    prepared = _hook_context_json(payload, "EVIDENCE_LANE_PROMPT_ENTRY=")
    assert payload["continue"] is True
    assert prepared["state"] == "PREPARED_NOT_COMMITTED"
    assert prepared["prompt_index"] == 1
    assert application.prompt_index_status("book-faires", session_id)[
        "total_resolvable"
    ] == 1
    assert not plugin_private_root.exists()


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
    assert projected_notice["package_change_status"]["hooks"]["count"] == 8
    assert projected_notice["package_change_status"]["hooks"][
        "count_semantics"
    ] == "REGISTERED_EVENT_COUNT"
    assert projected_notice["package_change_status"]["hooks"][
        "hook_file_count"
    ] == 9
    projected_context = projected_payload["hookSpecificOutput"]["additionalContext"]
    assert "EVIDENCE_LANE_HOST_STEP_TASK_LIST_PROJECTION=" not in projected_context
    assert "EVIDENCE_LANE_HOST_PLAN_ACTION=" not in projected_context
    assert "update_plan" not in projected_context
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
                    "hook_event_name": "UserPromptSubmit",
                    # Caller claims are ignored; each new turn starts as a
                    # prompt even though turn/steer uses this same hook.
                    "source": "steer" if prompt_index == 2 else "user_prompt",
                    "is_steer": prompt_index == 2,
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
        assert prepared["input_kind"] == "user_prompt"
        assert prepared["pre_reasoning_host_dispatch_proven"] is True
        assert prepared["capture_dispatch"]["state"] == (
            "USERPROMPTSUBMIT_ADAPTER_INVOKED"
        )
        assert prepared["capture_dispatch"][
            "installed_host_dispatch_independently_proven"
        ] is False
        assert prepared["capture_dispatch"]["caller_input_kind_authority"] is False
        assert prepared["capture_dispatch"]["classification_basis"] == (
            "FIRST_SEALED_INPUT_FOR_HOST_TURN"
        )
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
        assert prepared_notice["package_change_status"]["hooks"]["count"] == 8
        assert prepared_notice["package_change_status"]["hooks"][
            "hook_file_count"
        ] == 9
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
    resumed_context = resumed_payload["hookSpecificOutput"]["additionalContext"]
    assert "EVIDENCE_LANE_HOST_STEP_TASK_LIST_PROJECTION=" not in resumed_context
    assert "EVIDENCE_LANE_HOST_PLAN_ACTION=" not in resumed_context
    session_control = _hook_context_json(
        resumed_payload,
        "CODEX_TURN_CONTROL_ENVELOPE=",
    )
    assert session_control["host_plan_behavior_owner"] == (
        "ACTIVE_EVIDENCE_LANE_SKILL"
    )
    assert session_control["hook_performed_host_update_plan"] is False
    rehydration = session_control["host_plan_rehydration"]
    assert rehydration["receipt"]["action"] == (
        "CALL_HOST_UPDATE_PLAN_EXACTLY_ONCE_FOR_THIS_TRIGGER"
    )
    assert rehydration["receipt"][
        "native_runtime_invoked_host_update_plan"
    ] is False
    assert rehydration["receipt"]["host_plan_acceptance_status"] == (
        "PENDING_EXPLICIT_HOST_ACCEPTANCE"
    )
    assert rehydration["receipt"]["candidate_created"] is False
    assert rehydration["receipt"]["pointer_moved"] is False
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
                "hook_event_name": "UserPromptSubmit",
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
            host_payload=_host_shaped_user_prompt_submit_payload(
                host_session_id=host_session_id,
                turn_id="turn-interrupted-prepare",
                cwd=source_repository,
                prompt="Prepare this bounded interrupted turn.",
            ),
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

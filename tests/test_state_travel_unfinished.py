from __future__ import annotations

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError


def _profile() -> dict[str, str]:
    return {
        "model": "gpt-5.6",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
        "service_tier": "standard",
    }


def test_state_travel_preserves_unaccepted_candidate_and_exact_resume_row(
    service,
) -> None:
    profile = _profile()
    boot = service.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="sole-writer",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"execution_profile": profile},
        host_session_id="origin-codex-task",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    candidate = service.build_initial("book-faires", session_id)["candidate"]
    task_rows = [
        {"number": 1, "step": "Inspect source", "status": "COMPLETED"},
        {"number": 2, "step": "Present exact PV1 HIL", "status": "IN_PROGRESS"},
        {"number": 3, "step": "Fuse only after APPROVE", "status": "PENDING"},
    ]
    resume_contract = {
        "task_list": task_rows,
        "resume_step": 2,
        "additive_deltas": [
            {
                "delta_id": "STEER_001",
                "text": "Keep the task panel visible through the next HIL.",
                "linked_step": 2,
            }
        ],
        "execution_profile": profile,
    }
    prepared = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract=resume_contract,
    )
    handoff = prepared["state_travel"]
    assert handoff["schema"] == "evidence-lane.state-travel.v2"
    assert handoff["travel_mode"] == "UNFINISHED_VERIFIED_WORK"
    assert handoff["accepted_pv"] is None
    assert handoff["candidate_snapshot"]["candidate_id"] == candidate["candidate_id"]
    assert handoff["resume_contract"]["resume_step"] == 2
    assert handoff["resume_contract"]["task_list"] == [
        {**row, "task_id": f"STEP_{row['number']:03d}"} for row in task_rows
    ]
    assert handoff["resume_contract"]["collaboration_law"] == {
        "writer_policy": "SOLE_WRITER",
        "entry_recovery_subagents": "READ_ONLY_ONLY",
        "later_subagents": "EXPLICIT_USER_COMMAND_ONLY",
    }

    wrong_profile = {**profile, "reasoning_effort": "medium"}
    with pytest.raises(EvidenceLaneError) as mismatch:
        service.resume_state_travel(
            project_id="book-faires",
            session_id=session_id,
            handoff_id=handoff["handoff_id"],
            host="CODEX_DESKTOP",
            host_session_id="wrong-profile-task",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={"execution_profile": wrong_profile},
        )
    assert mismatch.value.code == "STATE_TRAVEL_EXECUTION_PROFILE_MISMATCH"
    assert (
        service.sessions.load("book-faires", session_id).metadata[
            "current_host_session_id"
        ]
        == "origin-codex-task"
    )

    traveled = service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="matching-profile-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"execution_profile": profile},
    )
    assert traveled["next_action"] == "RESUME_EXACT_UNFINISHED_STEP"
    assert traveled["continuation_ready"] is True
    assert traveled["next_action_contract"]["stop_and_wait"] is False
    assert traveled["session"]["state"] == "PV1_CANDIDATE"
    assert traveled["session"]["candidate_id"] == candidate["candidate_id"]
    assert traveled["pointer"]["accepted_pv"] is None
    assert traveled["state_travel"]["live_source_verified"] is True
    assert traveled["state_travel"]["execution_profile_verified"] is True
    assert traveled["entry"]["resume_step"] == 2

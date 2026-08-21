from __future__ import annotations


def _task(task_id: str, outcome: str) -> dict:
    return {
        "task_id": task_id,
        "task_class": "verify_result",
        "requested_outcome": outcome,
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["Verify the bounded result."],
        "stop_condition": "Stop at the next governed HIL.",
    }


def test_codex_plan_mode_bridge_and_canonical_steer_classification(service) -> None:
    tasks = [
        _task("step-001", "Verify the first result."),
        _task("step-002", "Verify the second result."),
    ]
    reminder = service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-mode-reminder",
        host_kind="CODEX_DESKTOP",
        host_mode="DEFAULT",
    )
    assert reminder["status"] == "PLAN_MODE_REQUIRED"
    assert reminder["suggested_next_prompt"] == "/pl"
    assert service.task_backlog("book-faires")["tasks"] == []

    planned = service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-mode-canonical",
        host_kind="CODEX_DESKTOP",
        host_mode="PLAN",
    )
    goal = planned["goal_projection"]
    assert goal["canonical_authority"] == "PLAN_LANE"
    assert goal["task_count"] == 2
    assert goal["persistent_until"] == "NEXT_SIX_WAY_HIL_PRESENTED"
    assert goal["host_projections"]["CODEX"]["plan_mode_shortcut"] == "/pl"
    assert planned["host_plan_bridge"]["copy_paste_required"] is True
    assert "as this Codex task's Goal" in goal["goal_start_prompt"]

    linked = service.record_steer_delta(
        "book-faires",
        delta_text="Also verify the exact source hash.",
        actor="human-test",
        delta_id="steer-linked-001",
        linked_task_id="step-002",
    )
    assert linked["task_count"] == 2
    assert linked["task_count_changed"] is False
    assert linked["steer"]["boundary"] == "BEFORE_NEXT_HIL"
    assert linked["steer"]["classification"] == "LINKED_EXISTING_STEP"
    assert linked["event"]["event_type"] == "STEER_DELTA_LINKED"

    replay = service.record_steer_delta(
        "book-faires",
        delta_text="Also verify the exact source hash.",
        actor="human-test",
        delta_id="steer-linked-001",
        linked_task_id="step-002",
    )
    assert replay["idempotent_reuse"] is True
    assert replay["task_count"] == 2

    appended = service.record_steer_delta(
        "book-faires",
        delta_text="Publish a separate host-boundary explanation.",
        actor="human-test",
        delta_id="steer-new-001",
        new_task_contract=_task(
            "step-003",
            "Publish a separate host-boundary explanation.",
        ),
    )
    assert appended["task_count"] == 3
    assert appended["task_count_changed"] is True
    assert appended["steer"]["classification"] == "NEW_STEP"
    assert appended["event"]["event_type"] == "STEER_DELTA_NEW_STEP"
    assert [row["number"] for row in appended["backlog"]["goal_projection"]["rows"]] == [
        1,
        2,
        3,
    ]


def test_chatgpt_plan_lane_uses_persistent_plugin_store_without_codex_ui(service) -> None:
    planned = service.plan_tasks(
        "book-faires",
        tasks=[_task("chatgpt-step-001", "Persist one ChatGPT lane task.")],
        planned_by="human-test",
        plan_id="chatgpt-plan-001",
        host_kind="CHATGPT_WORK",
        host_mode=None,
    )
    assert planned["status"] == "PASS"
    bridge = planned["host_plan_bridge"]
    assert bridge["copy_paste_required"] is False
    assert bridge["chatgpt_mounted_plugin_store_is_authority"] is True
    chatgpt = planned["goal_projection"]["host_projections"]["CHATGPT"]
    assert chatgpt["native_plan_mode"] is False
    assert chatgpt["native_goal"] is False
    assert chatgpt["native_task_panel"] is False
    assert chatgpt["append_only_lane_law_preserved"] is True

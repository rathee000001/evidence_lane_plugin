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


def test_chatgpt_plan_rows_are_parked_outside_the_codex_goal(service) -> None:
    deferred = service.plan_tasks(
        "book-faires",
        tasks=[_task("chatgpt-step-001", "Persist one ChatGPT lane task.")],
        planned_by="human-test",
        plan_id="chatgpt-plan-001",
        host_kind="CHATGPT_WORK",
        host_mode=None,
    )
    assert deferred["status"] == "HOST_DEFERRED"
    assert deferred["plan_persisted"] is False
    assert deferred["parked_scope"] == "CHATGPT_PLUGIN_LAYER"
    backlog = service.task_backlog("book-faires")
    assert backlog["tasks"] == []
    assert set(backlog["goal_projection"]["host_projections"]) == {"CODEX"}
    assert backlog["history_projection"]["parked_host_surfaces"] == [
        {
            "surface": "CHATGPT_PLUGIN_LAYER",
            "status": "DEFERRED_NON_EXECUTABLE",
            "reactivation_requires": "NEW_EXPLICIT_HUMAN_PLAN_AND_HIL",
        }
    ]


def test_unlinked_steers_insert_before_physically_final_hil_and_all_persist(
    service,
) -> None:
    final_hil = _task(
        "row-final-hil",
        "Present the physically final six-way HIL.",
    )
    final_hil["panel_role"] = "PHYSICALLY_FINAL_HIL"
    service.plan_tasks(
        "book-faires",
        tasks=[
            _task("row-active-work", "Finish the current governed correction."),
            final_hil,
        ],
        planned_by="human-test",
        plan_id="plan-with-final-hil",
    )

    for index in range(1, 8):
        task_id = f"late-correction-{index:03d}"
        service.record_steer_delta(
            "book-faires",
            delta_text=f"Preserve late correction {index} before the final HIL.",
            actor="human-test",
            delta_id=f"late-steer-{index:03d}",
            new_task_contract=_task(
                task_id,
                f"Implement late correction {index} before the final HIL.",
            ),
        )

    backlog = service.task_backlog("book-faires")
    ordered = backlog["goal_projection"]["rows"]
    assert [row["number"] for row in ordered] == list(range(1, 10))
    assert [row["task_id"] for row in ordered] == [
        "row-active-work",
        *(f"late-correction-{index:03d}" for index in range(1, 8)),
        "row-final-hil",
    ]
    assert ordered[-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert backlog["tasks"][-1]["task_id"] == "row-final-hil"
    assert backlog["goal_projection"]["unlinked_steer_policy"] == (
        "INSERT_NEW_NUMBERED_STEP_BEFORE_NEXT_HIL_AND_INCREASE_COUNT"
    )
    assert [
        row["steer_deltas"][0]["delta_id"]
        for row in ordered[1:-1]
    ] == [f"late-steer-{index:03d}" for index in range(1, 8)]

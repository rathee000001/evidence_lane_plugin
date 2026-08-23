from __future__ import annotations

import pytest
from evidence_lane_plugin.goal_usage import (
    GOAL_COMPLETION_COMMAND,
    RICH_GOAL_COMPLETION_METRICS_ROUTE,
    build_component_token_accounting,
    build_goal_completion_authorization,
    build_goal_usage_receipt,
    build_profile_observed_usage_context,
    build_rich_goal_completion_metrics_receipt,
    compact_duration,
    compact_token_count,
    compact_token_count_receipt,
)


def test_compact_token_count_uses_readable_k_m_and_b_notation() -> None:
    assert compact_token_count(999) == "999"
    assert compact_token_count(256_000) == "256K"
    assert compact_token_count(665_603) == "665.6K"
    assert compact_token_count(1_500_000) == "1.5M"
    assert compact_token_count(14_007_602) == "14M"
    assert compact_token_count(1_451_900_000) == "1.5B"
    assert compact_token_count(1_451_900_000, decimal_places=4) == "1.4519B"
    assert compact_token_count_receipt(1_451_900_000, decimal_places=4) == {
        "raw": 1_451_900_000,
        "display": "1.4519B",
        "suffix": "B",
        "divisor": 1_000_000_000,
        "decimal_places": 4,
        "rounding_rule": "ROUND_HALF_UP",
        "exact_raw_value_preserved": True,
    }


def test_legacy_goal_usage_route_is_a_non_executing_tombstone() -> None:
    tombstone = build_goal_usage_receipt(
        current_tokens=1_500_000,
        current_elapsed_seconds=3600,
    )
    assert tombstone["status"] == "OBSOLETE_ROUTE"
    assert tombstone["required_current_route"] == (
        RICH_GOAL_COMPLETION_METRICS_ROUTE
    )
    assert tombstone["legacy_execution_performed"] is False
    assert tombstone["fallback_allowed"] is False
    assert tombstone["mutation_performed"] is False
    assert tombstone["supplied_values_returned"] is False


def test_goal_completion_is_exact_human_only_and_has_two_dispositions() -> None:
    continued = build_goal_completion_authorization(
        visible_command=GOAL_COMPLETION_COMMAND,
        disposition="COMPLETE_THIS_TASK_AND_STATE_TRAVEL",
        actor_kind="HUMAN",
        current_task_id="task-current",
    )
    assert continued["current_task_goal_completed"] is True
    assert continued["state_travel_requested"] is True
    assert continued["successor_goal_required"] is True
    assert continued["full_goal_closed"] is False

    final = build_goal_completion_authorization(
        visible_command=GOAL_COMPLETION_COMMAND,
        disposition="COMPLETE_FULLY",
        actor_kind="HUMAN",
        current_task_id="task-current",
    )
    assert final["full_goal_closed"] is True
    assert final["state_travel_requested"] is False
    assert final["hil_can_complete_goal"] is False
    assert final["candidate_can_complete_goal"] is False
    assert final["automation_can_complete_goal"] is False
    assert final["task_transition_can_complete_goal"] is False
    assert final["pause_or_stall_can_complete_goal"] is False
    assert final["goal_completion_implies_hil_approval"] is False
    assert final["goal_completion_implies_pointer_move"] is False


@pytest.mark.parametrize(
    ("command", "disposition", "actor"),
    [
        ("APPROVE", "COMPLETE_FULLY", "HUMAN"),
        (GOAL_COMPLETION_COMMAND, "COMPLETE_FULLY", "AUTOMATION"),
        (GOAL_COMPLETION_COMMAND, "PAUSE", "HUMAN"),
    ],
)
def test_hil_automation_and_pause_cannot_complete_goal(
    command: str,
    disposition: str,
    actor: str,
) -> None:
    with pytest.raises(ValueError):
        build_goal_completion_authorization(
            visible_command=command,
            disposition=disposition,
            actor_kind=actor,
            current_task_id="task-current",
        )


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_goal_usage_rejects_invalid_accounting_values(value: object) -> None:
    with pytest.raises(ValueError):
        compact_token_count(value)  # type: ignore[arg-type]


def test_compact_duration_never_drops_seconds_when_they_are_material() -> None:
    assert compact_duration(0) == "0s"
    assert compact_duration(24 * 60 + 25) == "24m 25s"


def _binding() -> dict[str, str]:
    return {
        "project_id": "book-faires",
        "evidence_session_id": "session-001",
        "task_id": "task-001",
        "host_session_id_sha256": "A" * 64,
    }


def _rich_telemetry() -> dict[str, object]:
    return {
        "host_accounted_goal_tokens": 42_000,
        "host_accounting_formula": "HOST_EXPOSED_TEST_WEIGHTING",
        "raw_input_tokens": 100_000,
        "cached_input_tokens": 80_000,
        "uncached_input_tokens": 20_000,
        "output_tokens": 25_000,
        "reasoning_output_tokens": 5_000,
        "raw_input_output_total_tokens": 125_000,
        "elapsed_seconds": 3_661,
        "model_turn_starts": 12,
        "assistant_agent_messages": 31,
        "top_level_tool_calls": 18,
        "native_mcp_completions": 7,
        "patch_applications": 4,
        "web_search_completions": 2,
        "compactions": 3,
        "aborted_turns": 1,
        "unique_subagents": 2,
        "spawn_calls": 3,
        "subagent_lifecycle_counts": {
            "started": 2,
            "interacted": 4,
            "interrupted": 1,
        },
    }


def test_rich_goal_completion_metrics_are_exact_separate_and_subset_safe() -> None:
    receipt = build_rich_goal_completion_metrics_receipt(
        goal_id="goal-001",
        telemetry=_rich_telemetry(),
        provenance={"source": "codex-host-persisted-goal-telemetry"},
        binding=_binding(),
    )

    assert receipt["status"] == "PASS"
    assert receipt["route"] == RICH_GOAL_COMPLETION_METRICS_ROUTE
    assert receipt["host_accounting"]["goal_tokens"] == {
        "availability": "AVAILABLE",
        "raw": 42_000,
        "display": "42K",
        "suffix": "K",
        "divisor": 1_000,
        "decimal_places": 2,
        "rounding_rule": "ROUND_HALF_UP",
        "exact_raw_value_preserved": True,
    }
    assert receipt["host_accounting"][
        "kept_separate_from_raw_model_traffic"
    ] is True
    assert receipt["raw_model_traffic"]["raw_input_tokens"]["raw"] == 100_000
    assert receipt["raw_model_traffic"]["output_tokens"]["raw"] == 25_000
    assert receipt["raw_model_traffic"]["reasoning_output_tokens"]["raw"] == 5_000
    assert receipt["raw_model_traffic"]["raw_input_output_total_tokens"][
        "raw"
    ] == 125_000
    assert receipt["accounting_laws"]["reasoning_tokens_double_counted"] is False
    assert receipt["elapsed"] == {
        "availability": "AVAILABLE",
        "raw": 3_661,
        "display": "1h 1m 1s",
        "exact_raw_value_preserved": True,
    }
    assert receipt["activity_counts"]["native_mcp_completions"]["raw"] == 7
    assert receipt["subagent_lifecycle_counts"]["interacted"]["raw"] == 4
    assert receipt["missing_fields"] == []
    assert receipt["completion_state"]["completion_call_performed"] is False
    assert receipt["legacy_route"] == {
        "identifier": "build_goal_usage_receipt",
        "status": "OBSOLETE_ROUTE",
        "executable": False,
        "fallback_allowed": False,
        "required_current_route": RICH_GOAL_COMPLETION_METRICS_ROUTE,
    }


def test_completed_goal_reuses_persisted_rich_receipt_without_completion() -> None:
    persisted = build_rich_goal_completion_metrics_receipt(
        goal_id="goal-001",
        telemetry=_rich_telemetry(),
        provenance={"source": "codex-host-persisted-goal-telemetry"},
        binding=_binding(),
    )
    replay = build_rich_goal_completion_metrics_receipt(
        goal_id="goal-001",
        telemetry={},
        provenance={"source": "codex-host-persisted-goal-telemetry"},
        binding=_binding(),
        goal_already_complete=True,
        persisted_receipt=persisted,
    )

    assert replay == persisted
    assert replay["completion_state"]["completion_call_performed"] is False


def test_incomplete_rich_telemetry_reports_missing_fields_without_fallback() -> None:
    receipt = build_rich_goal_completion_metrics_receipt(
        goal_id="goal-002",
        telemetry={
            "raw_input_tokens": 100,
            "output_tokens": 25,
            "reasoning_output_tokens": 5,
        },
        provenance={"source": "partial-host-telemetry"},
        binding=_binding(),
        goal_already_complete=True,
    )

    assert receipt["status"] == "INCOMPLETE_TELEMETRY"
    assert receipt["raw_model_traffic"]["raw_input_output_total_tokens"][
        "raw"
    ] == 125
    assert receipt["host_accounting"]["conversion_or_weighting_formula"] == (
        "UNKNOWN_NOT_EXPOSED"
    )
    assert "host_accounted_goal_tokens" in receipt["missing_fields"]
    assert "host_accounting_formula" in receipt["missing_fields"]
    assert "persisted_completion_metrics_receipt" in receipt["missing_fields"]
    assert receipt["legacy_route"]["fallback_allowed"] is False
    assert receipt["completion_state"]["completion_call_performed"] is False


@pytest.mark.parametrize(
    "telemetry",
    [
        {"raw_input_tokens": 10, "cached_input_tokens": 11},
        {"output_tokens": 10, "reasoning_output_tokens": 11},
        {
            "raw_input_tokens": 10,
            "output_tokens": 2,
            "raw_input_output_total_tokens": 13,
        },
    ],
)
def test_rich_goal_metrics_reject_subset_or_total_mismatch(
    telemetry: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        build_rich_goal_completion_metrics_receipt(
            goal_id="goal-invalid",
            telemetry=telemetry,
            provenance={"source": "invalid-test-telemetry"},
            binding=_binding(),
        )


def test_component_accounting_preserves_raw_values_and_subset_semantics() -> None:
    receipt = build_component_token_accounting(
        components={
            "input_tokens": 1_000,
            "output_tokens": 250,
            "cached_input_tokens": 400,
            "reasoning_tokens": 100,
            "main_agent_tokens": "UNAVAILABLE",
            "subagent_tokens": "UNAVAILABLE",
        },
        provenance={"source": "codex-host-usage"},
        binding=_binding(),
    )
    assert receipt["components"]["cached_input_tokens"]["raw"] == 400
    assert receipt["components"]["reasoning_tokens"]["raw"] == 100
    assert receipt["components"]["subagent_tokens"]["availability"] == (
        "UNAVAILABLE"
    )
    assert receipt["final_aggregate"]["raw"] == 1_250
    assert receipt["final_aggregate"]["basis"] == (
        "SUM_NON_OVERLAPPING_INPUT_AND_OUTPUT"
    )
    assert receipt["cached_input_is_subset_of_input"] is True
    assert receipt["reasoning_is_subset_of_output"] is True


def test_component_accounting_never_presents_output_only_as_total() -> None:
    receipt = build_component_token_accounting(
        components={"output_tokens": 900},
        provenance={"source": "codex-host-usage"},
        binding=_binding(),
    )
    assert receipt["components"]["output_tokens"]["raw"] == 900
    assert receipt["final_aggregate"] == {
        "availability": "UNAVAILABLE",
        "raw": None,
        "display": "UNAVAILABLE",
        "basis": "UNAVAILABLE_INSUFFICIENT_NON_OVERLAPPING_COMPONENTS",
    }
    assert receipt["output_only_is_total"] is False


def test_component_accounting_does_not_double_count_agent_totals() -> None:
    receipt = build_component_token_accounting(
        components={
            "input_tokens": 1_000,
            "output_tokens": 250,
            "main_agent_tokens": 1_250,
            "subagent_tokens": 300,
        },
        provenance={"source": "codex-host-usage"},
        binding=_binding(),
    )
    assert receipt["final_aggregate"]["raw"] == 1_550
    assert receipt["final_aggregate"]["basis"] == (
        "SUM_NON_OVERLAPPING_MAIN_AGENT_AND_SUBAGENT_TOTALS"
    )

    host_total = build_component_token_accounting(
        components={
            "input_tokens": 1_000,
            "output_tokens": 250,
            "main_agent_tokens": 1_250,
            "subagent_tokens": 300,
        },
        total_tokens=1_550,
        provenance={"source": "codex-host-usage"},
        binding=_binding(),
    )
    assert host_total["final_aggregate"]["raw"] == 1_550
    assert host_total["final_aggregate"]["basis"] == "HOST_EXPOSED_FINAL_TOTAL"


def test_profile_observation_and_user_attribution_remain_separate() -> None:
    context = build_profile_observed_usage_context(
        observations=[
            {"observed_on": "2026-08-12", "raw": 902_300_000},
            {"observed_on": "2026-08-13", "raw": 549_600_000},
        ],
        user_exclusive_attribution=(
            "The governed task exclusively produced both displayed daily totals."
        ),
        binding=_binding(),
    )
    assert [row["display"] for row in context["observations"]] == [
        "902.3M",
        "549.6M",
    ]
    assert context["arithmetic_sum"]["raw"] == 1_451_900_000
    assert context["arithmetic_sum"]["display"] == "1.4519B"
    assert context["user_attestation"]["proven_by_profile_screenshots"] is False
    assert context["machine_telemetry"] == (
        "NOT_ESTABLISHED_BY_PROFILE_SCREENSHOTS"
    )
    assert context["binding"] == _binding()
    assert context["access_scope"] == "PROJECT_TASK_PRIVATE_ANALYSIS"
    assert context["cross_project_retrieval"] is False
    assert context["shared_global_telemetry"] is False
    assert context["public_output_included"] is False


def test_profile_observation_redacts_attribution_and_rejects_unbound_context() -> None:
    context = build_profile_observed_usage_context(
        observations=[{"observed_on": "2026-08-13", "raw": 549_600_000}],
        user_exclusive_attribution=(
            "This task produced the total; api_key=FAKE_TEST_SECRET_1234567890"
        ),
        binding=_binding(),
    )
    assert "FAKE_TEST_SECRET" not in context["user_attestation"]["statement"]
    assert context["user_attestation"]["statement"].endswith("[REDACTED]")

    with pytest.raises(ValueError, match="exact project/task/session/host binding"):
        build_profile_observed_usage_context(
            observations=[{"observed_on": "2026-08-13", "raw": 549_600_000}],
            user_exclusive_attribution="Bounded user attestation",
            binding={},
        )

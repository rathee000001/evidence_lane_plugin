from __future__ import annotations

import pytest
from evidence_lane_plugin import goal_usage
from evidence_lane_plugin.goal_usage import (
    GOAL_COMPLETION_COMMAND,
    RICH_GOAL_COMPLETION_METRICS_ROUTE,
    build_component_token_accounting,
    build_goal_completion_authorization,
    build_profile_observed_usage_context,
    build_reset_aware_epoch_accounting,
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


def test_compact_single_epoch_goal_usage_route_is_purged() -> None:
    assert not hasattr(goal_usage, "build_goal_usage_receipt")


def test_goal_completion_requests_do_not_attest_human_or_native_completion() -> None:
    continued = build_goal_completion_authorization(
        visible_command=GOAL_COMPLETION_COMMAND,
        disposition="COMPLETE_THIS_TASK_AND_HANDOFF_WORK",
        actor_kind="HUMAN",
        current_task_id="task-current",
    )
    assert continued["current_task_goal_completed"] is None
    assert continued["work_handoff_requested"] is True
    assert continued["successor_goal_required"] is True
    assert continued["full_goal_closed"] is None
    assert continued["full_goal_completion_requested"] is False

    final = build_goal_completion_authorization(
        visible_command=GOAL_COMPLETION_COMMAND,
        disposition="COMPLETE_FULLY",
        actor_kind="HUMAN",
        current_task_id="task-current",
    )
    assert final["full_goal_closed"] is None
    assert final["full_goal_completion_requested"] is True
    assert final["work_handoff_requested"] is False
    assert final["hil_can_complete_goal"] is False
    assert final["candidate_can_complete_goal"] is False
    assert final["automation_can_complete_goal"] is False
    assert final["task_transition_can_complete_goal"] is False
    assert final["pause_or_stall_can_complete_goal"] is False
    assert final["goal_completion_implies_hil_approval"] is False
    assert final["goal_completion_implies_pointer_move"] is False
    for request in (continued, final):
        assert request["schema"] == "evidence-lane.goal-completion-request.v4"
        assert request["status"] == "CALLER_REQUEST_VALIDATED"
        assert request["input_provenance"] == "CALLER_SUPPLIED"
        assert request["completion_requested"] is True
        assert request["human_authorization_verified"] is False
        assert request["current_task_identity_verified"] is False
        assert request["native_goal_state_verified"] is False
        assert request["completion_call_performed"] is False
        assert request["current_task_goal_completed"] is None


@pytest.mark.parametrize(("value", "places", "expected"), [
    (10_000, 0, "10K"), (100_000, 0, "100K"),
    (10_000_000, 0, "10M"), (100_000_000, 0, "100M"),
    (10_000_000_000, 0, "10B"), (100_000_000_000, 0, "100B"),
    (10_000, 1, "10K"), (10_500, 0, "11K"), (10_500, 2, "10.5K"),
])
def test_compact_display_preserves_integer_zeros_and_rounding(value, places, expected):
    receipt = compact_token_count_receipt(value, decimal_places=places)
    assert receipt["display"] == expected
    assert receipt["raw"] == value
    assert receipt["rounding_rule"] == "ROUND_HALF_UP"


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
        "cache_write_input_tokens": 0,
        "output_tokens": 25_000,
        "reasoning_output_tokens": 5_000,
        "raw_input_output_total_tokens": 125_000,
        "elapsed_seconds": 3_661,
        "model_turn_starts": 12,
        "assistant_agent_messages": 31,
        "top_level_tool_calls": 18,
        "execution_calls": 10,
        "native_mcp_completions": 7,
        "patch_applications": 4,
        "web_search_completions": 2,
        "compactions": 3,
        "aborted_turns": 1,
        "unique_subagents": 2,
        "spawn_calls": 3,
        "cumulative_token_samples": [
            {
                "timestamp": "2026-08-24T14:00:00Z",
                "input_tokens": 60_000,
                "cached_input_tokens": 40_000,
                "cache_write_input_tokens": 0,
                "output_tokens": 15_000,
                "reasoning_output_tokens": 3_000,
                "total_tokens": 75_000,
            },
            {
                "timestamp": "2026-08-25T14:00:00Z",
                "input_tokens": 10_000,
                "cached_input_tokens": 10_000,
                "cache_write_input_tokens": 0,
                "output_tokens": 2_000,
                "reasoning_output_tokens": 1_000,
                "total_tokens": 12_000,
            },
            {
                "timestamp": "2026-08-25T15:00:00Z",
                "input_tokens": 40_000,
                "cached_input_tokens": 40_000,
                "cache_write_input_tokens": 0,
                "output_tokens": 10_000,
                "reasoning_output_tokens": 2_000,
                "total_tokens": 50_000,
            },
        ],
        "daily_reconciliation_timezone": "America/New_York",
        "native_turn_evidence": {
            "returned_turn_count": 15,
            "in_progress_turns": 0,
            "populated_in_progress_turns": 0,
            "empty_in_progress_turns": 0,
            "context_compactions": 3,
            "model_turn_starts": 12,
            "aborted_turns": 1,
        },
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
    assert receipt["reset_aware_epoch_accounting"]["counter_reset_count"] == 1
    assert receipt["reset_aware_epoch_accounting"]["epoch_count"] == 2
    assert receipt["reset_aware_epoch_accounting"]["final_minus_initial_used"] is False
    assert receipt["native_turn_reconciliation"]["status"] == "PASS"
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
    assert receipt["completion_state"]["native_goal_state_verified"] is False
    assert receipt["completion_state"]["goal_completion_status_basis"] == "CALLER_SUPPLIED_METADATA"
    assert "legacy_route" not in receipt


def test_duration_authorities_are_separate_nullable_and_never_summed() -> None:
    telemetry = _rich_telemetry()
    telemetry.update(
        {
            "host_completed_time_used_seconds": 82_053,
            "user_confirmed_active_ui_runtime_seconds": 99_960,
            "goal_calendar_span_seconds": 150_016.691,
            "native_completed_turn_overlap_seconds": 81_950.691,
            "duration_observation": {
                "source_kind": "USER_CONFIRMED_ACTIVE_UI_RUNTIME",
                "runtime_seconds": 99_960,
                "observation_timestamp": None,
                "goal_recompleted": False,
                "main_token_segment_overwritten": False,
            },
            "non_execution_gaps": {
                "large_gap_seconds": 67_845,
                "small_gap_seconds": 221,
                "total_seconds": 68_066,
                "cause": "NON_EXECUTION_GAP_CAUSE_UNPROVEN",
            },
            "native_status_mismatches": [
                {
                    "native_status": "inProgress",
                    "rollout_status": "task_complete",
                }
            ],
            "hidden_overlay_truth": "STALE_INPROGRESS_VISIBLE",
            "first_complete_goal_receipt_selected": True,
        }
    )
    receipt = build_rich_goal_completion_metrics_receipt(
        goal_id="goal-duration-001",
        telemetry=telemetry,
        provenance={"source": "first-native-complete-goal-receipt"},
        binding=_binding(),
    )

    durations = receipt["duration_authorities"]
    assert durations["USER_CONFIRMED_ACTIVE_UI_RUNTIME"]["exact_seconds"] == 99_960
    assert durations["HOST_COMPLETED_TIME_USED"]["exact_seconds"] == 82_053
    assert durations["GOAL_CALENDAR_SPAN"]["exact_seconds"] == 150_016.691
    assert durations["NATIVE_COMPLETED_TURN_OVERLAP"]["exact_seconds"] == 81_950.691
    reconciliation = receipt["duration_reconciliation"]
    assert reconciliation["preferred_runtime_basis"] == (
        "USER_CONFIRMED_ACTIVE_UI_RUNTIME"
    )
    assert reconciliation["preferred_runtime_seconds"] == 99_960
    assert reconciliation["ui_minus_host_seconds"] == 17_907
    assert reconciliation["host_overhead_seconds"] == 102.309
    assert reconciliation["ui_host_wall_and_turn_overlap_summed"] is False
    assert reconciliation["duration_authorities_aliased"] is False
    assert reconciliation["active_ui_snapshot"]["availability"] == "UNAVAILABLE"
    assert reconciliation["non_execution_gaps"]["counted_as_active_runtime"] is False
    assert receipt["native_turn_reconciliation"][
        "started_minus_completed_inference_used"
    ] is False
    assert receipt["full_option_2_display_contract"][
        "post_append_closeout_tail_displayed_separately"
    ] is True


def test_active_ui_snapshot_is_client_presentation_not_admitted_runtime() -> None:
    telemetry = _rich_telemetry()
    telemetry["active_ui_snapshot"] = {
        "observed_at": "2026-08-27T00:00:00+00:00",
        "goal_status": "active",
        "time_used_seconds": 82_053,
        "updated_at": 1_787_780_000,
        "computed_active_display_seconds": 99_960,
    }
    receipt = build_rich_goal_completion_metrics_receipt(
        goal_id="goal-ui-001",
        telemetry=telemetry,
        provenance={"source": "native-active-goal-object"},
        binding=_binding(),
    )
    snapshot = receipt["duration_reconciliation"]["active_ui_snapshot"]
    assert snapshot["presentation_class"] == "CLIENT_EXTRAPOLATED_PRESENTATION"
    assert snapshot["admitted_to_duration_formula"] is False
    assert snapshot["formula"] == (
        "timeUsedSeconds + observation_timestamp - updatedAt"
    )


def test_reset_aware_goal_accounting_sums_positive_deltas_across_many_resets() -> None:
    samples = []
    for index, value in enumerate((10, 20, 3, 8, 1, 4)):
        samples.append(
            {
                "timestamp": f"2026-08-25T{index:02d}:00:00Z",
                "input_tokens": value,
                "cached_input_tokens": value // 2,
                "cache_write_input_tokens": 0,
                "output_tokens": value,
                "reasoning_output_tokens": value // 4,
                "total_tokens": value * 2,
            }
        )
    receipt = build_reset_aware_epoch_accounting(
        cumulative_samples=samples,
        timezone_name="America/New_York",
    )
    assert receipt["counter_reset_count"] == 2
    assert receipt["epoch_count"] == 3
    assert receipt["totals"]["input_tokens"] == 32
    assert receipt["totals"]["output_tokens"] == 32
    assert receipt["totals"]["total_tokens"] == 64
    assert receipt["final_minus_initial_used"] is False


def test_rich_goal_metrics_correction_supersedes_without_recompleting_goal() -> None:
    telemetry = _rich_telemetry()
    telemetry["correction_semantics"] = {
        "status": "CORRECTION_SUPERSESSION",
        "supersedes_receipt_sha256": "B" * 64,
    }
    receipt = build_rich_goal_completion_metrics_receipt(
        goal_id="goal-correction",
        telemetry=telemetry,
        provenance={"source": "reset-aware-correction"},
        binding=_binding(),
    )
    assert receipt["ledger_semantics"]["status"] == "CORRECTION_SUPERSESSION"
    assert receipt["ledger_semantics"]["supersedes_receipt_sha256"] == "B" * 64
    assert receipt["ledger_semantics"]["goal_recompleted_for_correction"] is False


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
    assert "legacy_route" not in receipt
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

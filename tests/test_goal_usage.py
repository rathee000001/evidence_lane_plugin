from __future__ import annotations

import pytest
from evidence_lane_plugin.goal_usage import (
    EARLIER_RECORDED_TOKENS,
    PRIOR_GOAL_TOKENS,
    build_component_token_accounting,
    build_goal_usage_receipt,
    build_profile_observed_usage_context,
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


def test_goal_usage_receipt_never_inherits_another_project_baseline() -> None:
    receipt = build_goal_usage_receipt(
        current_tokens=1_500_000,
        current_elapsed_seconds=3600,
    )
    assert receipt.exact()["current_tokens"] == 1_500_000
    assert receipt.exact()["prior_goal_tokens"] == PRIOR_GOAL_TOKENS
    assert receipt.exact()["earlier_recorded_tokens"] == EARLIER_RECORDED_TOKENS
    assert receipt.exact()["cumulative_tokens"] == 1_500_000
    assert receipt.display() == {
        "current_tokens": "1.5M",
        "current_elapsed": "1h 0m",
        "prior_goal_tokens": "0",
        "prior_goal_elapsed": "0s",
        "earlier_recorded_tokens": "0",
        "earlier_recorded_elapsed": "0s",
        "cumulative_tokens": "1.5M",
        "cumulative_elapsed": "1h 0m",
    }
    assert receipt.governance() == {
        "schema": "evidence-lane.goal-usage-governance.v1",
        "purpose": "ACCOUNTING_ONLY",
        "task_status_effect": "NONE",
        "goal_completion_effect": "NONE",
        "exact_counts_preserved": True,
    }


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_goal_usage_rejects_invalid_accounting_values(value: object) -> None:
    with pytest.raises(ValueError):
        compact_token_count(value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_goal_usage_receipt(
            current_tokens=value,  # type: ignore[arg-type]
            current_elapsed_seconds=0,
        )


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

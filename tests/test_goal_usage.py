from __future__ import annotations

import pytest
from evidence_lane_plugin.goal_usage import (
    EARLIER_RECORDED_TOKENS,
    PRIOR_GOAL_TOKENS,
    build_goal_usage_receipt,
    compact_duration,
    compact_token_count,
)


def test_compact_token_count_uses_readable_k_and_m_notation() -> None:
    assert compact_token_count(999) == "999"
    assert compact_token_count(256_000) == "256K"
    assert compact_token_count(665_603) == "665.6K"
    assert compact_token_count(1_500_000) == "1.5M"
    assert compact_token_count(14_007_602) == "14M"


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

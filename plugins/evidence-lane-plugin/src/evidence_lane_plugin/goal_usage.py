"""Compact display and exact accounting for persistent Goal usage."""

from __future__ import annotations

from dataclasses import asdict, dataclass

PRIOR_GOAL_TOKENS = 665_603
PRIOR_GOAL_ELAPSED_SECONDS = 24 * 60 + 25
EARLIER_RECORDED_TOKENS = 14_007_602
EARLIER_RECORDED_ELAPSED_SECONDS = 15 * 60 * 60 + 55 * 60


def compact_token_count(value: int) -> str:
    """Return a short K/M display while preserving integers elsewhere."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token count must be a non-negative integer")
    if value < 1_000:
        return str(value)
    divisor, suffix = (1_000_000, "M") if value >= 1_000_000 else (1_000, "K")
    rounded = round(value / divisor, 1)
    rendered = f"{rounded:.1f}".rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def compact_duration(total_seconds: int) -> str:
    """Render elapsed seconds without expanding into implementation detail."""

    if isinstance(total_seconds, bool) or not isinstance(total_seconds, int):
        raise TypeError("elapsed seconds must be a non-negative integer")
    if total_seconds < 0:
        raise ValueError("elapsed seconds must be a non-negative integer")
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or hours:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


@dataclass(frozen=True)
class GoalUsageReceipt:
    current_tokens: int
    current_elapsed_seconds: int
    prior_goal_tokens: int
    prior_goal_elapsed_seconds: int
    earlier_recorded_tokens: int
    earlier_recorded_elapsed_seconds: int
    cumulative_tokens: int
    cumulative_elapsed_seconds: int

    def exact(self) -> dict[str, int]:
        """Return the unabridged receipt used for forensic accounting."""

        return asdict(self)

    def display(self) -> dict[str, str]:
        """Return the human-facing K/M and elapsed-time projection."""

        return {
            "current_tokens": compact_token_count(self.current_tokens),
            "current_elapsed": compact_duration(self.current_elapsed_seconds),
            "prior_goal_tokens": compact_token_count(self.prior_goal_tokens),
            "prior_goal_elapsed": compact_duration(self.prior_goal_elapsed_seconds),
            "earlier_recorded_tokens": compact_token_count(
                self.earlier_recorded_tokens
            ),
            "earlier_recorded_elapsed": compact_duration(
                self.earlier_recorded_elapsed_seconds
            ),
            "cumulative_tokens": compact_token_count(self.cumulative_tokens),
            "cumulative_elapsed": compact_duration(self.cumulative_elapsed_seconds),
        }


def build_goal_usage_receipt(
    *,
    current_tokens: int,
    current_elapsed_seconds: int,
    prior_goal_tokens: int = PRIOR_GOAL_TOKENS,
    prior_goal_elapsed_seconds: int = PRIOR_GOAL_ELAPSED_SECONDS,
    earlier_recorded_tokens: int = EARLIER_RECORDED_TOKENS,
    earlier_recorded_elapsed_seconds: int = EARLIER_RECORDED_ELAPSED_SECONDS,
) -> GoalUsageReceipt:
    """Bind this continuation to the exact earlier usage baseline."""

    values = (
        current_tokens,
        current_elapsed_seconds,
        prior_goal_tokens,
        prior_goal_elapsed_seconds,
        earlier_recorded_tokens,
        earlier_recorded_elapsed_seconds,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ):
        raise ValueError("usage values must be non-negative integers")
    return GoalUsageReceipt(
        current_tokens=current_tokens,
        current_elapsed_seconds=current_elapsed_seconds,
        prior_goal_tokens=prior_goal_tokens,
        prior_goal_elapsed_seconds=prior_goal_elapsed_seconds,
        earlier_recorded_tokens=earlier_recorded_tokens,
        earlier_recorded_elapsed_seconds=earlier_recorded_elapsed_seconds,
        cumulative_tokens=current_tokens + prior_goal_tokens + earlier_recorded_tokens,
        cumulative_elapsed_seconds=(
            current_elapsed_seconds
            + prior_goal_elapsed_seconds
            + earlier_recorded_elapsed_seconds
        ),
    )

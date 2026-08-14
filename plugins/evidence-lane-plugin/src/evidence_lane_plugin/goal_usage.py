"""Compact display and exact accounting for persistent Goal usage.

The compact projection is presentation only.  Exact integers, component
availability, provenance, and the aggregation rule remain in the receipt so a
host cannot accidentally turn an output-only counter into total usage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .redaction import contains_secret, redact, redact_text

# Compatibility defaults are deliberately neutral. Historical ledgers belong to the
# exact project/task that supplied them; they must never become another user's
# implicit baseline.
PRIOR_GOAL_TOKENS = 0
PRIOR_GOAL_ELAPSED_SECONDS = 0
EARLIER_RECORDED_TOKENS = 0
EARLIER_RECORDED_ELAPSED_SECONDS = 0


TOKEN_COMPONENT_KEYS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "main_agent_tokens",
    "subagent_tokens",
)

_USAGE_BINDING_KEYS = (
    "project_id",
    "evidence_session_id",
    "task_id",
    "host_session_id_sha256",
)


def _normalized_usage_binding(binding: Mapping[str, object]) -> dict[str, str]:
    """Keep only the exact project/task/session/host identity fields."""

    if not isinstance(binding, Mapping):
        raise TypeError("usage binding must be a mapping")
    normalized = {
        key: str(binding.get(key) or "").strip() for key in _USAGE_BINDING_KEYS
    }
    if any(not value for value in normalized.values()):
        raise ValueError("usage requires exact project/task/session/host binding")
    host_session_sha256 = normalized["host_session_id_sha256"]
    if len(host_session_sha256) != 64 or any(
        character not in "0123456789abcdefABCDEF"
        for character in host_session_sha256
    ):
        raise ValueError("host_session_id_sha256 must be one exact SHA-256")
    if contains_secret(normalized):
        raise ValueError("usage binding contains secret-like material")
    return normalized


def compact_token_count(value: int, *, decimal_places: int = 1) -> str:
    """Return deterministic K/M/B notation while preserving exact integers.

    ``ROUND_HALF_UP`` is explicit so the same raw value renders identically on
    every supported Python host.  Callers that need to preserve a more precise
    observed display (for example ``1.4519B``) may request up to six decimal
    places; the ordinary UI projection remains one decimal place.
    """

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token count must be a non-negative integer")
    if (
        isinstance(decimal_places, bool)
        or not isinstance(decimal_places, int)
        or not 0 <= decimal_places <= 6
    ):
        raise ValueError("decimal_places must be an integer from 0 through 6")
    if value < 1_000:
        return str(value)
    if value >= 1_000_000_000:
        divisor, suffix = 1_000_000_000, "B"
    elif value >= 1_000_000:
        divisor, suffix = 1_000_000, "M"
    else:
        divisor, suffix = 1_000, "K"
    quantum = Decimal(1).scaleb(-decimal_places)
    rounded = (Decimal(value) / Decimal(divisor)).quantize(
        quantum, rounding=ROUND_HALF_UP
    )
    rendered = format(rounded, "f").rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def compact_token_count_receipt(
    value: int, *, decimal_places: int = 1
) -> dict[str, Any]:
    """Return the display together with its exact value and rounding law."""

    display = compact_token_count(value, decimal_places=decimal_places)
    suffix = display[-1] if display[-1] in {"K", "M", "B"} else None
    divisor = (
        {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
        if suffix is not None
        else 1
    )
    return {
        "raw": value,
        "display": display,
        "suffix": suffix,
        "divisor": divisor,
        "decimal_places": decimal_places,
        "rounding_rule": "ROUND_HALF_UP",
        "exact_raw_value_preserved": True,
    }


def _optional_token_count(name: str, value: object) -> int | None:
    if value is None or value == "UNAVAILABLE":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer or UNAVAILABLE")
    return value


def build_component_token_accounting(
    *,
    components: Mapping[str, object],
    total_tokens: object = None,
    provenance: Mapping[str, object],
    binding: Mapping[str, object],
) -> dict[str, Any]:
    """Build component-aware usage without double-counting subset metrics.

    Cached input is a subset of input and reasoning is a subset of output, so
    neither is added again.  A host-supplied final total wins.  Otherwise a
    total is computed only from a provably non-overlapping pair: main-agent and
    subagent totals, or input and output when agent totals are unavailable.
    Output by itself is never described as total usage.
    """

    if not isinstance(components, Mapping):
        raise TypeError("components must be a mapping")
    if not isinstance(provenance, Mapping) or not str(
        provenance.get("source") or ""
    ).strip():
        raise ValueError("component usage requires provenance with a source")
    normalized_binding = _normalized_usage_binding(binding)
    safe_provenance = redact(dict(provenance))
    if contains_secret(safe_provenance):
        raise ValueError("component usage provenance contains secret-like material")

    exact_components = {
        key: _optional_token_count(key, components.get(key))
        for key in TOKEN_COMPONENT_KEYS
    }
    component_projection = {
        key: (
            {
                "availability": "AVAILABLE",
                **compact_token_count_receipt(value),
            }
            if value is not None
            else {"availability": "UNAVAILABLE", "raw": None, "display": "UNAVAILABLE"}
        )
        for key, value in exact_components.items()
    }
    supplied_total = _optional_token_count("total_tokens", total_tokens)
    main_agent = exact_components["main_agent_tokens"]
    subagent = exact_components["subagent_tokens"]
    input_tokens = exact_components["input_tokens"]
    output_tokens = exact_components["output_tokens"]
    if supplied_total is not None:
        aggregate = supplied_total
        aggregate_basis = "HOST_EXPOSED_FINAL_TOTAL"
    elif main_agent is not None and subagent is not None:
        aggregate = main_agent + subagent
        aggregate_basis = "SUM_NON_OVERLAPPING_MAIN_AGENT_AND_SUBAGENT_TOTALS"
    elif (
        main_agent is None
        and subagent is None
        and input_tokens is not None
        and output_tokens is not None
    ):
        aggregate = input_tokens + output_tokens
        aggregate_basis = "SUM_NON_OVERLAPPING_INPUT_AND_OUTPUT"
    else:
        aggregate = None
        aggregate_basis = "UNAVAILABLE_INSUFFICIENT_NON_OVERLAPPING_COMPONENTS"

    return {
        "schema": "evidence-lane.goal-token-component-accounting.v1",
        "components": component_projection,
        "final_aggregate": (
            {
                "availability": "AVAILABLE",
                **compact_token_count_receipt(aggregate),
                "basis": aggregate_basis,
            }
            if aggregate is not None
            else {
                "availability": "UNAVAILABLE",
                "raw": None,
                "display": "UNAVAILABLE",
                "basis": aggregate_basis,
            }
        ),
        "provenance": safe_provenance,
        "binding": normalized_binding,
        "cached_input_is_subset_of_input": True,
        "reasoning_is_subset_of_output": True,
        "output_only_is_total": False,
        "main_and_subagent_double_count_prevented": True,
        "exact_counts_preserved": True,
    }


def build_profile_observed_usage_context(
    *,
    observations: list[Mapping[str, object]],
    user_exclusive_attribution: str,
    binding: Mapping[str, object],
) -> dict[str, Any]:
    """Keep profile observations separate from the user's causal attribution."""

    if not isinstance(observations, list) or not 1 <= len(observations) <= 32:
        raise ValueError("profile observations require one through 32 rows")
    normalized_binding = _normalized_usage_binding(binding)
    normalized: list[dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise TypeError("each profile observation must be a mapping")
        observed_on = str(observation.get("observed_on") or "").strip()
        raw = _optional_token_count("profile_observed_tokens", observation.get("raw"))
        if not observed_on or len(observed_on) > 64 or raw is None:
            raise ValueError("profile observations require a date and exact raw count")
        normalized.append(
            {
                "observed_on": observed_on,
                "evidence_kind": "PROFILE_SCREENSHOT_OBSERVED_DISPLAY",
                **compact_token_count_receipt(raw),
            }
        )
    exact_attestation = str(user_exclusive_attribution or "").strip()
    if not exact_attestation or len(exact_attestation) > 4_096:
        raise ValueError("profile usage requires one bounded user attribution")
    safe_attestation = redact_text(exact_attestation)
    if contains_secret(safe_attestation):
        raise ValueError("profile usage attribution contains secret-like material")
    arithmetic_sum = sum(int(row["raw"]) for row in normalized)
    return {
        "schema": "evidence-lane.profile-observed-usage-context.v1",
        "availability": "AVAILABLE",
        "observations": normalized,
        "arithmetic_sum": compact_token_count_receipt(
            arithmetic_sum, decimal_places=4
        ),
        "user_attestation": {
            "statement": safe_attestation,
            "evidence_kind": "USER_ATTESTATION",
            "proven_by_profile_screenshots": False,
        },
        "binding": normalized_binding,
        "machine_telemetry": "NOT_ESTABLISHED_BY_PROFILE_SCREENSHOTS",
        "causal_attribution_inferred": False,
        "access_scope": "PROJECT_TASK_PRIVATE_ANALYSIS",
        "project_local_only": True,
        "cross_project_retrieval": False,
        "shared_global_telemetry": False,
        "public_output_included": False,
        "private_reasoning_stored": False,
    }


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

    def governance(self) -> dict[str, str | bool]:
        """Declare that accounting never mutates Goal or task status."""

        return {
            "schema": "evidence-lane.goal-usage-governance.v1",
            "purpose": "ACCOUNTING_ONLY",
            "task_status_effect": "NONE",
            "goal_completion_effect": "NONE",
            "exact_counts_preserved": True,
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
    """Build a receipt from caller-supplied segments without inherited history."""

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

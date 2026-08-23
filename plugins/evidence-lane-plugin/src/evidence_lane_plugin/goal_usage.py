"""Compact display and exact accounting for persistent Goal usage.

The compact projection is presentation only.  Exact integers, component
availability, provenance, and the aggregation rule remain in the receipt so a
host cannot accidentally turn an output-only counter into total usage.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .redaction import contains_secret, redact, redact_text

# Compatibility defaults are deliberately neutral. Historical ledgers belong to the
# exact project/task that supplied them; they must never become another user's
# implicit baseline.
PRIOR_GOAL_TOKENS = 0
PRIOR_GOAL_ELAPSED_SECONDS = 0
EARLIER_RECORDED_TOKENS = 0
EARLIER_RECORDED_ELAPSED_SECONDS = 0
GOAL_COMPLETION_COMMAND = "MARK GOAL COMPLETE"
GOAL_COMPLETION_DISPOSITIONS = (
    "COMPLETE_THIS_TASK_AND_STATE_TRAVEL",
    "COMPLETE_FULLY",
)
RICH_GOAL_COMPLETION_METRICS_ROUTE = (
    "build_rich_goal_completion_metrics_receipt"
)
LEGACY_GOAL_USAGE_ROUTE = "build_goal_usage_receipt"

_RICH_RAW_TOKEN_FIELDS = (
    "raw_input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "raw_input_output_total_tokens",
)
_RICH_ACTIVITY_FIELDS = (
    "model_turn_starts",
    "assistant_agent_messages",
    "top_level_tool_calls",
    "native_mcp_completions",
    "patch_applications",
    "web_search_completions",
    "compactions",
    "aborted_turns",
    "unique_subagents",
    "spawn_calls",
)
_SUBAGENT_LIFECYCLE_FIELDS = (
    "started",
    "interacted",
    "interrupted",
)


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


def _count_projection(name: str, value: object) -> dict[str, Any]:
    exact = _optional_token_count(name, value)
    if exact is None:
        return {
            "availability": "UNAVAILABLE",
            "raw": None,
            "display": "UNAVAILABLE",
        }
    return {
        "availability": "AVAILABLE",
        **compact_token_count_receipt(exact, decimal_places=2),
    }


def _duration_projection(value: object) -> dict[str, Any]:
    exact = _optional_token_count("elapsed_seconds", value)
    if exact is None:
        return {
            "availability": "UNAVAILABLE",
            "raw": None,
            "display": "UNAVAILABLE",
        }
    return {
        "availability": "AVAILABLE",
        "raw": exact,
        "display": compact_duration(exact),
        "exact_raw_value_preserved": True,
    }


def _validate_persisted_rich_receipt(
    receipt: Mapping[str, object],
    *,
    goal_id: str,
    binding: Mapping[str, str],
) -> dict[str, Any]:
    persisted = dict(receipt)
    claimed_sha256 = str(persisted.pop("receipt_sha256", ""))
    if (
        persisted.get("schema")
        != "evidence-lane.rich-goal-completion-metrics.v1"
        or persisted.get("route") != RICH_GOAL_COMPLETION_METRICS_ROUTE
        or persisted.get("goal_id") != goal_id
        or persisted.get("binding") != dict(binding)
        or sha256_bytes(canonical_json_bytes(persisted)) != claimed_sha256
    ):
        raise ValueError("persisted rich Goal metrics receipt failed validation")
    return {**persisted, "receipt_sha256": claimed_sha256}


def build_rich_goal_completion_metrics_receipt(
    *,
    goal_id: str,
    telemetry: Mapping[str, object],
    provenance: Mapping[str, object],
    binding: Mapping[str, object],
    goal_already_complete: bool = False,
    persisted_receipt: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Render the sole supported Goal completion telemetry receipt.

    This function is display-only. It never completes a Goal. An already
    completed Goal reuses its exact persisted rich receipt and never replays a
    completion call merely to obtain metrics.
    """

    exact_goal_id = str(goal_id or "").strip()
    if not exact_goal_id or len(exact_goal_id) > 256:
        raise ValueError("rich Goal metrics require one exact Goal identity")
    if not isinstance(telemetry, Mapping):
        raise TypeError("rich Goal metrics telemetry must be a mapping")
    if not isinstance(provenance, Mapping) or not str(
        provenance.get("source") or ""
    ).strip():
        raise ValueError("rich Goal metrics require provenance with a source")
    normalized_binding = _normalized_usage_binding(binding)
    if not isinstance(goal_already_complete, bool):
        raise TypeError("goal_already_complete must be a boolean")
    if goal_already_complete and persisted_receipt is not None:
        return _validate_persisted_rich_receipt(
            persisted_receipt,
            goal_id=exact_goal_id,
            binding=normalized_binding,
        )

    safe_provenance = redact(dict(provenance))
    if contains_secret(safe_provenance):
        raise ValueError("rich Goal metrics provenance contains secret-like material")
    normalized = dict(telemetry)

    raw_input = _optional_token_count(
        "raw_input_tokens", normalized.get("raw_input_tokens")
    )
    cached_input = _optional_token_count(
        "cached_input_tokens", normalized.get("cached_input_tokens")
    )
    uncached_input = _optional_token_count(
        "uncached_input_tokens", normalized.get("uncached_input_tokens")
    )
    output = _optional_token_count("output_tokens", normalized.get("output_tokens"))
    reasoning = _optional_token_count(
        "reasoning_output_tokens", normalized.get("reasoning_output_tokens")
    )
    raw_total = _optional_token_count(
        "raw_input_output_total_tokens",
        normalized.get("raw_input_output_total_tokens"),
    )
    if raw_input is not None and cached_input is not None:
        if cached_input > raw_input:
            raise ValueError("cached input cannot exceed raw input")
        derived_uncached = raw_input - cached_input
        if uncached_input is None:
            uncached_input = derived_uncached
        elif uncached_input != derived_uncached:
            raise ValueError("uncached input must equal raw input minus cached input")
    if reasoning is not None and output is not None and reasoning > output:
        raise ValueError("reasoning output is a subset of output")
    if raw_input is not None and output is not None:
        derived_total = raw_input + output
        if raw_total is None:
            raw_total = derived_total
        elif raw_total != derived_total:
            raise ValueError("raw input+output total must equal raw input plus output")

    normalized_tokens = {
        "raw_input_tokens": raw_input,
        "cached_input_tokens": cached_input,
        "uncached_input_tokens": uncached_input,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "raw_input_output_total_tokens": raw_total,
    }
    missing_fields = [
        name for name in _RICH_RAW_TOKEN_FIELDS if normalized_tokens[name] is None
    ]

    host_accounted = _optional_token_count(
        "host_accounted_goal_tokens",
        normalized.get("host_accounted_goal_tokens"),
    )
    if host_accounted is None:
        missing_fields.append("host_accounted_goal_tokens")
    host_formula = str(normalized.get("host_accounting_formula") or "").strip()
    if not host_formula:
        host_formula = "UNKNOWN_NOT_EXPOSED"
        missing_fields.append("host_accounting_formula")

    elapsed = _optional_token_count(
        "elapsed_seconds", normalized.get("elapsed_seconds")
    )
    if elapsed is None:
        missing_fields.append("elapsed_seconds")

    activity_values: dict[str, int | None] = {}
    for name in _RICH_ACTIVITY_FIELDS:
        value = _optional_token_count(name, normalized.get(name))
        activity_values[name] = value
        if value is None:
            missing_fields.append(name)

    raw_lifecycle = normalized.get("subagent_lifecycle_counts")
    lifecycle = dict(raw_lifecycle) if isinstance(raw_lifecycle, Mapping) else {}
    lifecycle_values: dict[str, int | None] = {}
    for name in _SUBAGENT_LIFECYCLE_FIELDS:
        value = _optional_token_count(
            f"subagent_lifecycle_counts.{name}", lifecycle.get(name)
        )
        lifecycle_values[name] = value
        if value is None:
            missing_fields.append(f"subagent_lifecycle_counts.{name}")

    if goal_already_complete and persisted_receipt is None:
        missing_fields.append("persisted_completion_metrics_receipt")

    core: dict[str, Any] = {
        "schema": "evidence-lane.rich-goal-completion-metrics.v1",
        "status": "PASS" if not missing_fields else "INCOMPLETE_TELEMETRY",
        "route": RICH_GOAL_COMPLETION_METRICS_ROUTE,
        "goal_id": exact_goal_id,
        "binding": normalized_binding,
        "host_accounting": {
            "goal_tokens": _count_projection(
                "host_accounted_goal_tokens", host_accounted
            ),
            "conversion_or_weighting_formula": host_formula,
            "formula_exposed": host_formula != "UNKNOWN_NOT_EXPOSED",
            "kept_separate_from_raw_model_traffic": True,
        },
        "raw_model_traffic": {
            name: _count_projection(name, value)
            for name, value in normalized_tokens.items()
        },
        "elapsed": _duration_projection(elapsed),
        "activity_counts": {
            name: _count_projection(name, value)
            for name, value in activity_values.items()
        },
        "subagent_lifecycle_counts": {
            name: _count_projection(name, value)
            for name, value in lifecycle_values.items()
        },
        "missing_fields": sorted(set(missing_fields)),
        "provenance": safe_provenance,
        "accounting_laws": {
            "reasoning_tokens_are_subset_of_output": True,
            "reasoning_tokens_double_counted": False,
            "raw_total_formula": "RAW_INPUT_PLUS_OUTPUT",
            "cached_input_is_subset_of_raw_input": True,
            "exact_values_preserved": True,
        },
        "completion_state": {
            "goal_already_complete": goal_already_complete,
            "persisted_receipt_reused": False,
            "completion_call_performed": False,
            "display_route_has_completion_authority": False,
        },
        "legacy_route": {
            "identifier": LEGACY_GOAL_USAGE_ROUTE,
            "status": "OBSOLETE_ROUTE",
            "executable": False,
            "fallback_allowed": False,
            "required_current_route": RICH_GOAL_COMPLETION_METRICS_ROUTE,
        },
    }
    return {
        **core,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(core)),
    }


def build_goal_completion_authorization(
    *,
    visible_command: str,
    disposition: str,
    actor_kind: str,
    current_task_id: str,
) -> dict[str, Any]:
    """Validate the sole human-owned Goal completion boundary.

    HIL, candidate, Plan, test, automation, and lifecycle state may pause or
    stall a Goal, but none can complete it.  State Travel is one explicit
    human disposition: it closes the current task's Goal boundary while
    preserving unfinished governed work for an exact successor task.  It does
    not turn Goal completion into HIL or pointer authority.
    """

    command = str(visible_command or "").strip()
    exact_disposition = str(disposition or "").strip().upper()
    exact_actor = str(actor_kind or "").strip().upper()
    task_id = str(current_task_id or "").strip()
    if command != GOAL_COMPLETION_COMMAND:
        raise ValueError("Goal completion requires the exact visible human command")
    if exact_actor != "HUMAN":
        raise ValueError("Only the human may authorize Goal completion")
    if exact_disposition not in GOAL_COMPLETION_DISPOSITIONS:
        raise ValueError("Goal completion requires one exact human disposition")
    if not task_id or len(task_id) > 256:
        raise ValueError("Goal completion requires one exact current task identity")
    return {
        "schema": "evidence-lane.goal-completion-authorization.v1",
        "status": "AUTHORIZED_BY_EXACT_HUMAN_COMMAND",
        "visible_command": GOAL_COMPLETION_COMMAND,
        "actor_kind": "HUMAN",
        "current_task_id": task_id,
        "disposition": exact_disposition,
        "current_task_goal_completed": True,
        "state_travel_requested": (
            exact_disposition == "COMPLETE_THIS_TASK_AND_STATE_TRAVEL"
        ),
        "successor_goal_required": (
            exact_disposition == "COMPLETE_THIS_TASK_AND_STATE_TRAVEL"
        ),
        "full_goal_closed": exact_disposition == "COMPLETE_FULLY",
        "hil_can_complete_goal": False,
        "candidate_can_complete_goal": False,
        "automation_can_complete_goal": False,
        "task_transition_can_complete_goal": False,
        "pause_or_stall_can_complete_goal": False,
        "goal_completion_implies_hil_approval": False,
        "goal_completion_implies_fuse": False,
        "goal_completion_implies_pointer_move": False,
        "goal_completion_implies_git_or_install": False,
    }


def build_goal_usage_receipt(
    *,
    current_tokens: int,
    current_elapsed_seconds: int,
    prior_goal_tokens: int = PRIOR_GOAL_TOKENS,
    prior_goal_elapsed_seconds: int = PRIOR_GOAL_ELAPSED_SECONDS,
    earlier_recorded_tokens: int = EARLIER_RECORDED_TOKENS,
    earlier_recorded_elapsed_seconds: int = EARLIER_RECORDED_ELAPSED_SECONDS,
) -> dict[str, Any]:
    """Return a non-executing tombstone for the superseded compact route."""

    supplied_fields = {
        "current_tokens": current_tokens is not None,
        "current_elapsed_seconds": current_elapsed_seconds is not None,
        "prior_goal_tokens": prior_goal_tokens is not None,
        "prior_goal_elapsed_seconds": prior_goal_elapsed_seconds is not None,
        "earlier_recorded_tokens": earlier_recorded_tokens is not None,
        "earlier_recorded_elapsed_seconds": (
            earlier_recorded_elapsed_seconds is not None
        ),
    }
    core = {
        "schema": "evidence-lane.obsolete-route.v1",
        "status": "OBSOLETE_ROUTE",
        "obsolete_route": LEGACY_GOAL_USAGE_ROUTE,
        "required_current_route": RICH_GOAL_COMPLETION_METRICS_ROUTE,
        "required_schema": "evidence-lane.rich-goal-completion-metrics.v1",
        "legacy_execution_performed": False,
        "fallback_allowed": False,
        "mutation_performed": False,
        "supplied_fields": supplied_fields,
        "supplied_values_returned": False,
    }
    return {
        **core,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(core)),
    }

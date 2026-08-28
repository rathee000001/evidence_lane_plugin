"""Compact display and exact accounting for persistent Goal usage.

The compact projection is presentation only.  Exact integers, component
availability, provenance, and the aggregation rule remain in the receipt so a
host cannot accidentally turn an output-only counter into total usage.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .hashing import canonical_json_bytes, sha256_bytes
from .redaction import contains_secret, redact, redact_text

GOAL_COMPLETION_COMMAND = "MARK GOAL COMPLETE"
GOAL_COMPLETION_DISPOSITIONS = (
    "COMPLETE_THIS_TASK_AND_STATE_TRAVEL",
    "COMPLETE_FULLY",
)
RICH_GOAL_COMPLETION_METRICS_ROUTE = (
    "build_rich_goal_completion_metrics_receipt"
)

_RICH_RAW_TOKEN_FIELDS = (
    "raw_input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "raw_input_output_total_tokens",
)
_RICH_ACTIVITY_FIELDS = (
    "model_turn_starts",
    "assistant_agent_messages",
    "top_level_tool_calls",
    "execution_calls",
    "native_mcp_completions",
    "patch_applications",
    "web_search_completions",
    "compactions",
    "aborted_turns",
    "unique_subagents",
    "spawn_calls",
)
_RESET_AWARE_COUNTER_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
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


def _sample_timestamp(value: object) -> datetime:
    exact = str(value or "").strip()
    if not exact:
        raise ValueError("reset-aware token samples require timestamps")
    try:
        parsed = datetime.fromisoformat(exact)
    except ValueError as exc:
        raise ValueError("reset-aware token sample timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("reset-aware token sample timestamp requires a timezone")
    return parsed.astimezone(UTC)


def build_reset_aware_epoch_accounting(
    *,
    cumulative_samples: object,
    timezone_name: str = "America/New_York",
) -> dict[str, Any]:
    """Sum positive cumulative-counter deltas across arbitrary reset epochs."""

    if not isinstance(cumulative_samples, list) or not cumulative_samples:
        raise ValueError("reset-aware accounting requires cumulative token samples")
    if len(cumulative_samples) > 100_000:
        raise ValueError("reset-aware accounting sample count exceeds the bound")
    try:
        timezone_value = ZoneInfo(str(timezone_name or ""))
    except (ValueError, TypeError) as exc:
        raise ValueError("reset-aware accounting timezone is invalid") from exc

    totals = {field: 0 for field in _RESET_AWARE_COUNTER_FIELDS}
    daily: dict[str, dict[str, int]] = {}
    reset_count = 0
    prior: dict[str, int] | None = None
    prior_timestamp: datetime | None = None
    epochs: list[dict[str, Any]] = []
    epoch_index = 1
    epoch_start: str | None = None
    for index, raw_sample in enumerate(cumulative_samples):
        if not isinstance(raw_sample, Mapping):
            raise TypeError("reset-aware token samples must be mappings")
        timestamp = _sample_timestamp(raw_sample.get("timestamp"))
        if prior_timestamp is not None and timestamp < prior_timestamp:
            raise ValueError("reset-aware token samples must be chronological")
        current: dict[str, int] = {}
        for field in _RESET_AWARE_COUNTER_FIELDS:
            value = _optional_token_count(
                f"cumulative_token_samples[{index}].{field}",
                raw_sample.get(field),
            )
            if value is None:
                raise ValueError("reset-aware token samples require every counter field")
            current[field] = value
        if current["cached_input_tokens"] > current["input_tokens"]:
            raise ValueError("cached cumulative input cannot exceed cumulative input")
        if current["reasoning_output_tokens"] > current["output_tokens"]:
            raise ValueError("cumulative reasoning output must remain an output subset")
        if current["total_tokens"] != current["input_tokens"] + current["output_tokens"]:
            raise ValueError("cumulative total tokens must equal input plus output")

        reset = prior is not None and any(
            current[field] < prior[field] for field in _RESET_AWARE_COUNTER_FIELDS
        )
        if epoch_start is None:
            epoch_start = timestamp.isoformat().replace("+00:00", "Z")
        if reset:
            epochs.append(
                {
                    "epoch": epoch_index,
                    "started_at": epoch_start,
                    "ended_before": timestamp.isoformat().replace("+00:00", "Z"),
                }
            )
            reset_count += 1
            epoch_index += 1
            epoch_start = timestamp.isoformat().replace("+00:00", "Z")
        delta = {
            field: (
                current[field]
                if prior is None or current[field] < prior[field]
                else current[field] - prior[field]
            )
            for field in _RESET_AWARE_COUNTER_FIELDS
        }
        day = timestamp.astimezone(timezone_value).date().isoformat()
        day_totals = daily.setdefault(
            day, {field: 0 for field in _RESET_AWARE_COUNTER_FIELDS}
        )
        for field in _RESET_AWARE_COUNTER_FIELDS:
            totals[field] += delta[field]
            day_totals[field] += delta[field]
        prior = current
        prior_timestamp = timestamp

    assert prior_timestamp is not None and epoch_start is not None
    epochs.append(
        {
            "epoch": epoch_index,
            "started_at": epoch_start,
            "ended_at": prior_timestamp.isoformat().replace("+00:00", "Z"),
        }
    )
    if totals["total_tokens"] != totals["input_tokens"] + totals["output_tokens"]:
        raise ValueError("reset-aware aggregate total arithmetic mismatch")
    if totals["cached_input_tokens"] > totals["input_tokens"]:
        raise ValueError("reset-aware cached input exceeds raw input")
    if totals["reasoning_output_tokens"] > totals["output_tokens"]:
        raise ValueError("reset-aware reasoning output exceeds output")
    core = {
        "schema": "evidence-lane.reset-aware-token-epochs.v1",
        "status": "PASS",
        "timezone": str(timezone_name),
        "sample_count": len(cumulative_samples),
        "counter_reset_count": reset_count,
        "epoch_count": reset_count + 1,
        "totals": totals,
        "daily_totals": [
            {"date": day, **values} for day, values in sorted(daily.items())
        ],
        "epochs": epochs,
        "positive_delta_across_resets": True,
        "final_minus_initial_used": False,
        "reasoning_tokens_double_counted": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


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


def _duration_authority_projection(name: str, value: object) -> dict[str, Any]:
    if value is None or value == "UNAVAILABLE":
        return {
            "authority": name,
            "availability": "UNAVAILABLE",
            "exact_seconds": None,
            "display": "UNAVAILABLE",
        }
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"{name} must be non-negative seconds or UNAVAILABLE")
    exact = Decimal(str(value))
    if exact < 0:
        raise ValueError(f"{name} must be non-negative seconds or UNAVAILABLE")
    whole = int(exact)
    fraction = exact - Decimal(whole)
    days, remainder = divmod(whole, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds = divmod(remainder, 60)
    second_value = Decimal(seconds) + fraction
    second_text = format(second_value.normalize(), "f")
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{second_text}s")
    return {
        "authority": name,
        "availability": "AVAILABLE",
        "exact_seconds": int(exact) if exact == exact.to_integral() else float(exact),
        "display": " ".join(parts),
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

    raw_samples = normalized.get("cumulative_token_samples")
    reset_accounting = (
        build_reset_aware_epoch_accounting(
            cumulative_samples=raw_samples,
            timezone_name=str(
                normalized.get("daily_reconciliation_timezone")
                or "America/New_York"
            ),
        )
        if raw_samples is not None
        else None
    )
    reset_totals = (
        dict(reset_accounting["totals"])
        if isinstance(reset_accounting, dict)
        else {}
    )

    def reset_aware_or_supplied(
        public_name: str,
        counter_name: str,
    ) -> int | None:
        supplied = _optional_token_count(public_name, normalized.get(public_name))
        derived = reset_totals.get(counter_name)
        if derived is not None:
            if supplied is not None and supplied != derived:
                raise ValueError(
                    f"{public_name} does not match reset-aware epoch accounting"
                )
            return int(derived)
        return supplied

    raw_input = reset_aware_or_supplied(
        "raw_input_tokens", "input_tokens"
    )
    cached_input = reset_aware_or_supplied(
        "cached_input_tokens", "cached_input_tokens"
    )
    uncached_input = _optional_token_count(
        "uncached_input_tokens", normalized.get("uncached_input_tokens")
    )
    cache_write_input = reset_aware_or_supplied(
        "cache_write_input_tokens", "cache_write_input_tokens"
    )
    output = reset_aware_or_supplied("output_tokens", "output_tokens")
    reasoning = reset_aware_or_supplied(
        "reasoning_output_tokens", "reasoning_output_tokens"
    )
    raw_total = reset_aware_or_supplied(
        "raw_input_output_total_tokens", "total_tokens"
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
        "cache_write_input_tokens": cache_write_input,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "raw_input_output_total_tokens": raw_total,
    }
    missing_fields = [
        name for name in _RICH_RAW_TOKEN_FIELDS if normalized_tokens[name] is None
    ]
    if reset_accounting is None:
        missing_fields.append("cumulative_token_samples")

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

    host_completed_seconds = normalized.get(
        "host_completed_time_used_seconds", elapsed
    )
    user_confirmed_ui_seconds = normalized.get(
        "user_confirmed_active_ui_runtime_seconds"
    )
    wall_seconds = normalized.get("goal_calendar_span_seconds")
    native_overlap_seconds = normalized.get(
        "native_completed_turn_overlap_seconds"
    )
    duration_authorities = {
        "USER_CONFIRMED_ACTIVE_UI_RUNTIME": _duration_authority_projection(
            "USER_CONFIRMED_ACTIVE_UI_RUNTIME", user_confirmed_ui_seconds
        ),
        "HOST_COMPLETED_TIME_USED": _duration_authority_projection(
            "HOST_COMPLETED_TIME_USED", host_completed_seconds
        ),
        "GOAL_CALENDAR_SPAN": _duration_authority_projection(
            "GOAL_CALENDAR_SPAN", wall_seconds
        ),
        "NATIVE_COMPLETED_TURN_OVERLAP": _duration_authority_projection(
            "NATIVE_COMPLETED_TURN_OVERLAP", native_overlap_seconds
        ),
    }
    host_decimal = (
        Decimal(str(host_completed_seconds))
        if host_completed_seconds not in {None, "UNAVAILABLE"}
        else None
    )
    user_decimal = (
        Decimal(str(user_confirmed_ui_seconds))
        if user_confirmed_ui_seconds not in {None, "UNAVAILABLE"}
        else None
    )
    overlap_decimal = (
        Decimal(str(native_overlap_seconds))
        if native_overlap_seconds not in {None, "UNAVAILABLE"}
        else None
    )
    preferred_runtime_basis = (
        "USER_CONFIRMED_ACTIVE_UI_RUNTIME"
        if user_decimal is not None
        else "HOST_COMPLETED_TIME_USED"
        if host_decimal is not None
        else "UNAVAILABLE"
    )
    preferred_runtime_seconds = (
        user_confirmed_ui_seconds
        if user_decimal is not None
        else host_completed_seconds
    )
    ui_minus_host = (
        user_decimal - host_decimal
        if user_decimal is not None and host_decimal is not None
        else None
    )
    host_minus_overlap = (
        host_decimal - overlap_decimal
        if host_decimal is not None and overlap_decimal is not None
        else None
    )
    raw_active_ui = normalized.get("active_ui_snapshot")
    active_ui_snapshot = (
        dict(raw_active_ui) if isinstance(raw_active_ui, Mapping) else {}
    )
    if active_ui_snapshot:
        required_ui_fields = {
            "observed_at",
            "goal_status",
            "time_used_seconds",
            "updated_at",
            "computed_active_display_seconds",
        }
        if set(active_ui_snapshot) != required_ui_fields:
            raise ValueError("active UI snapshot must carry its exact Goal object")
        _sample_timestamp(active_ui_snapshot["observed_at"])
        if active_ui_snapshot["goal_status"] != "active":
            raise ValueError("active UI snapshot requires an active Goal")
        ui_base = _optional_token_count(
            "active_ui_snapshot.time_used_seconds",
            active_ui_snapshot["time_used_seconds"],
        )
        ui_updated = _optional_token_count(
            "active_ui_snapshot.updated_at", active_ui_snapshot["updated_at"]
        )
        ui_display = _optional_token_count(
            "active_ui_snapshot.computed_active_display_seconds",
            active_ui_snapshot["computed_active_display_seconds"],
        )
        if None in {ui_base, ui_updated, ui_display}:
            raise ValueError("active UI snapshot values cannot be unavailable")
        active_ui_snapshot["presentation_class"] = (
            "CLIENT_EXTRAPOLATED_PRESENTATION"
        )
        active_ui_snapshot["formula"] = (
            "timeUsedSeconds + observation_timestamp - updatedAt"
        )
        active_ui_snapshot["admitted_to_duration_formula"] = False
    else:
        active_ui_snapshot = {
            "availability": "UNAVAILABLE",
            "presentation_class": "CLIENT_EXTRAPOLATED_PRESENTATION",
            "admitted_to_duration_formula": False,
        }

    raw_duration_observation = normalized.get("duration_observation")
    duration_observation = (
        dict(raw_duration_observation)
        if isinstance(raw_duration_observation, Mapping)
        else {}
    )
    if user_decimal is not None:
        if (
            duration_observation.get("source_kind")
            != "USER_CONFIRMED_ACTIVE_UI_RUNTIME"
            or Decimal(str(duration_observation.get("runtime_seconds")))
            != user_decimal
            or duration_observation.get("goal_recompleted") is not False
            or duration_observation.get("main_token_segment_overwritten") is not False
        ):
            raise ValueError(
                "user-confirmed UI runtime requires a separate append-only observation"
            )
        observation_timestamp = duration_observation.get("observation_timestamp")
        if observation_timestamp not in {None, "UNAVAILABLE"}:
            _sample_timestamp(observation_timestamp)
        duration_observation["admitted_as_separate_duration_authority"] = True
    else:
        duration_observation = {
            "availability": "UNAVAILABLE",
            "source_kind": "USER_CONFIRMED_ACTIVE_UI_RUNTIME",
            "missing_not_coerced": True,
        }

    raw_gaps = normalized.get("non_execution_gaps")
    non_execution_gaps = dict(raw_gaps) if isinstance(raw_gaps, Mapping) else {}
    if non_execution_gaps:
        total_gap = _optional_token_count(
            "non_execution_gaps.total_seconds",
            non_execution_gaps.get("total_seconds"),
        )
        large_gap = _optional_token_count(
            "non_execution_gaps.large_gap_seconds",
            non_execution_gaps.get("large_gap_seconds"),
        )
        small_gap = _optional_token_count(
            "non_execution_gaps.small_gap_seconds",
            non_execution_gaps.get("small_gap_seconds"),
        )
        if (
            total_gap is None
            or large_gap is None
            or small_gap is None
            or total_gap != large_gap + small_gap
        ):
            raise ValueError("non-execution gap arithmetic is invalid")
        non_execution_gaps = {
            **non_execution_gaps,
            "classification": "NON_EXECUTION_GAP_WITH_STALE_INPROGRESS_START",
            "cause": str(
                non_execution_gaps.get("cause")
                or "NON_EXECUTION_GAP_CAUSE_UNPROVEN"
            ),
            "counted_as_active_runtime": False,
        }

    activity_values: dict[str, int | None] = {}
    for name in _RICH_ACTIVITY_FIELDS:
        value = _optional_token_count(name, normalized.get(name))
        activity_values[name] = value
        if value is None:
            missing_fields.append(name)

    raw_native_turns = normalized.get("native_turn_evidence")
    native_turns: dict[str, int | None]
    native_fields = (
        "returned_turn_count",
        "in_progress_turns",
        "populated_in_progress_turns",
        "empty_in_progress_turns",
        "context_compactions",
        "model_turn_starts",
        "aborted_turns",
    )
    if isinstance(raw_native_turns, Mapping):
        native_turns = {
            name: _optional_token_count(
                f"native_turn_evidence.{name}", raw_native_turns.get(name)
            )
            for name in native_fields
        }
        in_progress = native_turns["in_progress_turns"]
        populated = native_turns["populated_in_progress_turns"]
        empty = native_turns["empty_in_progress_turns"]
        if (
            in_progress is not None
            and populated is not None
            and empty is not None
            and populated + empty != in_progress
        ):
            raise ValueError(
                "native populated plus empty inProgress turns must equal total"
            )
        for activity_name, native_name in (
            ("compactions", "context_compactions"),
            ("model_turn_starts", "model_turn_starts"),
            ("aborted_turns", "aborted_turns"),
        ):
            native_value = native_turns[native_name]
            supplied_value = activity_values[activity_name]
            if (
                native_value is not None
                and supplied_value is not None
                and native_value != supplied_value
            ):
                raise ValueError(
                    f"{activity_name} does not match native turn reconciliation"
                )
            if native_value is not None:
                activity_values[activity_name] = native_value
    else:
        native_turns = {name: None for name in native_fields}
        missing_fields.append("native_turn_evidence")

    raw_status_mismatches = normalized.get("native_status_mismatches")
    native_status_mismatches = (
        list(raw_status_mismatches)
        if isinstance(raw_status_mismatches, list)
        else []
    )

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

    raw_correction = normalized.get("correction_semantics")
    correction_semantics = (
        dict(raw_correction) if isinstance(raw_correction, Mapping) else {}
    )
    correction_status = str(
        correction_semantics.get("status") or "ADMITTED"
    ).strip().upper()
    if correction_status not in {"ADMITTED", "CORRECTION_SUPERSESSION"}:
        raise ValueError("Goal metrics correction status is invalid")
    supersedes = correction_semantics.get("supersedes_receipt_sha256")
    if correction_status == "CORRECTION_SUPERSESSION":
        if not isinstance(supersedes, str) or len(supersedes) != 64:
            raise ValueError("Goal metrics correction requires one superseded SHA-256")
    elif supersedes not in {None, ""}:
        raise ValueError("An admitted Goal metrics segment cannot supersede a receipt")

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
        "reset_aware_epoch_accounting": (
            reset_accounting
            if reset_accounting is not None
            else {
                "schema": "evidence-lane.reset-aware-token-epochs.v1",
                "status": "UNAVAILABLE",
                "reason": "CUMULATIVE_TOKEN_SAMPLES_NOT_SUPPLIED",
                "final_minus_initial_used": False,
            }
        ),
        "native_turn_reconciliation": {
            "status": (
                "PASS"
                if isinstance(raw_native_turns, Mapping)
                else "UNAVAILABLE"
            ),
            **native_turns,
            "unavailable_values_coerced_to_zero": False,
            "stale_in_progress_turns_visible": native_turns[
                "in_progress_turns"
            ],
            "status_mismatches": native_status_mismatches,
            "hidden_overlay_truth": str(
                normalized.get("hidden_overlay_truth") or "UNAVAILABLE"
            ),
            "aborted_turn_source": "NATIVE_TURN_ABORTED_EVENTS_ONLY",
            "started_minus_completed_inference_used": False,
        },
        "elapsed": _duration_projection(elapsed),
        "duration_authorities": duration_authorities,
        "duration_reconciliation": {
            "preferred_runtime_basis": preferred_runtime_basis,
            "preferred_runtime_seconds": preferred_runtime_seconds,
            "preferred_formula": (
                "COALESCE(USER_CONFIRMED_ACTIVE_UI_RUNTIME,HOST_COMPLETED_TIME_USED)"
            ),
            "ui_minus_host_seconds": (
                float(ui_minus_host)
                if ui_minus_host is not None
                and ui_minus_host != ui_minus_host.to_integral()
                else int(ui_minus_host)
                if ui_minus_host is not None
                else None
            ),
            "host_overhead_seconds": (
                float(host_minus_overlap)
                if host_minus_overlap is not None
                and host_minus_overlap != host_minus_overlap.to_integral()
                else int(host_minus_overlap)
                if host_minus_overlap is not None
                else None
            ),
            "active_ui_snapshot": active_ui_snapshot,
            "user_confirmed_duration_observation": duration_observation,
            "non_execution_gaps": non_execution_gaps or {
                "availability": "UNAVAILABLE",
                "cause": "NON_EXECUTION_GAP_CAUSE_UNPROVEN",
                "counted_as_active_runtime": False,
            },
            "ui_host_wall_and_turn_overlap_summed": False,
            "duration_authorities_aliased": False,
            "first_complete_goal_receipt_required": True,
            "first_complete_goal_receipt_selected": bool(
                normalized.get("first_complete_goal_receipt_selected") is True
            ),
        },
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
        "ledger_semantics": {
            "status": correction_status,
            "supersedes_receipt_sha256": supersedes or None,
            "goal_recompleted_for_correction": False,
            "idempotent_exact_task_segment_required": True,
            "consolidated_formula_excludes_superseded_and_quarantined": True,
            "missing_host_tokens_remain_null": True,
        },
        "full_option_2_display_contract": {
            "current_exact_and_display_table": True,
            "current_activity_sentence": True,
            "native_turn_reconciliation": True,
            "daily_reset_aware_reconciliation": True,
            "duration_authority_table": True,
            "consolidated_exact_and_display_table": True,
            "consolidated_activity_sentence": True,
            "nullable_observation_availability_counts": True,
            "formula_and_all_receipts_visible": True,
            "post_append_closeout_tail_displayed_separately": True,
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

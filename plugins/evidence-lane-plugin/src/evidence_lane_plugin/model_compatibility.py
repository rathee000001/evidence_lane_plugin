"""Model/tool compatibility policy for governed Evidence Lane execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

MODEL_COMPATIBILITY_SCHEMA = "evidence-lane.model-compatibility.v1"
MODEL_COMPATIBILITY_CATALOG_SCHEMA = (
    "evidence-lane.model-compatibility-catalog.v1"
)

_CURRENT_TOOL_REQUIREMENTS = (
    "FUNCTION_CALLING",
    "STRUCTURED_OUTPUTS",
    "MCP",
    "SKILLS",
    "TOOL_SEARCH",
)
_RULES: dict[str, dict[str, Any]] = {
    "gpt-5.6-sol": {
        "family": "GPT_5_6_SOL",
        "submodels": ("sol",),
        "efforts": ("none", "low", "medium", "high", "xhigh", "max"),
        "codex_extended_efforts": ("ultra",),
        "tool_contract": "OFFICIAL_CURRENT_TOOL_SURFACE",
        "qualification": "CURRENT_PRIMARY",
        "minimum_full_lifecycle_effort": "high",
    },
    "gpt-5.6-terra": {
        "family": "GPT_5_6_TERRA",
        "submodels": ("terra",),
        "efforts": ("none", "low", "medium", "high", "xhigh", "max"),
        "codex_extended_efforts": ("ultra",),
        "tool_contract": "OFFICIAL_CURRENT_TOOL_SURFACE",
        "qualification": "CURRENT_CONDITIONAL_INSTALLED_PROOF",
        "minimum_full_lifecycle_effort": "high",
    },
    "gpt-5.6-luna": {
        "family": "GPT_5_6_LUNA",
        "submodels": ("luna",),
        "efforts": ("none", "low", "medium", "high", "xhigh", "max"),
        "codex_extended_efforts": (),
        "tool_contract": "OFFICIAL_CURRENT_TOOL_SURFACE",
        "qualification": "CURRENT_CONDITIONAL_INSTALLED_PROOF",
        "minimum_full_lifecycle_effort": "high",
    },
    "gpt-5.5": {
        "family": "GPT_5_5",
        "submodels": (),
        "efforts": ("none", "low", "medium", "high", "xhigh"),
        "codex_extended_efforts": (),
        "tool_contract": "OFFICIAL_TOOL_SURFACE",
        "qualification": "LEGACY_CONDITIONAL_INSTALLED_PROOF",
        "minimum_full_lifecycle_effort": "high",
    },
    "gpt-5.4": {
        "family": "GPT_5_4",
        "submodels": (),
        "efforts": ("none", "low", "medium", "high", "xhigh"),
        "codex_extended_efforts": (),
        "tool_contract": "OFFICIAL_TOOL_SURFACE",
        "qualification": "LEGACY_CONDITIONAL_INSTALLED_PROOF",
        "minimum_full_lifecycle_effort": "high",
    },
    "gpt-5.4-mini": {
        "family": "GPT_5_4_MINI",
        "submodels": (),
        "efforts": ("none", "low", "medium", "high", "xhigh"),
        "codex_extended_efforts": (),
        "tool_contract": "OFFICIAL_TOOL_SURFACE",
        "qualification": "MINI_CONDITIONAL_INSTALLED_PROOF",
        "minimum_full_lifecycle_effort": "high",
    },
}
_ALIASES = {
    "gpt-5.6": "gpt-5.6-sol",
    "5.6 sol": "gpt-5.6-sol",
    "5.6 terra": "gpt-5.6-terra",
    "5.6 luna": "gpt-5.6-luna",
    "5.5": "gpt-5.5",
    "5.4": "gpt-5.4",
    "5.4 mini": "gpt-5.4-mini",
}
_BLOCKED_CHAT_MODELS = {
    "5.3 codex spark",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.3-chat",
    "gpt-5.2-chat",
    "chat-latest",
    "instant",
}
_SPEED_RULES = {
    "standard": "CURRENT_STANDARD_SELECTOR",
    "instant": "CHATGPT_INSTANT_OUT_OF_SCOPE",
}
_EFFORT_ALIASES = {
    "light": "low",
    "extra high": "xhigh",
    "extra_high": "xhigh",
    "extra-high": "xhigh",
}
_EFFORT_RANK = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "xhigh": 4,
    "max": 5,
    "ultra": 6,
}


def model_compatibility_catalog() -> dict[str, Any]:
    rows = [
        {
            "model": model,
            **rule,
            "submodels": list(rule["submodels"]),
            "efforts": list(rule["efforts"]),
            "codex_extended_efforts": list(rule["codex_extended_efforts"]),
        }
        for model, rule in _RULES.items()
    ]
    core = {
        "schema": MODEL_COMPATIBILITY_CATALOG_SCHEMA,
        "status": "PASS",
        "policy": "CAPABILITY_AND_INSTALLED_PROOF_NOT_MODEL_NAME_ONLY",
        "required_tool_capabilities": list(_CURRENT_TOOL_REQUIREMENTS),
        "models": rows,
        "reasoning_speeds": dict(_SPEED_RULES),
        "blocked_chat_or_instant_models": sorted(_BLOCKED_CHAT_MODELS),
        "installed_host_negative_evidence": [
            {
                "profile": "5.3 Codex Spark",
                "classification": "BLOCKED_CURRENT_PLUGIN_HOST_SURFACE",
                "evidence_class": "USER_OBSERVED_INSTALLED_HOST_BEHAVIOR",
                "observed_failure": (
                    "PLUGIN_RIGHT_PANEL_AND_PUBLIC_ACTION_SURFACE_ABSENT"
                ),
                "observed_at": "2026-08-26",
                "future_requalification_requires": (
                    "FULL_MODEL_VISIBLE_SKILL_MCP_ACTION_AND_RIGHT_PANEL_PROOF"
                ),
            }
        ],
        "unknown_model_behavior": "CONDITIONAL_INSTALLED_HOST_PROOF_REQUIRED",
        "low_or_none_reasoning_behavior": (
            "TOOL_CAPABLE_WHEN_MODEL_SUPPORTS_IT_BUT_NOT_QUALIFIED_FOR_LONG_"
            "GOVERNED_AUTONOMY_WITHOUT_REPRESENTATIVE_TESTS"
        ),
        "work_handoff_requires_exact_profile_replay": True,
        "official_sources": [
            "https://developers.openai.com/api/docs/models",
            "https://developers.openai.com/api/docs/models/gpt-5.4",
            "https://developers.openai.com/api/docs/models/gpt-5.4-mini",
            "https://developers.openai.com/api/docs/models/gpt-5.5",
        ],
        "official_source_checked_at": "2026-08-26",
        "chatgpt_surface_exposed": False,
    }
    return {**core, "catalog_sha256": sha256_bytes(canonical_json_bytes(core))}


def classify_model_compatibility(
    execution_profile: Mapping[str, Any] | None,
    *,
    installed_host_tooling_proven: bool = False,
) -> dict[str, Any]:
    profile = {
        key: str(value).strip()
        for key, value in dict(execution_profile or {}).items()
        if value is not None and str(value).strip()
    }
    raw_model = profile.get("model", "").casefold()
    model = _ALIASES.get(raw_model, raw_model)
    submodel = profile.get("submodel", "").casefold()
    raw_effort = profile.get("reasoning_effort", "").casefold()
    effort = _EFFORT_ALIASES.get(raw_effort, raw_effort)
    speed = profile.get("reasoning_speed", "").casefold()
    rule = _RULES.get(model)

    reasons: list[str] = []
    blocked = model in _BLOCKED_CHAT_MODELS or speed == "instant"
    if not model:
        reasons.append("MODEL_UNAVAILABLE")
    elif blocked:
        reasons.append("CHATGPT_INSTANT_OR_CHAT_MODEL_OUT_OF_SCOPE")
    elif rule is None:
        reasons.append("MODEL_NOT_IN_CURRENT_QUALIFICATION_CATALOG")
    else:
        expected_submodels = set(rule["submodels"])
        if expected_submodels and submodel and submodel not in expected_submodels:
            reasons.append("SUBMODEL_MISMATCH_OR_UNAVAILABLE")
        if effort:
            official_efforts = set(rule["efforts"])
            extended_efforts = set(rule["codex_extended_efforts"])
            if effort not in official_efforts | extended_efforts:
                reasons.append("REASONING_EFFORT_UNSUPPORTED")
            elif effort in extended_efforts and not installed_host_tooling_proven:
                reasons.append("CODEX_EXTENDED_EFFORT_REQUIRES_INSTALLED_PROOF")
        else:
            reasons.append("REASONING_EFFORT_UNAVAILABLE")
        if speed not in _SPEED_RULES:
            reasons.append("REASONING_SPEED_UNAVAILABLE_OR_UNKNOWN")
        if not installed_host_tooling_proven:
            reasons.append("REPRESENTATIVE_INSTALLED_HOST_PROOF_REQUIRED")

    full_lifecycle_effort = False
    if rule is not None and effort in _EFFORT_RANK:
        minimum = str(rule["minimum_full_lifecycle_effort"])
        full_lifecycle_effort = _EFFORT_RANK[effort] >= _EFFORT_RANK[minimum]
        if not full_lifecycle_effort:
            reasons.append(
                "EFFORT_BELOW_FULL_GOVERNED_LIFECYCLE_QUALIFICATION"
            )

    hard_block_reasons = {
        "CHATGPT_INSTANT_OR_CHAT_MODEL_OUT_OF_SCOPE",
        "SUBMODEL_MISMATCH_OR_UNAVAILABLE",
        "REASONING_EFFORT_UNSUPPORTED",
    }
    if hard_block_reasons & set(reasons):
        status = "BLOCKED_INCOMPATIBLE_PROFILE"
        execution_allowed = False
    elif reasons:
        status = "CONDITIONAL_INSTALLED_PROOF_REQUIRED"
        execution_allowed = bool(
            installed_host_tooling_proven
            and rule is not None
            and full_lifecycle_effort
        )
    else:
        status = "SUPPORTED_PROFILE"
        execution_allowed = True

    core = {
        "schema": MODEL_COMPATIBILITY_SCHEMA,
        "status": status,
        "model": model or None,
        "submodel": submodel or None,
        "reasoning_effort": effort or None,
        "reasoning_speed": speed or None,
        "model_family": rule["family"] if rule is not None else None,
        "tool_contract": rule["tool_contract"] if rule is not None else None,
        "required_tool_capabilities": list(_CURRENT_TOOL_REQUIREMENTS),
        "installed_host_tooling_proven": bool(installed_host_tooling_proven),
        "execution_allowed": execution_allowed,
        "full_governed_lifecycle_effort_qualified": full_lifecycle_effort,
        "minimum_full_lifecycle_effort": (
            rule["minimum_full_lifecycle_effort"] if rule is not None else None
        ),
        "lower_effort_scope": (
            "READ_ONLY_OR_BOUNDED_MANUAL_STEP_PENDING_REPRESENTATIVE_EVAL"
            if rule is not None and not full_lifecycle_effort
            else None
        ),
        "reasons": sorted(set(reasons)),
        "profile_name_alone_is_proof": False,
        "work_handoff_exact_profile_replay_required": True,
        "chatgpt_surface_exposed": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}

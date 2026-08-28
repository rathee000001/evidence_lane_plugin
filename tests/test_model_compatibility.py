from __future__ import annotations

import pytest

from evidence_lane_plugin.model_compatibility import (
    classify_model_compatibility,
    model_compatibility_catalog,
)


def _profile(
    model: str,
    submodel: str | None,
    effort: str,
    speed: str = "standard",
) -> dict[str, str]:
    result = {
        "model": model,
        "reasoning_effort": effort,
        "reasoning_speed": speed,
    }
    if submodel is not None:
        result["submodel"] = submodel
    return result


def test_catalog_is_capability_based_and_keeps_chatgpt_hidden() -> None:
    catalog = model_compatibility_catalog()

    assert catalog["status"] == "PASS"
    assert catalog["policy"] == "CAPABILITY_AND_INSTALLED_PROOF_NOT_MODEL_NAME_ONLY"
    assert {row["model"] for row in catalog["models"]} >= {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
    }
    assert catalog["chatgpt_surface_exposed"] is False
    assert "instant" in catalog["blocked_chat_or_instant_models"]


@pytest.mark.parametrize(
    "profile",
    [
        _profile("gpt-5.6-sol", "sol", "xhigh"),
        _profile("gpt-5.6-terra", "terra", "high"),
        _profile("gpt-5.6-luna", "luna", "high"),
        _profile("gpt-5.5", None, "high"),
        _profile("gpt-5.4", None, "xhigh"),
        _profile("gpt-5.4-mini", None, "high"),
    ],
)
def test_known_models_support_exact_proven_installed_profiles(
    profile: dict[str, str],
) -> None:
    receipt = classify_model_compatibility(
        profile,
        installed_host_tooling_proven=True,
    )

    assert receipt["status"] == "SUPPORTED_PROFILE"
    assert receipt["execution_allowed"] is True
    assert receipt["profile_name_alone_is_proof"] is False


def test_unproven_terra_and_medium_effort_remain_conditional() -> None:
    terra = classify_model_compatibility(
        _profile("gpt-5.6-terra", "terra", "medium"),
    )
    medium = classify_model_compatibility(
        _profile("gpt-5.6-sol", "sol", "medium"),
    )

    assert terra["status"] == "CONDITIONAL_INSTALLED_PROOF_REQUIRED"
    assert "REPRESENTATIVE_INSTALLED_HOST_PROOF_REQUIRED" in terra["reasons"]
    assert medium["status"] == "CONDITIONAL_INSTALLED_PROOF_REQUIRED"
    assert "EFFORT_BELOW_FULL_GOVERNED_LIFECYCLE_QUALIFICATION" in medium[
        "reasons"
    ]


@pytest.mark.parametrize(
    "profile",
    [
        _profile("gpt-5.3-chat", None, "medium", "instant"),
        _profile("5.3 Codex Spark", None, "xhigh"),
        _profile("gpt-5.6-luna", "luna", "ultra"),
        _profile("gpt-5.6-terra", "sol", "medium"),
    ],
)
def test_incompatible_or_chat_profiles_fail_closed(profile: dict[str, str]) -> None:
    receipt = classify_model_compatibility(
        profile,
        installed_host_tooling_proven=True,
    )

    assert receipt["status"] == "BLOCKED_INCOMPATIBLE_PROFILE"
    assert receipt["execution_allowed"] is False

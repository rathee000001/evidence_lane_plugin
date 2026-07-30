from __future__ import annotations

import json

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.operating_modes import classify_operating_modes

from .conftest import boot_local


def test_explicit_mode_intersection_is_ordered_and_keeps_mode_separate() -> None:
    result = classify_operating_modes(
        "Perform forensic analysis, plan the correction, implement it, then validate.",
        explicit_modes=["AL", "PL", "CD", "VAL"],
        code_lane="local_code",
    )

    assert result["mode_intersection"] == "AL+PL+CD+VAL"
    assert result["intersection"] is True
    assert result["canonical_lanes"] == [
        "mode",
        "chat_lineage",
        "analysis",
        "plan",
        "local_code",
        "artifacts",
    ]
    assert result["code_recursive_policy"] == ["D", "PL", "CD", "VAL"]
    routes = {
        route["canonical_lane_id"]: route["command"] for route in result["lane_routes"]
    }
    assert routes["mode"] == "/evi-mode"
    assert routes["local_code"] == "/evi-03-local"
    assert result["lifecycle_effect"] == "NONE"
    assert result["pointer_moved"] is False
    assert result["candidate_created"] is False


def test_mode_inference_and_invalid_empty_selection_fail_closed() -> None:
    inferred = classify_operating_modes(
        "Research prior art, then build code and test it.",
        explicit_modes=None,
        code_lane="github_code",
    )
    assert [mode["id"] for mode in inferred["selected_modes"]] == [
        "RS",
        "CD",
        "VAL",
    ]
    assert "github_code" in inferred["canonical_lanes"]
    ordered_hil = classify_operating_modes(
        "While we review, code the fix, then HIL.",
        explicit_modes=None,
        code_lane="github_code",
    )
    assert [mode["id"] for mode in ordered_hil["selected_modes"]] == ["CD", "VAL"]

    with pytest.raises(EvidenceLaneError) as error:
        classify_operating_modes(
            "Do the thing.",
            explicit_modes=None,
            code_lane="github_code",
        )
    assert error.value.code == "MODE_SELECTION_REQUIRED"


def test_active_session_mode_receipt_preserves_lifecycle_and_pointer(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    session_before = service.sessions.load("book-faires", session_id).as_dict()
    pointer_before = service.store.pointer("book-faires").as_dict()
    exact_request = "Analyze and plan this bounded correction."

    result = service.classify_mode(
        "book-faires",
        exact_request,
        explicit_modes=["AL", "PL", "CD"],
        session_id=session_id,
    )

    session_after = service.sessions.load("book-faires", session_id).as_dict()
    assert result["chat_lineage"]["append_status"] == "APPENDED"
    assert result["prior_lifecycle_state"] == session_before["state"]
    assert result["pointer"] == pointer_before
    assert "local_code" in result["canonical_lanes"]
    assert "github_code" not in result["canonical_lanes"]
    assert session_after["state"] == session_before["state"]
    assert session_after["task"] == session_before["task"]
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    event_text = json.dumps(result["chat_lineage"], sort_keys=True)
    assert exact_request not in event_text
    assert result["next_action"] == "RETURN_TO_PRIOR_LIFECYCLE_POSITION"

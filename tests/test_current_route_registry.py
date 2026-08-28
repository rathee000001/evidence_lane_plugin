from __future__ import annotations

from evidence_lane_plugin.current_route_registry import (
    current_implementation_registry,
    current_route,
)
from evidence_lane_plugin.service import EvidenceLaneService


def test_current_registry_has_one_route_and_no_compatibility_tombstones() -> None:
    registry = current_implementation_registry()
    assert registry["status"] == "PASS"
    assert registry["obsolete_execution_allowed"] is False
    assert registry["fallback_to_historical_route_allowed"] is False
    assert registry["capability_count"] == len(registry["capabilities"])
    assert (
        len({row["capability"] for row in registry["capabilities"]})
        == registry["capability_count"]
    )
    assert all(row["status"] == "CURRENT_ROUTE" for row in registry["capabilities"])
    assert all(row["consumers"] for row in registry["capabilities"])
    assert all(row["verification"] for row in registry["capabilities"])
    assert registry["obsolete_public_tools"] == []
    assert all("obsolete_routes" not in row for row in registry["capabilities"])


def test_named_current_routes_cover_r265_cross_surface_corrections() -> None:
    assert current_route("state_travel")["current_route"] == (
        "SESSION_RESUME_THEN_SIX_FIELD_DIRECT_SAME_WORKTREE"
    )
    assert current_route("project_sub_pv")["current_route"] == (
        "PLAN_SQLITE_SUB_PV_ACCEPTANCE_POINT"
    )
    assert current_route("goal_metrics")["current_route"] == (
        "RESET_AWARE_RICH_GOAL_COMPLETION_METRICS"
    )
    assert current_route("local_install")["current_route"] == (
        "PLUGIN_CREATOR_STAGE_MATERIALIZE_PREPARE_TERMINAL_USER_RESTART_REATTACH"
    )


def test_superseded_refresh_and_sealed_state_travel_service_routes_are_purged() -> None:
    for name in (
        "prepare_state_travel",
        "resume_state_travel",
        "refresh",
        "_obsolete_prepare_state_travel",
        "_obsolete_resume_state_travel",
        "_obsolete_refresh",
    ):
        assert not hasattr(EvidenceLaneService, name)

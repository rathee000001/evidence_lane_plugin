from __future__ import annotations

from evidence_lane_plugin.next_actions import (
    PUBLIC_CONTROLS,
    boot_next_action,
    direct_command_map,
    resolve_direct_command_route,
)


def test_direct_command_map_binds_all_six_controls_to_their_existing_skills() -> None:
    command_map = direct_command_map()
    assert command_map["ordered_controls"] == list(PUBLIC_CONTROLS)
    assert [row["command"] for row in command_map["routes"]] == list(
        PUBLIC_CONTROLS
    )
    assert [row["skill"] for row in command_map["routes"]] == [
        "evi-boot",
        "evi-rollback",
        "evi-build",
        "evi-refresh",
        "evi-mode",
        "evi-source-intake",
    ]
    assert command_map["explicit_and_inferred_share_skill"] is True
    assert command_map["host_plan_sync_is_evi_refresh"] is False
    assert boot_next_action(entry_action="SESSION_RESUMED")["direct_command_map"] == (
        command_map
    )


def test_explicit_and_inferred_refresh_share_one_route_without_executing_it() -> None:
    explicit = resolve_direct_command_route("/evi-refresh changed sections")
    inferred = resolve_direct_command_route("Please refresh governed evidence now")
    assert explicit["command"] == inferred["command"] == "/evi-refresh"
    assert explicit["skill"] == inferred["skill"] == "evi-refresh"
    assert explicit["route_kind"] == "EXPLICIT_COMMAND"
    assert inferred["route_kind"] == "INFERRED_VISIBLE_INTENT"
    assert explicit["lifecycle_action_executed"] is False
    assert inferred["lifecycle_action_executed"] is False


def test_plan_reactivation_never_aliases_the_governed_refresh_command() -> None:
    for prompt in (
        "Refresh the Plan panel",
        "Rehydrate the step task list",
        "Restore the right-side Plan",
    ):
        route = resolve_direct_command_route(prompt)
        assert route["status"] == "HOST_PLAN_SURFACE_OPERATION"
        assert route["command"] is None
        assert route["skill"] == "evidence-lane-code-lifecycle"
        assert route["lifecycle_action_executed"] is False


def test_ambiguous_and_unrelated_prompts_fail_closed_without_a_route() -> None:
    ambiguous = resolve_direct_command_route(
        "Boot Evidence Lane and build an Evidence Lane candidate"
    )
    unrelated = resolve_direct_command_route("Explain the current architecture")
    assert ambiguous["status"] == "AMBIGUOUS_FAIL_CLOSED"
    assert ambiguous["command"] is None
    assert unrelated["status"] == "NO_ROUTE"
    assert unrelated["command"] is None

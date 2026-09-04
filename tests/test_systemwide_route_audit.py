from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_lane_plugin.constants import NATIVE_TOOL_COUNT
from evidence_lane_plugin.current_route_registry import (
    current_implementation_registry,
)
from evidence_lane_plugin.mcp_server import create_mcp_server
from evidence_lane_plugin.service import EvidenceLaneService
from evidence_lane_plugin.systemwide_route_audit import (
    _systemwide_regression_receipt,
    audit_plan_supersession,
    build_systemwide_route_audit,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
PLAN = Path(
    r"F:\EvidenceLaneProjects\test-codex-evidence-lane-plugin"
    r"\sectors\plan\plan_runtime_projection.sqlite"
)
SCOPED_STATUS = "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED"


def _regression_receipt(*, status: str = SCOPED_STATUS) -> dict:
    receipt = {
        "schema": "evidence-lane.systemwide-regression-receipt.v1",
        "status": status,
        "publication_authorized": False,
        "full_regression": {
            "authorized_run_count": 1,
            "full_rerun_count": 0,
            "passed": 100,
            "failed": 2,
            "skipped": 1,
        },
        "targeted_closure": {
            "status": "PASS",
            "passed": 4,
            "failed": 0,
            "skipped": 0,
            "full_suite_rerun": False,
        },
        "publication_scope": {
            "status": "DEFERRED_NOT_PASSED",
            "publication_authorized": False,
            "deferred_selector_count": 2,
            "deferral_contract_file_sha256": "A" * 64,
        },
        "skips": [{"disposition": "REQUIRED_POST_LOCAL_INSTALL_R265_TARGETED_PROOF"}],
        "failures": [{"tests": ["tests.test_one::test_a", "tests.test_two::test_b"]}],
        "boundaries": {
            "accepted_archive_queried": False,
            "pointer_moved": False,
            "git_or_main_mutated": False,
        },
    }
    if status == "PASS_WITH_TARGETED_FAILURE_CLOSURE":
        receipt.pop("publication_authorized")
        receipt.pop("publication_scope")
    return receipt


def _write_regression_receipt(path: Path, receipt: dict) -> Path:
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def test_systemwide_regression_preserves_legacy_targeted_closure(
    tmp_path: Path,
) -> None:
    path = _write_regression_receipt(
        tmp_path / "legacy.json",
        _regression_receipt(status="PASS_WITH_TARGETED_FAILURE_CLOSURE"),
    )

    result = _systemwide_regression_receipt(path)

    assert result["status"] == "PASS"
    assert result["full_failed_then_targeted_closed"] == 2
    assert "scope_status" not in result
    assert "publication_authorized" not in result


def test_systemwide_regression_accepts_scoped_local_executable_deferral(
    tmp_path: Path,
) -> None:
    path = _write_regression_receipt(
        tmp_path / "scoped.json",
        _regression_receipt(),
    )

    result = _systemwide_regression_receipt(path)

    assert result["status"] == "PASS"
    assert result["scope_status"] == SCOPED_STATUS
    assert result["full_failed"] == 2
    assert result["publication_scope_status"] == "DEFERRED_NOT_PASSED"
    assert result["publication_authorized"] is False
    assert result["deferred_publication_selector_count"] == 2
    assert result["deferral_contract_file_sha256"] == "A" * 64
    assert "full_failed_then_targeted_closed" not in result


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_publication_scope",
        "top_level_publication_authorized",
        "publication_scope_passed",
        "publication_scope_authorized",
        "missing_selector_count",
        "boolean_selector_count",
        "selector_count_exceeds_failures",
        "invalid_contract_hash",
    ],
)
def test_systemwide_regression_rejects_missing_or_mixed_scoped_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    receipt = _regression_receipt()
    publication = receipt["publication_scope"]
    if mutation == "missing_publication_scope":
        receipt.pop("publication_scope")
    elif mutation == "top_level_publication_authorized":
        receipt["publication_authorized"] = True
    elif mutation == "publication_scope_passed":
        publication["status"] = "PASS"
    elif mutation == "publication_scope_authorized":
        publication["publication_authorized"] = True
    elif mutation == "missing_selector_count":
        publication.pop("deferred_selector_count")
    elif mutation == "boolean_selector_count":
        publication["deferred_selector_count"] = True
    elif mutation == "selector_count_exceeds_failures":
        publication["deferred_selector_count"] = 3
    elif mutation == "invalid_contract_hash":
        publication["deferral_contract_file_sha256"] = "not-a-sha256"
    path = _write_regression_receipt(tmp_path / f"{mutation}.json", receipt)

    with pytest.raises(RuntimeError, match="SYSTEMWIDE_REGRESSION_RECEIPT_INVALID"):
        _systemwide_regression_receipt(path)


def test_current_registry_assigns_every_live_tool_to_one_route() -> None:
    registry = current_implementation_registry()
    rows = registry["public_tool_routes"]
    assert registry["public_tool_count"] == NATIVE_TOOL_COUNT
    assert len(rows) == len({row["tool"] for row in rows}) == NATIVE_TOOL_COUNT
    assert registry["obsolete_public_tools"] == []
    assert all(row["fallback_allowed"] is False for row in rows)
    assert all(
        row["status"] == "CURRENT_ROUTE" or row["executable"] is False for row in rows
    )


def test_plan_history_through_r265_has_no_unclassified_or_cyclic_route() -> None:
    assert PLAN.is_file()
    receipt = audit_plan_supersession(PLAN, active_row=265)
    assert receipt["status"] == "PASS"
    assert receipt["scope"]["executable_row_end"] == 265
    assert receipt["scope"]["accepted_archive_queried"] is False
    assert receipt["supersession_cycle_count"] == 0
    assert receipt["unclassified_row_count"] == 0
    assert receipt["canonical_record_count"] == 284
    assert receipt["canonical_history_record_count"] == 82
    assert receipt["canonical_executable_row_count"] == 202
    assert receipt["in_scope_record_count"] == 267
    assert receipt["scope"]["physical_final_row"] == 282
    assert receipt["scope"]["queued_rows_after_active"] == 17


def test_systemwide_route_audit_covers_every_public_consumer() -> None:
    assert PLAN.is_file()
    receipt = build_systemwide_route_audit(
        plugin_root=PLUGIN,
        repository_root=ROOT,
        plan_runtime_sqlite=PLAN,
        active_row=265,
    )
    assert receipt["status"] == "PASS"
    parity = receipt["consumer_parity"]
    assert parity["tool_count"] == NATIVE_TOOL_COUNT
    assert all(
        parity[key] is True
        for key in (
            "routing_equals_registry",
            "routing_equals_public_schema",
            "routing_equals_remote_adapter",
            "routing_equals_mcp",
        )
    )
    assert parity["specialized_subset_registered"] is True
    assert parity["sdk_route_registry_shared"] is True
    assert parity["all_public_actions_enter_internal_sdk"] is True
    assert parity["env_uop_sdk_module_complete"] is True
    assert (
        parity["internal_sdk_public_dispatch"]["internal_sdk_public_action_count"]
        == NATIVE_TOOL_COUNT
    )
    purge = receipt["obsolete_route_purge"]
    assert purge["status"] == "PASS"
    assert purge["active_workflow_violations"] == []
    assert purge["public_and_service_compatibility_routes_purged"] is True
    assert receipt["accepted_archive_queried"] is False
    assert receipt["candidate_created_or_cleared"] is False
    assert receipt["pointer_moved"] is False
    assert receipt["systemwide_regression"] == {
        "status": "NOT_SUPPLIED_UNIT_AUDIT",
        "required_before_local_package": True,
    }
    assert receipt["skill_current_route_audit"]["status"] == "PASS"
    assert receipt["skill_current_route_audit"]["skill_count"] == 26
    assert receipt["skill_current_route_audit"]["unclassified_skills"] == []
    assert receipt["skill_current_route_audit"]["missing_skill_workflows"] == []
    assert receipt["skill_current_route_audit"]["skill_count_semantics"] == (
        "DERIVED_CURRENT_INVENTORY_NO_NUMERIC_CEILING"
    )
    assert receipt["skill_current_route_audit"]["issues"] == []
    governance = receipt["env_uop_current_authority_governance"]
    assert governance["status"] == "PASS"
    assert governance["sector_lane_count"] == 18
    assert governance["current_authority_classes_derived"] is True
    assert governance["authority_merge_allowed"] is False
    assert governance["env_and_uop_are_governance_not_authority_arms"] is True
    whole_sdk = receipt["whole_plugin_sdk_governance"]
    assert whole_sdk["status"] == "PASS"
    assert whole_sdk["law"] == "ALL_PLUGIN_BEHAVIOR_INTERNAL_SDK_GOVERNED"
    assert whole_sdk["missing_adapter_capabilities"] == []
    workflow_sdk = receipt["runtime_workflow_sdk_registry"]
    assert workflow_sdk["status"] == "PASS"
    assert workflow_sdk["internal_sdk_public_action_count"] == NATIVE_TOOL_COUNT
    assert workflow_sdk["all_explicit_actions_work_with_hooks_off"] is True
    assert workflow_sdk["sdk_claims_pre_reasoning_prompt_interception"] is False


def test_source_intake_public_schema_rejects_ambiguous_route_modes(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "runtime")
    )
    tool = server._tool_manager.get_tool("source_intake_classify")
    assert tool is not None
    properties = tool.parameters["properties"]
    assert properties["git_mode"]["enum"] == ["AUTO", "REQUIRED", "DISABLED"]
    assert properties["authority_mode"]["enum"] == [
        "CLASSIFICATION_ONLY",
        "GOVERNED_CONTENT_REGISTRY",
    ]
    assert properties["working_authority_action"]["enum"] == [
        "CLASSIFY_ONLY",
        "REFRESH_WORKING_SECTORS",
    ]

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.mcp_server import create_mcp_server
from evidence_lane_plugin.service import (
    SERVICE_DISPATCH_BOUNDARY_METHODS,
    SERVICE_INTERNAL_ORCHESTRATION_METHODS,
    SERVICE_MCP_WORKFLOW_METHODS,
    SERVICE_SDK_WORKFLOW_METHODS,
    EvidenceLaneService,
    inspect_service_route_parity,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = (
    ROOT / "plugins" / "evidence-lane-plugin" / "src" / "evidence_lane_plugin"
)


def _service_references(path: Path, owner_names: set[str]) -> set[str]:
    inventory = {
        name
        for name in dir(EvidenceLaneService)
        if not name.startswith("_")
        and callable(getattr(EvidenceLaneService, name, None))
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in owner_names
        and node.attr in inventory
    }


def test_service_route_review_classifies_every_public_method() -> None:
    review = inspect_service_route_parity()

    assert review["status"] == "PASS"
    assert review["service_public_method_count"] == 56
    assert review["mcp_workflow_method_count"] == 51
    assert review["sdk_workflow_method_count"] == 20
    assert review["dispatch_boundary_method_count"] == 1
    assert review["internal_orchestration_method_count"] == 1
    assert review["eligible_unrouted_method_count"] == 0
    assert review["implementation_delta_candidates"] == []

    by_method = {row["method"]: row for row in review["methods"]}
    assert by_method["decide"] == {
        "method": "decide",
        "classification": "INTERNAL_ORCHESTRATION_ONLY",
        "workflow": "SPLIT_PROJECT_HIL_DECISION_CORE",
        "route_owners": ["record_hil_decision", "fuse"],
        "public_route_eligible": False,
        "implementation_delta_eligible": False,
        "reason": (
            "Non-promotion outcomes are owned by record_hil_decision; exact "
            "APPROVE promotion is owned exclusively by fuse."
        ),
    }
    assert by_method["invoke"]["classification"] == "MCP_RESULT_BOUNDARY"


def test_service_route_declarations_match_mcp_and_sdk_source() -> None:
    actual_mcp = _service_references(
        PACKAGE / "mcp_server.py",
        {"application", "backend_application", "service"},
    )
    actual_sdk = _service_references(PACKAGE / "internal_sdk.py", {"service"})

    assert actual_mcp == (
        set(SERVICE_MCP_WORKFLOW_METHODS)
        | set(SERVICE_DISPATCH_BOUNDARY_METHODS)
    )
    assert actual_sdk == set(SERVICE_SDK_WORKFLOW_METHODS)
    assert not (
        set(SERVICE_INTERNAL_ORCHESTRATION_METHODS) & (actual_mcp | actual_sdk)
    )


def test_unclassified_public_service_method_fails_closed() -> None:
    class DriftedService(EvidenceLaneService):
        def undeclared_product_operation(self) -> dict[str, str]:
            return {"status": "UNDECLARED"}

    with pytest.raises(EvidenceLaneError) as caught:
        inspect_service_route_parity(DriftedService)

    assert caught.value.code == "SERVICE_ROUTE_CLASSIFICATION_MISMATCH"
    assert caught.value.status == "MISMATCH"
    assert caught.value.details["unclassified_methods"] == [
        "undeclared_product_operation"
    ]


def test_mcp_construction_attaches_bounded_service_route_receipt(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(service=EvidenceLaneService(data_root=tmp_path))
    review = server._evidence_lane_service_route_review  # type: ignore[attr-defined]
    route = server._evidence_lane_native_route_receipt  # type: ignore[attr-defined]

    assert review["status"] == "PASS"
    assert review["eligible_unrouted_method_count"] == 0
    assert route["service_route_review"] == {
        "schema": review["schema"],
        "status": "PASS",
        "service_public_method_count": 56,
        "mcp_workflow_method_count": 51,
        "sdk_workflow_method_count": 20,
        "eligible_unrouted_method_count": 0,
        "receipt_sha256": review["receipt_sha256"],
    }

from __future__ import annotations

import asyncio

from evidence_lane_plugin.internal_sdk import build_local_service_adapter
from evidence_lane_plugin.mcp_server import create_mcp_server

from .test_internal_sdk_handler_parity import _binding, _context


def test_public_status_route_returns_only_bounded_projection(service) -> None:
    server = create_mcp_server(service=service)
    tool = server._tool_manager.get_tool("pv_status")
    assert tool is not None

    _content, structured = asyncio.run(
        tool.run({"project_id": "book-faires"}, convert_result=True)
    )
    assert structured["status"] == "PASS"
    data = structured["data"]

    assert data["schema"] == "evidence-lane.pv-status-window.v1"
    assert "accepted" not in data
    assert "candidates" not in data
    assert "accepted_history" not in data
    assert "lanes" not in data["lane_projection"]
    assert "absent_lane_ids" not in data["lane_projection"]
    assert data["model_context_boundary"] == {
        "accepted_history_returned": False,
        "candidate_id_list_returned": False,
        "lane_rows_returned": False,
        "full_plan_ledger_returned": False,
        "full_pv_payload_loaded": False,
        "detail_routes": [
            "pv_summary",
            "pv_query",
            "search",
            "fetch",
            "pv_task_backlog",
        ],
    }


def test_sdk_status_uses_same_bounded_service_route() -> None:
    binding = _binding()

    class StatusService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def status_window(self, project_id: str):
            self.calls.append(project_id)
            return {
                "schema": "evidence-lane.pv-status-window.v1",
                "status": "PASS",
                "model_context_boundary": {
                    "accepted_history_returned": False,
                    "full_plan_ledger_returned": False,
                },
            }

    service = StatusService()
    adapter = build_local_service_adapter(
        service, runtime_binding=binding.as_dict()
    )
    result = adapter.invoke(
        "project_truth",
        "status",
        binding,
        {},
        _context("bounded-status"),
    )

    assert service.calls == [binding.project_id]
    assert result["schema"] == "evidence-lane.pv-status-window.v1"
    assert result["model_context_boundary"]["accepted_history_returned"] is False
    assert result["model_context_boundary"]["full_plan_ledger_returned"] is False

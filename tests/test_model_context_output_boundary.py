from __future__ import annotations

import asyncio
import json

import evidence_lane_plugin.cli as cli_module
import evidence_lane_plugin.reader as reader_module
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes
from evidence_lane_plugin.mcp_server import (
    _compact_fastmcp_structured_result,
    create_mcp_server,
)
from evidence_lane_plugin.service import _MODEL_CONTEXT_RESULT_MAX_BYTES

from .conftest import build_and_approve_pv1


def test_oversized_public_result_is_replaced_by_hashed_receipt(service) -> None:
    result = service.invoke(
        "synthetic_oversized_read",
        lambda: {
            "status": "PASS",
            "project_id": "book-faires",
            "raw_markdown": "# raw authority\n" * 10_000,
        },
    )

    data = result["data"]
    assert data["schema"] == "evidence-lane.model-context-withheld-receipt.v1"
    assert data["status"] == "PASS"
    assert data["payload_withheld"] is True
    assert data["full_authority_returned"] is False
    assert data["raw_payload_returned"] is False
    assert data["result_bytes"] > data["max_public_result_bytes"]
    assert len(data["result_sha256"]) == 64
    assert "raw_markdown" not in data
    assert len(canonical_json_bytes(result)) < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_small_bounded_result_keeps_data_and_adds_boundary_receipt(service) -> None:
    result = service.invoke(
        "synthetic_bounded_query",
        lambda: {
            "status": "PASS",
            "project_id": "book-faires",
            "results": [{"task_id": "row-1", "snippet": "bounded"}],
        },
    )

    data = result["data"]
    assert data["results"] == [{"task_id": "row-1", "snippet": "bounded"}]
    assert data["public_result_boundary"]["payload_withheld"] is False
    assert data["public_result_boundary"]["bounded_public_result"] is True
    assert data["public_result_boundary"]["result_bytes"] > 0
    assert len(data["public_result_boundary"]["result_sha256"]) == 64


def test_exact_fetch_keeps_one_excerpt_and_removes_duplicate_content(service) -> None:
    repeated = "bounded source line\n" * 2_000
    result = service.invoke(
        "fetch",
        lambda: {
            "status": "PASS",
            "project_id": "book-faires",
            "ref_id": "file:README.md",
            "text": repeated,
            "content": repeated,
            "sha256": "A" * 64,
        },
    )

    data = result["data"]
    assert "content" not in data
    assert data["content_duplicate_returned"] is False
    assert "MODEL_CONTEXT_TRUNCATED" in data["text"]
    assert repeated not in json.dumps(result)
    assert data["model_context_boundary"]["exact_bounded_read"] is True
    assert len(canonical_json_bytes(result)) < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_excessive_collection_is_bounded_with_hashed_tail(service) -> None:
    result = service.invoke(
        "synthetic_bounded_query",
        lambda: {
            "status": "PASS",
            "results": [
                {"row": number, "snippet": f"bounded-{number}"}
                for number in range(1_000)
            ],
        },
    )

    results = result["data"]["results"]
    assert len(results) == 51
    assert results[-1]["payload_withheld"] is True
    assert results[-1]["reason"] == "COLLECTION_ITEM_LIMIT_EXCEEDED"
    assert results[-1]["omitted_items"] == 950
    assert len(results[-1]["result_sha256"]) == 64


def test_recursive_result_fails_closed_without_recursive_payload(service) -> None:
    recursive: dict[str, object] = {"status": "PASS"}
    recursive["self"] = recursive

    result = service.invoke("synthetic_recursive_result", lambda: recursive)

    assert result["status"] == "FAIL"
    assert result["data"] is None
    assert result["error"]["code"] == "UNEXPECTED_INTERNAL_ERROR"
    assert len(canonical_json_bytes(result)) < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_plan_task_write_returns_receipt_not_authority(service) -> None:
    rows = [
        {
            "number": number,
            "task_id": f"task-{number}",
            "status": "in_progress" if number == 24 else "pending",
            "lifecycle_status": "ACTIVE" if number == 24 else "QUEUED",
            "step": "full private Plan row " + ("x" * 2_000),
            **({"panel_role": "PHYSICALLY_FINAL_HIL"} if number == 80 else {}),
        }
        for number in range(1, 81)
    ]
    result = service.invoke(
        "pv_plan_tasks",
        lambda: {
            "status": "PASS",
            "project_id": "book-faires",
            "counts": {"ACTIVE": 1, "QUEUED": 79},
            "goal_projection": {
                "project_id": "book-faires",
                "task_count": 80,
                "canonical_task_count": 120,
                "history_task_count": 40,
                "row_start": 1,
                "row_end": 80,
                "rows": rows,
                "canonical_plan_sha256": "A" * 64,
                "projection_sha256": "B" * 64,
                "history_projection_sha256": "C" * 64,
            },
            "canonical_plan_projection": {
                "task_count": 120,
                "rows": rows,
                "projection_sha256": "A" * 64,
            },
            "history_projection": {
                "task_count": 40,
                "rows": rows,
            },
            "tasks": rows,
            "plans": [{"plan_id": "full-plan", "tasks": rows}],
        },
        lifecycle=True,
    )

    data = result["data"]
    assert data["schema"] == "evidence-lane.plan-write-receipt.v2"
    assert data["canonical_task_count"] == 120
    assert data["executable_task_count"] == 80
    assert data["active_row"] == {
        "number": 24,
        "task_id": "task-24",
        "status": "in_progress",
        "lifecycle_status": "ACTIVE",
    }
    assert data["physical_final_hil"]["task_id"] == "task-80"
    assert data["model_context_boundary"]["write_receipt_only"] is True
    assert data["model_context_boundary"]["full_backlog_returned"] is False
    assert data["model_context_boundary"]["full_plan_returned"] is False
    for forbidden in (
        "goal_projection",
        "canonical_plan_projection",
        "history_projection",
        "tasks",
        "plans",
    ):
        assert forbidden not in data
    assert len(canonical_json_bytes(result)) < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_plan_steer_write_whitelists_compact_receipt(service) -> None:
    result = service.invoke(
        "pv_plan_steer_delta",
        lambda: {
            "status": "PASS",
            "idempotent_reuse": False,
            "steer": {
                "delta_id": "bounded-steer",
                "delta_sha256": "A" * 64,
                "classification": "LINKED_EXISTING_STEP",
                "linked_task_id": "task-1",
                "delta_text_returned": False,
                "text": "raw steer text must not cross the boundary",
            },
            "task_count": 8,
            "task_count_changed": False,
            "tasks": [{"description": "x" * 100_000}],
            "plans": [{"description": "y" * 100_000}],
            "backlog_receipt": {
                "schema": "evidence-lane.plan-backlog-write-receipt.v1",
                "project_id": "book-faires",
                "canonical_task_count": 12,
                "executable_task_count": 8,
                "full_backlog_returned": False,
                "full_plan_returned": False,
            },
        },
        lifecycle=True,
    )

    data = result["data"]
    assert data["schema"] == "evidence-lane.plan-steer-write-receipt.v2"
    assert data["steer"]["delta_id"] == "bounded-steer"
    assert "text" not in data["steer"]
    assert "tasks" not in data
    assert "plans" not in data
    assert data["model_context_boundary"]["write_receipt_only"] is True
    assert len(canonical_json_bytes(result)) < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_task_activity_write_returns_only_compact_append_receipt(service) -> None:
    result = service.invoke(
        "task_record_activity",
        lambda: {
            "status": "PASS",
            "event": {
                "event_id": "task6-row224-bounded-output-pass-20260816-001",
                "event_type": "task.test.output",
                "event_sha256": "D" * 64,
                "occurred_at": "2026-08-16T10:00:00Z",
                "session_id": "session-test",
                "task_id": "row224",
                "run_id": "run-test",
                "visible_payload": {
                    "raw_markdown": "private activity payload " * 10_000,
                    "full_plan": [{"description": "x" * 10_000}],
                },
            },
            "observed_experience": {
                "schema": "evidence-lane.observed-experience.v1",
                "status": "PASS",
                "packet_id": "packet-1",
                "packet_sha256": "E" * 64,
                "raw_packet": "private packet " * 10_000,
            },
            "host_plan_rehydration": {
                "schema": "evidence-lane.host-plan-rehydration.v1",
                "status": "PASS",
                "project_id": "book-faires",
                "full_plan": [{"description": "y" * 10_000}],
            },
            "source_state": "ENTRY_MATCH",
            "accepted_pv_query_scope": "ENTRY_STATE_ONLY",
        },
        lifecycle=True,
    )

    data = result["data"]
    assert data["schema"] == "evidence-lane.task-activity-write-receipt.v1"
    assert data["write_committed"] is True
    assert data["activity_type"] == "test.output"
    assert data["event"] == {
        "event_id": "task6-row224-bounded-output-pass-20260816-001",
        "event_type": "task.test.output",
        "event_sha256": "D" * 64,
        "occurred_at": "2026-08-16T10:00:00Z",
        "session_id": "session-test",
        "task_id": "row224",
        "run_id": "run-test",
    }
    assert data["model_context_boundary"]["write_receipt_only"] is True
    assert data["model_context_boundary"]["visible_payload_returned"] is False
    assert data["model_context_boundary"]["full_chatlineage_returned"] is False
    serialized = canonical_json_bytes(result)
    assert b"private activity payload" not in serialized
    assert b"private packet" not in serialized
    assert len(serialized) < 8_192


def test_oversized_warnings_are_hashed_not_returned(service) -> None:
    result = service.invoke(
        "synthetic_warning_read",
        lambda: {
            "status": "PASS",
            "result": "bounded",
            "warnings": ["raw warning " * 10_000],
        },
    )

    assert result["data"]["result"] == "bounded"
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert warning["schema"] == (
        "evidence-lane.model-context-auxiliary-withheld-receipt.v1"
    )
    assert warning["kind"] == "warnings"
    assert warning["payload_withheld"] is True
    assert "raw warning" not in json.dumps(result)
    assert len(canonical_json_bytes(result)) < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_oversized_error_details_keep_identity_and_withhold_payload(service) -> None:
    error = EvidenceLaneError(
        code="BOUNDED_FAILURE",
        message="Exact public failure",
        details={"raw_markdown": "private detail " * 10_000},
    )
    result = service.invoke(
        "synthetic_error",
        lambda: (_ for _ in ()).throw(error),
    )

    assert result["status"] == "FAIL"
    assert result["error"]["code"] == "BOUNDED_FAILURE"
    assert result["error"]["message"] == "Exact public failure"
    assert result["error"]["payload_withheld"] is True
    assert result["error"]["detail_keys"] == ["raw_markdown"]
    assert "private detail" not in json.dumps(result)
    assert len(canonical_json_bytes(result)) < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_fastmcp_text_content_does_not_duplicate_structured_receipt(service) -> None:
    structured = service.invoke(
        "synthetic_bounded_query",
        lambda: {
            "status": "PASS",
            "results": [{"snippet": "bounded"}],
        },
    )
    original = ([{"type": "text", "text": json.dumps(structured)}], structured)

    content, exact_structured = _compact_fastmcp_structured_result(
        "synthetic_bounded_query",
        original,
    )

    assert exact_structured == structured
    assert len(content) == 1
    text_receipt = json.loads(content[0].text)
    assert text_receipt["structured_receipt_authoritative"] is True
    assert text_receipt["duplicate_structured_json_returned"] is False
    assert "results" not in text_receipt
    combined_bytes = len(content[0].text.encode("utf-8")) + len(
        canonical_json_bytes(exact_structured)
    )
    assert combined_bytes < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_live_fastmcp_call_returns_compact_text_and_structured_receipt(service) -> None:
    server = create_mcp_server(service=service)

    content, structured = asyncio.run(server.call_tool("runtime_doctor", {}))

    assert len(content) == 1
    text_receipt = json.loads(content[0].text)
    assert text_receipt["tool"] == "runtime_doctor"
    assert text_receipt["duplicate_structured_json_returned"] is False
    assert structured["tool"] == "runtime_doctor"
    combined_bytes = len(content[0].text.encode("utf-8")) + len(
        canonical_json_bytes(structured)
    )
    assert combined_bytes < _MODEL_CONTEXT_RESULT_MAX_BYTES


def test_bounded_reader_query_does_not_reapply_the_promotion_gate(
    service,
    source_repository,
) -> None:
    build_and_approve_pv1(service)
    from .test_lifecycle import _materialize_live_root_sectors

    _materialize_live_root_sectors(service, source_repository)
    assert not hasattr(reader_module, "validate_pv_package")

    result = service.reader.query("book-faires", "imports", value="flask", limit=1)

    assert result["status"] == "PASS"
    assert result["accepted_archive_opened"] is False
    assert result["accepted_archive_queried"] is False
    assert result["retrieval_authority"] == "LIVE_ROOT_SECTORS_LOCAL_CODE"


def test_cli_emits_the_shared_bounded_envelope(service, capsys) -> None:
    cli_module._emit_bounded_result(
        service,
        "validate-pv",
        {
            "status": "PASS",
            "project_id": "book-faires",
            "raw_package_projection": "sealed authority " * 10_000,
        },
    )

    output = capsys.readouterr().out.strip()
    envelope = json.loads(output)
    assert envelope["tool"] == "validate-pv"
    assert envelope["data"]["payload_withheld"] is True
    assert envelope["data"]["raw_payload_returned"] is False
    assert "sealed authority" not in output
    assert len(output.encode("utf-8")) < _MODEL_CONTEXT_RESULT_MAX_BYTES

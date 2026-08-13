from __future__ import annotations

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError

from .conftest import boot_local


def test_progressive_query_labels_candidate_and_accepted_authority(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    initial = service.build_initial("book-faires", session_id)
    candidate_id = initial["candidate"]["candidate_id"]

    candidate_search = service.reader.search(
        "book-faires",
        "list_books",
        pv_ref=candidate_id,
    )
    assert candidate_search["authority_state"] == "UNACCEPTED_CANDIDATE"
    assert candidate_search["accepted_truth"] is False
    assert candidate_search["warnings"]
    assert candidate_search["results"]
    candidate_provenance = candidate_search["results"][0]["provenance"]
    assert candidate_provenance["project_id"] == "book-faires"
    assert candidate_provenance["lane_id"] == "github_code"
    assert candidate_provenance["accepted_truth"] is False
    assert candidate_provenance["pv_ref"] == candidate_id
    assert candidate_provenance["source_locator"]
    assert len(candidate_provenance["source_locator_sha256"]) == 64
    assert {
        "id",
        "ref_id",
        "title",
        "url",
        "snippet",
        "metadata",
    } <= candidate_search["results"][0].keys()

    service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_reader_pv1",
    )
    accepted_search = service.reader.search("book-faires", "list_books")
    assert accepted_search["authority_state"] == "CURRENT_ACCEPTED_PV"
    assert accepted_search["accepted_truth"] is True
    assert accepted_search["accepted_pv"] == "PV1"
    assert accepted_search["source_commit"]
    assert accepted_search["candidate_overlay_used"] is False
    assert accepted_search["no_hit_is_valid"] is False
    with pytest.raises(EvidenceLaneError) as blocked_overlay:
        service.reader.search(
            "book-faires",
            "list_books",
            candidate_overlay_ref=candidate_id,
            candidate_overlay_authorization="AUTHORIZE_CANDIDATE_OVERLAY:wrong",
        )
    assert blocked_overlay.value.code == "CANDIDATE_OVERLAY_AUTHORIZATION_REQUIRED"
    overlay = service.reader.search(
        "book-faires",
        "list_books",
        candidate_overlay_ref=candidate_id,
        candidate_overlay_authorization=(
            f"AUTHORIZE_CANDIDATE_OVERLAY:{candidate_id}"
        ),
    )
    assert overlay["schema"] == "evidence-lane.governed-retrieval.v1"
    assert overlay["candidate_overlay_used"] is True
    assert overlay["accepted_and_candidate_results_separated"] is True
    assert overlay["accepted_result_count"] > 0
    assert overlay["candidate_overlay_result_count"] > 0
    assert overlay["candidate_overlay_authorization_stored"] is False
    assert overlay["scrollback_used"] is False
    assert overlay["transcript_used"] is False
    no_hit = service.reader.search("book-faires", "definitely_no_such_evidence_987")
    assert no_hit["status"] == "EMPTY"
    assert no_hit["results"] == []
    assert no_hit["no_hit_is_valid"] is True


def test_fetch_supports_symbol_and_bounded_file_lines(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_fetch_pv1",
    )

    search = service.reader.search("book-faires", "list_books")
    symbol = next(
        result for result in search["results"] if result["id"].startswith("symbol:")
    )
    symbol_result = service.reader.fetch("book-faires", symbol["id"])
    assert symbol_result["metadata"]["kind"] == "symbol"
    assert symbol_result["authority_state"] == "CURRENT_ACCEPTED_PV"
    assert "def list_books" in symbol_result["text"]

    file_result = service.reader.fetch(
        "book-faires",
        "file:src/app.py",
        start_line=5,
        end_line=20,
        max_lines=2,
    )
    assert file_result["start_line"] == 5
    assert file_result["end_line"] == 6
    assert file_result["truncated"] is True
    assert '@app.get("/books")' in file_result["text"]
    assert "def list_books" in file_result["text"]
    assert file_result["metadata"]["file_sha256"] == file_result["sha256"]
    assert file_result["url"].endswith("/src/app.py#L5-L6")


def test_query_exposes_imports_and_path_fallback(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_imports_pv1",
    )

    imports = service.reader.query(
        "book-faires",
        "imports",
        value="flask",
    )
    assert imports["status"] == "PASS"
    assert imports["rows"][0]["path"] == "src/app.py"
    assert imports["rows"][0]["module"] == "flask"
    assert imports["authority_state"] == "CURRENT_ACCEPTED_PV"

    path_search = service.reader.search("book-faires", "fixture.bin")
    file_result = next(
        result
        for result in path_search["results"]
        if result["id"] == "file:fixture.bin"
    )
    assert file_result["metadata"]["kind"] == "file"

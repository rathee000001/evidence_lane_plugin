from __future__ import annotations

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

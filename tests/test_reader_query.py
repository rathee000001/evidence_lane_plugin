from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError

from .conftest import boot_local, build_and_approve_pv1


def _build_bound_pv2_candidate(service) -> tuple[str, str]:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Verify governed retrieval against unchanged PV1 bytes.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=["Bounded accepted and candidate retrieval passes."],
        stop_condition="Stop at the unaccepted PV2 HIL gate.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    candidate = service.refresh("book-faires", session_id)["candidate"]
    return session_id, candidate["candidate_id"]


def test_progressive_query_labels_candidate_and_accepted_authority(service) -> None:
    session_id, candidate_id = _build_bound_pv2_candidate(service)

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
    assert overlay["candidate_overlay_binding"]["status"] == "PASS"
    assert overlay["candidate_overlay_binding"]["accepted_pv"] == "PV1"
    assert overlay["candidate_overlay_binding"]["candidate_pv_ref"] == candidate_id
    assert overlay["scrollback_used"] is False
    assert overlay["transcript_used"] is False
    assert overlay["browser_history_used"] is False
    assert overlay["live_source_used"] is False
    no_hit = service.reader.search("book-faires", "definitely_no_such_evidence_987")
    assert no_hit["status"] == "PASS"
    assert no_hit["result_state"] == "EMPTY"
    assert no_hit["results"] == []
    assert no_hit["no_hit_is_valid"] is True

    service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_reader_pv2",
    )
    with pytest.raises(EvidenceLaneError) as stale_overlay:
        service.reader.search(
            "book-faires",
            "list_books",
            pv_ref="PV1",
            candidate_overlay_ref=candidate_id,
            candidate_overlay_authorization=(
                f"AUTHORIZE_CANDIDATE_OVERLAY:{candidate_id}"
            ),
        )
    assert stale_overlay.value.code == "CANDIDATE_OVERLAY_STALE_POINTER"
    assert stale_overlay.value.status == "STALE"


def test_candidate_overlay_rejects_unbound_parent_contract(service) -> None:
    accepted = {
        "authority_state": "CURRENT_ACCEPTED_PV",
        "current_accepted": True,
        "pv_ref": "PV12",
        "accepted_pv": "PV12",
        "accepted_manifest_sha256": "A" * 64,
        "accepted_pointer_generation": 12,
    }
    overlay = {
        "package_project_id": "book-faires",
        "package_entry_project_id": "book-faires",
        "package_entry_accepted_pv": "PV11",
        "package_entry_pointer_generation": 12,
        "candidate_parent_accepted_pv": "PV11",
        "candidate_parent_manifest_sha256": "B" * 64,
        "pv_ref": "PV13_CANDIDATE__RUN_UNBOUND",
    }
    with pytest.raises(EvidenceLaneError) as unbound:
        service.reader._require_candidate_overlay_binding(
            "book-faires",
            accepted,
            overlay,
        )
    assert unbound.value.code == "CANDIDATE_OVERLAY_BASE_BINDING_MISMATCH"
    assert unbound.value.status == "BLOCKED"


def test_reader_rejects_cross_project_candidate_copy(
    service,
    source_repository: Path,
    tmp_path: Path,
) -> None:
    boot = boot_local(service)
    candidate_id = service.build_initial(
        "book-faires",
        boot["session"]["session_id"],
    )["candidate"]["candidate_id"]
    other_repository = tmp_path / "other-project-source"
    shutil.copytree(source_repository, other_repository)
    service.register_project(
        project_id="other-project",
        display_name="Other Project",
        repository_path=str(other_repository),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    copied_candidate = service.store.candidate_path("other-project", candidate_id)
    copied_candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        service.store.candidate_path("book-faires", candidate_id),
        copied_candidate,
    )
    with pytest.raises(EvidenceLaneError) as cross_project:
        service.reader.search(
            "other-project",
            "list_books",
            pv_ref=candidate_id,
        )
    assert cross_project.value.code == "PV_PROJECT_BINDING_MISMATCH"
    assert cross_project.value.status == "MISMATCH"


def test_search_uses_immutable_pv_not_live_source_browser_or_scrollback(
    service,
    source_repository: Path,
) -> None:
    build_and_approve_pv1(service)
    app_path = source_repository / "src" / "app.py"
    app_path.write_text(
        app_path.read_text(encoding="utf-8")
        + "\nLIVE_SOURCE_ONLY_SENTINEL = 'not accepted evidence'\n",
        encoding="utf-8",
    )

    result = service.reader.search("book-faires", "LIVE_SOURCE_ONLY_SENTINEL")

    assert result["status"] == "PASS"
    assert result["result_state"] == "EMPTY"
    assert result["live_truth_status"] == "DIRTY_WORKING_TREE"
    assert result["retrieval_authority"] == "IMMUTABLE_PV_PACKAGE"
    assert result["results"] == []
    assert result["no_hit_is_valid"] is True
    assert result["live_source_used"] is False
    assert result["browser_history_used"] is False
    assert result["scrollback_used"] is False
    assert result["transcript_used"] is False


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

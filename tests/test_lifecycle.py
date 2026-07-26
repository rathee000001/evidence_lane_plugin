from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError

from .conftest import build_and_approve_pv1


def test_full_pv1_task_pv2_approve_next_entry_proves_pv3(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    task = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome="Return a deterministic book count.",
        permitted_paths=["src/app.py"],
        permitted_tools=[
            "pv_search",
            "pv_fetch",
            "repository_read",
            "repository_write",
            "terminal",
            "test",
            "git_diff",
        ],
        acceptance_checks=["GET /books includes count=1"],
        stop_condition="Stop after the bounded test and PV Exit candidate.",
    )
    assert task["task"]["write_boundary"] == "AUTHORIZED_SANDBOX_PATHS_ONLY"
    application_file = source_repository / "src" / "app.py"
    application_file.write_text(
        application_file.read_text(encoding="utf-8").replace(
            'return {"books": ["Dune"]}',
            'return {"books": ["Dune"], "count": 1}',
        ),
        encoding="utf-8",
    )
    activity = service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "src/app.py", "reason": "bounded fixture change"},
        event_id="evt_bounded_change",
    )
    assert activity["source_state"] == "MUTATED_AFTER_ENTRY"
    assert activity["accepted_pv_query_scope"] == "ENTRY_STATE_ONLY"
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    refresh = service.refresh("book-faires", session_id)
    assert refresh["candidate"]["proposed_pv"] == "PV2"
    assert [
        row["path"] for row in refresh["candidate"]["source_delta"]["modified"]
    ] == ["src/app.py"]
    decision = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_pv2",
    )
    assert decision["pointer_advanced"] is True
    assert decision["pointer"]["accepted_pv"] == "PV2"
    assert decision["pointer"]["generation"] == 2
    assert decision["session"]["metadata"]["source_state"] == "ACCEPTED_ENTRY_EXACT"
    next_turn = service.sessions.begin_next_turn("book-faires", session_id)
    assert next_turn["proof"] == {
        "accepted_entry_pv": "PV2",
        "pointer_generation": 2,
        "next_candidate_would_be": "PV3",
    }
    diff = service.reader.diff("book-faires", "PV1", "PV2")
    assert [row["path"] for row in diff["modified"]] == ["src/app.py"]


@pytest.mark.parametrize(
    ("decision", "kwargs", "state"),
    [
        (
            "APPROVE_WITH_DELTA",
            {"correction_delta": "Correct only src/app.py return shape."},
            "CORRECTION_TASK_PENDING",
        ),
        (
            "MORE_RESEARCH",
            {"research_question": "Does the route contract require pagination?"},
            "RESEARCH_TASK_PENDING",
        ),
        ("REJECT", {"reason": "Fixture does not meet the contract."}, "REJECTED_RUN"),
        ("FAIL", {"reason": "Gate TEST_OUTPUT_MISSING failed."}, "FAILED_RUN"),
    ],
)
def test_nonapprove_outcomes_preserve_pointer(
    service,
    decision: str,
    kwargs: dict,
    state: str,
) -> None:
    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    result = service.decide(
        "book-faires",
        session_id,
        decision=decision,
        decided_by="human-test",
        decision_id=f"decision_{decision.lower()}",
        **kwargs,
    )
    assert result["pointer_advanced"] is False
    assert result["pointer"]["accepted_pv"] is None
    assert result["pointer"]["generation"] == 0
    assert result["session"]["state"] == state


def test_approve_with_delta_requires_exact_delta(service) -> None:
    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    with pytest.raises(EvidenceLaneError) as error:
        service.sessions.decide(
            "book-faires",
            session_id,
            decision="APPROVE_WITH_DELTA",
            decided_by="human-test",
        )
    assert error.value.code == "CORRECTION_DELTA_REQUIRED"


def test_approve_with_delta_resumes_only_the_exact_correction(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome="Return a deterministic book count.",
        permitted_paths=["src/app.py"],
        permitted_tools=["repository_write", "test"],
        acceptance_checks=["GET /books includes count=1"],
        stop_condition="Stop at the PV2 HIL gate.",
    )
    application_file = source_repository / "src" / "app.py"
    application_file.write_text(
        application_file.read_text(encoding="utf-8").replace(
            'return {"books": ["Dune"]}',
            'return {"books": ["Dune"], "count": 1}',
        ),
        encoding="utf-8",
    )
    service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "src/app.py"},
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    exact_delta = "Correct only src/app.py return shape."
    decision = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE_WITH_DELTA",
        decided_by="human-test",
        correction_delta=exact_delta,
        decision_id="decision_exact_delta",
    )
    assert decision["session"]["task"] is None
    assert decision["session"]["candidate_id"] is None
    assert (
        decision["session"]["metadata"]["pending_task"]["required_task_class"]
        == "fix_bug"
    )

    with pytest.raises(EvidenceLaneError) as wrong_class:
        service.sessions.classify(
            "book-faires",
            session_id,
            task_class="research",
            requested_outcome=exact_delta,
            permitted_paths=[],
            permitted_tools=["repository_read"],
            acceptance_checks=[],
            stop_condition="Stop after the bounded check.",
        )
    assert wrong_class.value.code == "PENDING_TASK_CLASS_MISMATCH"

    with pytest.raises(EvidenceLaneError) as wrong_outcome:
        service.sessions.classify(
            "book-faires",
            session_id,
            task_class="fix_bug",
            requested_outcome="Broaden the correction.",
            permitted_paths=["src/app.py"],
            permitted_tools=["repository_write", "test"],
            acceptance_checks=["Exact return-shape test passes"],
            stop_condition="Stop at the next HIL gate.",
        )
    assert wrong_outcome.value.code == "PENDING_TASK_OUTCOME_MISMATCH"

    resumed = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome=exact_delta,
        permitted_paths=["src/app.py"],
        permitted_tools=["repository_write", "test"],
        acceptance_checks=["Exact return-shape test passes"],
        stop_condition="Stop at the next HIL gate.",
    )
    assert "pending_task" not in resumed["session"]["metadata"]
    assert resumed["session"]["metadata"]["task_source_basis"]["kind"] == (
        "HIL_CANDIDATE_SOURCE"
    )
    assert service.store.pointer("book-faires").generation == 1


def test_more_research_resumes_only_as_exact_research(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Verify the route contract.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=[],
        stop_condition="Stop at the PV2 HIL gate.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    question = "Does the route contract require pagination?"
    service.decide(
        "book-faires",
        session_id,
        decision="MORE_RESEARCH",
        decided_by="human-test",
        research_question=question,
        decision_id="decision_exact_research",
    )
    resumed = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="research",
        requested_outcome=question,
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=[],
        stop_condition="Stop after the bounded research answer.",
    )
    assert resumed["task"]["task_class"] == "research"
    assert resumed["session"]["metadata"]["source_state"] == (
        "PENDING_CANDIDATE_SOURCE_EXACT"
    )


def test_return_to_accepted_requires_exact_source_restore(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome="Return a deterministic book count.",
        permitted_paths=["src/app.py"],
        permitted_tools=["repository_write", "test"],
        acceptance_checks=["GET /books includes count=1"],
        stop_condition="Stop at the PV2 HIL gate.",
    )
    application_file = source_repository / "src" / "app.py"
    original = application_file.read_text(encoding="utf-8")
    application_file.write_text(
        original.replace(
            'return {"books": ["Dune"]}',
            'return {"books": ["Dune"], "count": 1}',
        ),
        encoding="utf-8",
    )
    service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "src/app.py"},
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    service.decide(
        "book-faires",
        session_id,
        decision="REJECT",
        decided_by="human-test",
        reason="The bounded change is rejected.",
        decision_id="decision_rejected_pv2",
    )
    with pytest.raises(EvidenceLaneError) as not_restored:
        service.sessions.return_to_accepted(
            "book-faires",
            session_id,
            reason="Return to PV1.",
        )
    assert not_restored.value.code == "ACCEPTED_SOURCE_RESTORE_REQUIRED"

    application_file.write_text(original, encoding="utf-8")
    returned = service.sessions.return_to_accepted(
        "book-faires",
        session_id,
        reason="Rejected source removed; return to PV1.",
    )
    assert returned["pointer_advanced"] is False
    assert returned["pointer"]["accepted_pv"] == "PV1"
    assert returned["pointer"]["generation"] == 1
    assert returned["session"]["state"] == "PVN_ACCEPTED"
    assert returned["session"]["metadata"]["source_state"] == "ACCEPTED_ENTRY_EXACT"


def test_return_to_accepted_blocks_without_any_accepted_pv(service) -> None:
    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    service.decide(
        "book-faires",
        session_id,
        decision="FAIL",
        decided_by="human-test",
        reason="Initial candidate gate failed.",
        decision_id="decision_initial_fail",
    )
    with pytest.raises(EvidenceLaneError) as no_entry:
        service.sessions.return_to_accepted(
            "book-faires",
            session_id,
            reason="Attempt return.",
        )
    assert no_entry.value.code == "RETURN_TO_ACCEPTED_NO_ACCEPTED_PV"


def test_pointer_compare_and_swap_blocks_stale_promotion(service) -> None:
    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    candidate = service.build_initial("book-faires", session_id)["candidate"]
    with pytest.raises(EvidenceLaneError) as error:
        service.store.promote(
            "book-faires",
            candidate["candidate_id"],
            expected_pointer_generation=9,
            decided_by="human-test",
            decision_id="decision_stale",
        )
    assert error.value.code == "POINTER_COMPARE_AND_SWAP_FAILED"
    assert service.store.pointer("book-faires").accepted_pv is None

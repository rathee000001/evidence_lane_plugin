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
    service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "src/app.py", "reason": "bounded fixture change"},
        event_id="evt_bounded_change",
    )
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

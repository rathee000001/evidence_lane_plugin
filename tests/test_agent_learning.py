from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_lane_plugin.agent_learning import (
    decide_learning_candidate,
    expire_learning_candidates,
    inspect_learning_authority,
    project_truth_pointer_sha256,
    retrieve_accepted_learning,
    revoke_learning_candidate,
    seal_learning_candidate,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import atomic_write_json, sha256_bytes

PROJECT_ID = "learning-fixture"
T0 = "2026-08-13T12:00:00+00:00"
T1 = "2026-08-13T13:00:00+00:00"
T2 = "2026-08-13T14:00:00+00:00"
T3 = "2026-08-13T15:00:00+00:00"


def _hash(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _root(tmp_path: Path, project_id: str = PROJECT_ID) -> Path:
    root = tmp_path / project_id
    root.mkdir()
    atomic_write_json(
        root / "project.json",
        {"schema": "fixture.project.v1", "project_id": project_id},
    )
    atomic_write_json(
        root / "active_pointer.json",
        {
            "schema": "evidence-lane.pointer.v1",
            "project_id": project_id,
            "accepted_pv": "PV12",
            "generation": 12,
            "accepted_manifest_sha256": _hash("project-manifest"),
        },
    )
    return root


def _evidence(
    label: str = "source", *, project_id: str = PROJECT_ID
) -> dict[str, str]:
    return {
        "project_id": project_id,
        "task_id": "task-fixture",
        "delta_id": "EL-FIXTURE-DELTA-001",
        "pv_ref": "PV12",
        "ref": f"lineage://{label}",
        "sha256": _hash(label),
    }


def _seal(
    root: Path,
    *,
    statement: str = "Retry only after a fresh capability receipt.",
    tier: str = "DELTA_OBSERVATION",
    expires_at: str | None = "2026-08-14T12:00:00+00:00",
    supersedes: str | None = None,
    scope: dict | None = None,
    evidence: list[dict] | None = None,
) -> dict:
    return seal_learning_candidate(
        root,
        project_id=PROJECT_ID,
        tier=tier,
        lesson_type="FAILURE_AVOIDANCE",
        statement=statement,
        scope=scope or {"kind": "PROJECT", "selectors": [PROJECT_ID]},
        evidence=evidence or [_evidence(statement)],
        outcome="SUCCEEDED",
        confidence=0.85,
        counterevidence=[_evidence("counterevidence")],
        contradictions=["truth://known-limit"],
        temporal={
            "observed_at": T0,
            "valid_from": T1,
            "expires_at": expires_at,
        },
        privacy="PROJECT_PRIVATE",
        source_lineage_head_sha256=_hash("lineage-head"),
        supersedes=supersedes,
    )


def _decide(
    root: Path,
    sealed: dict,
    token: str,
    *,
    decided_at: str = T2,
) -> dict:
    candidate = sealed["candidate"]
    return decide_learning_candidate(
        root,
        project_id=PROJECT_ID,
        candidate_id=candidate["candidate_id"],
        expected_candidate_sha256=candidate["candidate_sha256"],
        decision_token=token,
        actor_id="fixture-user",
        decided_at=decided_at,
        expected_project_truth_pointer_sha256=project_truth_pointer_sha256(
            root, project_id=PROJECT_ID
        ),
    )


def test_candidate_is_immutable_provenanced_and_idempotent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = _seal(root)
    second = _seal(root)

    assert first["state"] == "PENDING_LEARNING_HIL"
    assert first["candidate"]["evidence"][0]["pv_ref"] == "PV12"
    assert first["private_reasoning_stored"] is False
    assert second["idempotent_reuse"] is True
    assert second["candidate"] == first["candidate"]
    assert inspect_learning_authority(root, project_id=PROJECT_ID)[
        "candidate_count"
    ] == 1


def test_exact_approve_moves_learning_pointer_only(tmp_path: Path) -> None:
    root = _root(tmp_path)
    sealed = _seal(root)
    project_before = (root / "active_pointer.json").read_bytes()

    result = _decide(root, sealed, "APPROVE")

    assert result["receipt"]["learning_pointer_moved"] is True
    assert result["receipt"]["project_truth_pointer_moved"] is False
    assert result["receipt"]["project_hil_invoked"] is False
    assert (root / "active_pointer.json").read_bytes() == project_before
    assert inspect_learning_authority(root, project_id=PROJECT_ID)[
        "current_pointer"
    ]["generation"] == 1

    replay = _decide(root, sealed, "APPROVE")
    assert replay["idempotent_reuse"] is True
    assert replay["receipt"] == result["receipt"]


@pytest.mark.parametrize(
    ("token", "state"),
    [
        ("APPROVE_WITH_DELTA: narrow the host scope", "CORRECTION_REQUESTED"),
        ("MORE_RESEARCH: reproduce on a second host", "RESEARCH_REQUESTED"),
        ("REJECT: evidence is not causal", "REJECTED"),
        ("FAIL: privacy boundary missing", "FAILED"),
    ],
)
def test_nonapprove_decisions_preserve_both_pointers(
    tmp_path: Path, token: str, state: str
) -> None:
    root = _root(tmp_path)
    sealed = _seal(root, statement=token)
    project_before = (root / "active_pointer.json").read_bytes()

    result = _decide(root, sealed, token)

    assert result["receipt"]["state_after"] == state
    assert result["receipt"]["learning_pointer_moved"] is False
    assert result["receipt"]["project_truth_pointer_moved"] is False
    assert (root / "active_pointer.json").read_bytes() == project_before


@pytest.mark.parametrize(
    "token",
    [
        "approve",
        "APPROVE ",
        "APPROVE_WITH_DELTA",
        "MORE_RESEARCH:",
        "ROLLBACK: PV1",
        "REJECT",
        "FAIL",
    ],
)
def test_implicit_or_malformed_learning_hil_is_rejected(
    tmp_path: Path, token: str
) -> None:
    root = _root(tmp_path)
    sealed = _seal(root, statement=token)

    with pytest.raises(EvidenceLaneError) as blocked:
        _decide(root, sealed, token)

    assert blocked.value.code == "LEARNING_DECISION_TOKEN_INVALID"
    assert not (root / "learning" / "active_pointer.json").exists()


def test_supersession_and_learning_only_rollback_preserve_history(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    project_before = (root / "active_pointer.json").read_bytes()
    first = _seal(root, statement="Use bounded retry receipts.")
    _decide(root, first, "APPROVE")
    second = _seal(
        root,
        statement="Use capability receipts before any retry.",
        supersedes=first["candidate"]["candidate_id"],
    )
    _decide(root, second, "APPROVE", decided_at=T3)

    after_second = inspect_learning_authority(root, project_id=PROJECT_ID)
    assert after_second["current_pointer"]["generation"] == 2
    assert after_second["candidate_states"][first["candidate"]["candidate_id"]] == (
        "SUPERSEDED"
    )

    rollback = _decide(root, second, "ROLLBACK: LGEN1", decided_at="2026-08-13T16:00:00Z")
    after_rollback = inspect_learning_authority(root, project_id=PROJECT_ID)

    assert rollback["receipt"]["state_after"] == "ROLLED_BACK"
    assert after_rollback["current_pointer"]["generation"] == 3
    assert after_rollback["current_pointer"]["accepted_candidate_id"] == first[
        "candidate"
    ]["candidate_id"]
    assert after_rollback["candidate_states"][first["candidate"]["candidate_id"]] == (
        "ACCEPTED"
    )
    assert (root / "active_pointer.json").read_bytes() == project_before


def test_expiry_and_revocation_remove_lessons_from_retrieval(tmp_path: Path) -> None:
    root = _root(tmp_path)
    expiring = _seal(
        root,
        statement="Expiry bounded retry lesson.",
        expires_at="2026-08-13T14:30:00+00:00",
    )
    _decide(root, expiring, "APPROVE", decided_at=T2)
    assert retrieve_accepted_learning(
        root,
        project_id=PROJECT_ID,
        query="expiry retry",
        scope_selectors=[PROJECT_ID],
        as_of="2026-08-13T14:15:00+00:00",
    )["result"] == "HIT"

    expired = expire_learning_candidates(
        root,
        project_id=PROJECT_ID,
        as_of="2026-08-13T14:31:00+00:00",
    )
    assert expired["expired_candidate_ids"] == [expiring["candidate"]["candidate_id"]]
    assert retrieve_accepted_learning(
        root,
        project_id=PROJECT_ID,
        query="expiry retry",
        scope_selectors=[PROJECT_ID],
        as_of="2026-08-13T14:31:00+00:00",
    )["result"] == "NO_HIT"

    revocable = _seal(root, statement="Revocable tool routing lesson.")
    _decide(root, revocable, "APPROVE", decided_at="2026-08-13T16:00:00Z")
    revoke_learning_candidate(
        root,
        project_id=PROJECT_ID,
        candidate_id=revocable["candidate"]["candidate_id"],
        reason="new counterevidence",
        revoked_at="2026-08-13T17:00:00Z",
    )
    assert retrieve_accepted_learning(
        root,
        project_id=PROJECT_ID,
        query="revocable routing",
        scope_selectors=[PROJECT_ID],
        as_of="2026-08-13T17:01:00Z",
    )["result"] == "NO_HIT"


def test_project_truth_conflict_is_suppressed_with_receipt(tmp_path: Path) -> None:
    root = _root(tmp_path)
    sealed = _seal(root, statement="Use one bounded capability retry.")
    _decide(root, sealed, "APPROVE")

    result = retrieve_accepted_learning(
        root,
        project_id=PROJECT_ID,
        query="bounded capability retry",
        scope_selectors=[PROJECT_ID],
        as_of=T3,
        project_truth_conflict_candidate_ids=[sealed["candidate"]["candidate_id"]],
    )

    assert result["result"] == "NO_HIT"
    assert result["suppressed"][0]["state"] == (
        "SUPPRESSED_PROJECT_TRUTH_CONTRADICTION"
    )
    assert result["project_truth_slice"] is None


def test_cross_project_secret_and_tamper_guards_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    with pytest.raises(EvidenceLaneError) as cross_project:
        _seal(root, evidence=[_evidence("foreign", project_id="other-project")])
    assert cross_project.value.code == "LEARNING_CROSS_PROJECT_EVIDENCE_DENIED"

    with pytest.raises(EvidenceLaneError) as secret:
        _seal(root, statement="authorization=github_pat_abcdefghijklmnopqrstuvwxyz")
    assert secret.value.code == "LEARNING_CANDIDATE_SECRET_BLOCKED"

    sealed = _seal(root, statement="Tamper-evident lesson.")
    candidate_path = Path(sealed["candidate_path"])
    value = json.loads(candidate_path.read_text(encoding="utf-8"))
    value["statement"] = "tampered"
    atomic_write_json(candidate_path, value)
    with pytest.raises(EvidenceLaneError) as tampered:
        inspect_learning_authority(root, project_id=PROJECT_ID)
    assert tampered.value.code == "LEARNING_CANDIDATE_FILE_LEDGER_MISMATCH"

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import evidence_lane_plugin.agent_learning as learning_module
import pytest
from evidence_lane_plugin.agent_learning import (
    LEARNING_EXPIRY_OWNER,
    bootstrap_verified_learning_history,
    decide_learning_candidate,
    expire_learning_candidates,
    inspect_learning_authority,
    learning_runtime_contract,
    project_truth_pointer_sha256,
    query_memory_graph,
    record_host_memory_import,
    record_memory_link,
    retrieve_accepted_learning,
    revoke_learning_candidate,
    seal_learning_candidate,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
)
from evidence_lane_plugin.mcp_server import SDK_NATIVE_ACTIONS
from evidence_lane_plugin.project_memory import MEMORY_SECTOR_LOCATOR_PREFIXES

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


def _bootstrap_plan_projection(root: Path) -> Path:
    path = root / "plan_runtime_projection.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE delta_task(task_id TEXT PRIMARY KEY);
        CREATE TABLE plan_execution_row(
            task_id TEXT PRIMARY KEY,
            plan_sequence INTEGER NOT NULL UNIQUE,
            row_number INTEGER,
            history_number INTEGER,
            lifecycle_status TEXT NOT NULL,
            requested_outcome TEXT NOT NULL,
            task_classification TEXT NOT NULL,
            plan_group TEXT NOT NULL,
            commit_batch_id TEXT NOT NULL,
            task_contract_sha256 TEXT NOT NULL
        );
        CREATE TABLE delta_event(
            event_id TEXT PRIMARY KEY,
            sequence INTEGER NOT NULL UNIQUE,
            task_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            to_status TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            details_json TEXT NOT NULL
        );
        CREATE TABLE sub_pv_acceptance(
            task_id TEXT PRIMARY KEY,
            sub_pv_id TEXT NOT NULL,
            state TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL,
            learning_acceptance_inherited_from_sub_pv INTEGER NOT NULL
        );
        """
    )
    rows = [
        (
            "accepted-task",
            1,
            81,
            None,
            "ACCEPTED",
            "Keep accepted Project outcomes separate from Learning decisions.",
            "add_bounded_feature",
            "LEARNING",
            "PV2",
            _hash("accepted-contract"),
        ),
        (
            "verified-forward-task",
            2,
            239,
            None,
            "DONE",
            "Resolve project authority before activating the next Delta.",
            "fix_bug",
            "PROJECT_AUTHORITY",
            "PV13",
            _hash("verified-contract"),
        ),
        (
            "ambiguous-done-task",
            3,
            200,
            None,
            "DONE",
            "This row lacks the exact verified checkpoint and must be excluded.",
            "modify_code",
            "AMBIGUOUS",
            "PV13",
            _hash("ambiguous-contract"),
        ),
    ]
    connection.executemany(
        "INSERT INTO delta_task(task_id) VALUES(?)",
        [(row[0],) for row in rows],
    )
    connection.executemany(
        """
        INSERT INTO plan_execution_row(
            task_id,plan_sequence,row_number,history_number,lifecycle_status,
            requested_outcome,task_classification,plan_group,commit_batch_id,
            task_contract_sha256
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    connection.executemany(
        """
        INSERT INTO delta_event(
            event_id,sequence,task_id,event_type,to_status,recorded_at,
            event_sha256,details_json
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        [
            (
                "accepted-task-hil",
                1,
                "accepted-task",
                "HIL_OUTCOME",
                "ACCEPTED",
                T0,
                _hash("accepted-event"),
                json.dumps({"decision": "APPROVE", "accepted_pv": "PV2"}),
            ),
            (
                "verified-forward-checkpoint",
                2,
                "verified-forward-task",
                "VERIFIED_TASK_CHECKPOINT_COMPLETED",
                "DONE",
                T1,
                _hash("verified-event"),
                json.dumps(
                    {
                        "verification_kind": "PER_DELTA_LOCAL_VERIFICATION",
                        "candidate_created": False,
                        "pointer_moved": False,
                        "hil_inferred": False,
                        "pending_hil": False,
                        "completion_receipt_sha256": _hash("completion-receipt"),
                        "verification_proof_sha256": _hash("verification-proof"),
                    }
                ),
            ),
            (
                "ambiguous-done",
                3,
                "ambiguous-done-task",
                "TASK_DONE",
                "DONE",
                T2,
                _hash("ambiguous-event"),
                json.dumps({"generic_pass": True}),
            ),
        ],
    )
    connection.execute(
        """
        INSERT INTO sub_pv_acceptance(
            task_id,sub_pv_id,state,receipt_sha256,
            learning_acceptance_inherited_from_sub_pv
        ) VALUES(?,?,?,?,?)
        """,
        (
            "verified-forward-task",
            "PV12.239.1",
            "AUTO_ACCEPTED_DELTA_ROW_WORK",
            _hash("verified-forward-sub-pv"),
            1,
        ),
    )
    connection.commit()
    connection.close()
    return path


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


def _memory_locator(
    *,
    sector: str,
    locator_kind: str,
    locator_value: str,
    revision: str,
    label: str,
    search_terms: list[str],
) -> dict[str, object]:
    return {
        "sector": sector,
        "locator_kind": locator_kind,
        "locator_value": locator_value,
        "revision_sha256": _hash(revision),
        "label": label,
        "search_terms": search_terms,
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


def test_verified_history_bootstrap_is_bounded_idempotent_and_pointer_neutral(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _bootstrap_plan_projection(root)
    project_pointer_before = project_truth_pointer_sha256(root, project_id=PROJECT_ID)

    first = bootstrap_verified_learning_history(
        root,
        project_id=PROJECT_ID,
        accepted_pv="PV12",
        max_candidates=8,
    )
    second = bootstrap_verified_learning_history(
        root,
        project_id=PROJECT_ID,
        accepted_pv="PV12",
        max_candidates=8,
    )

    assert first["status"] == "PASS"
    assert first["created_count"] == 2
    assert first["idempotent_reuse_count"] == 0
    assert first["excluded_ambiguous_or_unverified_count"] == 1
    assert first["source_kind_counts"] == {
        "ACCEPTED_HISTORY": 1,
        "VERIFIED_FORWARD": 1,
    }
    assert second["created_count"] == 0
    assert second["idempotent_reuse_count"] == 2
    assert second["candidate_ids"] == first["candidate_ids"]
    assert second["receipt"] == first["receipt"]
    assert project_truth_pointer_sha256(root, project_id=PROJECT_ID) == (
        project_pointer_before
    )
    assert not (root / "ai_learning" / "active_pointer.json").exists()
    inspected = inspect_learning_authority(root, project_id=PROJECT_ID)
    assert inspected["candidate_count"] == 3
    assert inspected["event_count"] == 5
    assert list(inspected["candidate_states"].values()).count(
        "AUTO_ACCEPTED_DELTA_LEARNING"
    ) == 2
    assert list(inspected["candidate_states"].values()).count(
        "PENDING_LEARNING_HIL"
    ) == 1
    assert inspected["auto_accepted_delta_count"] == 2
    assert inspected["pending_weave_count"] == 1
    assert inspected["learning_weaves"] == [
        {
            "accepted_project_pv": "PV12",
            "target_project_pv": "PV13",
            "member_count": 2,
            "member_set_sha256": first["weave_receipt"]["member_set_sha256"],
            "weave_candidate_id": first["weave_candidate"]["candidate_id"],
            "weave_candidate_sha256": first["weave_candidate"][
                "candidate_sha256"
            ],
            "weave_candidate_state": "PENDING_LEARNING_HIL",
            "receipt_sha256": first["weave_receipt"]["receipt_sha256"],
            "acceptance_decision_receipt_sha256": None,
            "acceptance_decided_at": None,
            "full_member_payload_returned": False,
        }
    ]
    assert first["project_candidate_created"] is False
    assert first["project_hil_invoked"] is False
    assert first["learning_hil_invoked"] is False
    assert first["automatic_learning_acceptance"] is True
    assert first["auto_accepted_delta_count"] == 2
    assert first["weave_candidate_state"] == "PENDING_LEARNING_HIL"
    assert first["weave_receipt"]["member_count"] == 2
    assert second["weave_candidate"] == first["weave_candidate"]
    assert second["auto_acceptance_reuse_count"] == 2
    assert first["full_plan_loaded_into_model_context"] is False


def test_only_woven_learning_candidate_is_human_decidable(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _bootstrap_plan_projection(root)
    bootstrapped = bootstrap_verified_learning_history(
        root,
        project_id=PROJECT_ID,
        accepted_pv="PV12",
        max_candidates=8,
    )
    member_id = bootstrapped["candidate_ids"][0]
    member = json.loads(
        (root / "ai_learning" / "candidates" / f"{member_id}.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(EvidenceLaneError) as blocked:
        decide_learning_candidate(
            root,
            project_id=PROJECT_ID,
            candidate_id=member_id,
            expected_candidate_sha256=member["candidate_sha256"],
            decision_token="APPROVE",
            actor_id="fixture-user",
            decided_at=T3,
            expected_project_truth_pointer_sha256=project_truth_pointer_sha256(
                root, project_id=PROJECT_ID
            ),
        )
    assert blocked.value.code == "LEARNING_CANDIDATE_NOT_DECIDABLE"

    weave = bootstrapped["weave_candidate"]
    accepted = decide_learning_candidate(
        root,
        project_id=PROJECT_ID,
        candidate_id=weave["candidate_id"],
        expected_candidate_sha256=weave["candidate_sha256"],
        decision_token="APPROVE",
        actor_id="fixture-user",
        decided_at=T3,
        expected_project_truth_pointer_sha256=project_truth_pointer_sha256(
            root, project_id=PROJECT_ID
        ),
    )
    assert accepted["receipt"]["learning_pointer_moved"] is True
    inspected = inspect_learning_authority(root, project_id=PROJECT_ID)
    assert inspected["current_pointer"]["generation"] == 1
    assert inspected["current_pointer"]["accepted_candidate_id"] == weave[
        "candidate_id"
    ]
    assert inspected["learning_weaves"][0][
        "acceptance_decision_receipt_sha256"
    ] == accepted["receipt"]["receipt_sha256"]
    assert inspected["learning_weaves"][0]["acceptance_decided_at"] == accepted[
        "receipt"
    ]["decided_at"]
    assert inspected["candidate_states"][member_id] == (
        "AUTO_ACCEPTED_DELTA_LEARNING"
    )


def test_verified_history_bootstrap_prevalidates_before_any_learning_write(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    plan_path = _bootstrap_plan_projection(root)
    connection = sqlite3.connect(plan_path)
    connection.execute(
        """
        UPDATE plan_execution_row
        SET requested_outcome=?
        WHERE task_id='verified-forward-task'
        """,
        ("authorization=github_pat_abcdefghijklmnopqrstuvwxyz",),
    )
    connection.commit()
    connection.close()

    with pytest.raises(EvidenceLaneError) as blocked:
        bootstrap_verified_learning_history(
            root,
            project_id=PROJECT_ID,
            accepted_pv="PV12",
            max_candidates=8,
        )

    assert blocked.value.code == "LEARNING_BOOTSTRAP_OUTCOME_INVALID"
    inspected = inspect_learning_authority(root, project_id=PROJECT_ID)
    assert inspected["candidate_count"] == 0
    assert inspected["event_count"] == 0


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
    assert not (root / "ai_learning" / "active_pointer.json").exists()


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
        expiry_owner=LEARNING_EXPIRY_OWNER,
    )
    assert expired["expired_candidate_ids"] == [expiring["candidate"]["candidate_id"]]
    assert expired["receipt"]["expiry_owner"] == LEARNING_EXPIRY_OWNER
    assert expired["receipt"]["learning_pointer_moved"] is False
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


def test_runtime_contract_has_six_learning_routes_and_first_class_memory_boundary() -> None:
    contract = learning_runtime_contract()

    assert contract["public_actions"] == [
        "learning_inspect",
        "learning_retrieve",
        "learning_record_host_memory_import",
        "learning_seal_candidate",
        "learning_decide_candidate",
        "learning_revoke",
    ]
    assert contract["memory_graph"]["schema_version"] == 1
    assert contract["memory_graph"]["authority"] == (
        "INDEPENDENT_PROJECT_MEMORY_AUTHORITY"
    )
    assert contract["memory_graph"]["sdk_module"] == "project_memory"
    assert contract["memory_graph"]["current_action_names"] == [
        "project_memory_query",
        "project_memory_record_link",
    ]
    assert contract["memory_graph"]["owner_skill"] == "evi-memory"
    assert contract["memory_graph"]["obsolete_compatibility_action_names"] == [
        "learning_memory_query",
        "learning_memory_record_link",
    ]
    assert contract["memory_graph"]["obsolete_compatibility_actions_executable"] is False
    assert contract["memory_graph"]["legacy_learning_tables"] == (
        "IMMUTABLE_MIGRATION_SOURCE_ONLY"
    )
    assert contract["delta_learning"] == {
        "intermediate_acceptance": "AUTO_ACCEPTED_AT_VERIFIED_DELTA_EXIT",
        "project_pointer_effect": "NONE",
        "learning_pointer_effect": "NONE",
        "full_pv_hil_input": "ONE_DETERMINISTIC_WOVEN_CANDIDATE",
        "individual_member_hil_allowed": False,
        "weave_decision_pointer_moves": 1,
    }
    assert set(contract["memory_graph"]["sectors"]) == set(
        MEMORY_SECTOR_LOCATOR_PREFIXES
    )
    assert contract["memory_graph"]["raw_database_or_markdown_stored"] is False
    assert contract["memory_graph"]["automatic_host_memory_import"] is False
    assert contract["memory_graph"]["project_truth_effect"] == "NONE"
    assert contract["memory_graph"]["candidate_effect"] == "NONE"
    assert contract["memory_graph"]["hil_effect"] == "NONE"
    assert contract["public_action_count"] == 6
    assert contract["search"]["engine"] == "SQLITE_FTS5"
    assert contract["search"]["full_ledger_loaded_into_model_context"] is False
    assert contract["expiry"]["event_materialization_owner"] == (
        LEARNING_EXPIRY_OWNER
    )
    assert contract["expiry"]["public_action"] is None
    assert contract["expiry"]["hook_owned"] is False
    routing = {
        name: (module, operation)
        for name, _title, _description, module, operation, _read_only in (
            SDK_NATIVE_ACTIONS
        )
        if name in contract["public_actions"]
    }
    assert set(routing) == set(contract["public_actions"])
    memory_routing = {
        name: (module, operation)
        for name, _title, _description, module, operation, _read_only in (
            SDK_NATIVE_ACTIONS
        )
        if name in contract["memory_graph"]["current_action_names"]
    }
    assert memory_routing["project_memory_query"] == ("project_memory", "query")
    assert memory_routing["project_memory_record_link"] == (
        "project_memory",
        "record_link",
    )
    assert all(module == "agent_learning" for module, _operation in routing.values())


def test_memory_graph_is_bounded_revisioned_and_idempotent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    old_turn = _memory_locator(
        sector="CHAT_LINEAGE",
        locator_kind="TURN",
        locator_value="chat-lineage://task/task-fixture/turn/1",
        revision="turn-revision-1",
        label="Retry decision before correction",
        search_terms=["retry", "continuity", "old"],
    )
    corrected_turn = _memory_locator(
        sector="CHAT_LINEAGE",
        locator_kind="TURN",
        locator_value="chat-lineage://task/task-fixture/turn/1",
        revision="turn-revision-2",
        label="Retry decision after correction",
        search_terms=["retry", "continuity", "corrected"],
    )

    recorded = record_memory_link(
        root,
        project_id=PROJECT_ID,
        source=corrected_turn,
        target=old_turn,
        edge_type="SUPERSEDES",
        evidence_sha256=_hash("turn-supersession"),
        recorded_at=T1,
    )
    reused = record_memory_link(
        root,
        project_id=PROJECT_ID,
        source=corrected_turn,
        target=old_turn,
        edge_type="SUPERSEDES",
        evidence_sha256=_hash("turn-supersession"),
        recorded_at=T1,
    )
    result = query_memory_graph(
        root,
        project_id=PROJECT_ID,
        query="retry continuity",
        as_of=T2,
        sectors=["CHAT_LINEAGE"],
        limit=2,
    )

    assert recorded["inserted_locator_count"] == 2
    assert recorded["idempotent_reuse"] is False
    assert reused["inserted_locator_count"] == 0
    assert reused["idempotent_reuse"] is True
    assert [hit["revision_sha256"] for hit in result["hits"]] == [
        _hash("turn-revision-2")
    ]
    assert result["suppressed"] == [
        {
            "locator_id": recorded["target_locator_id"],
            "state": "SUPPRESSED_MEMORY_LOCATOR",
            "edge_types": ["SUPERSEDES"],
        }
    ]
    assert result["receipt"]["hit_count"] == 1
    assert result["receipt"]["suppressed_count"] == 1
    assert result["full_ledger_loaded_into_model_context"] is False
    assert result["raw_database_or_markdown_returned"] is False
    assert result["receipt"]["project_truth_pointer_moved"] is False
    assert result["receipt"]["candidate_created"] is False
    assert result["receipt"]["hil_invoked"] is False


def test_memory_graph_rejects_cross_sector_prefix_and_unbounded_query(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    target = _memory_locator(
        sector="PLAN",
        locator_kind="TASK",
        locator_value="plan://task/task-fixture",
        revision="plan-revision",
        label="Plan task",
        search_terms=["plan", "task"],
    )
    invalid_source = _memory_locator(
        sector="CANON",
        locator_kind="NODE",
        locator_value="chat-lineage://wrong-sector",
        revision="canon-revision",
        label="Wrong sector locator",
        search_terms=["canon", "wrong"],
    )

    with pytest.raises(EvidenceLaneError) as invalid:
        record_memory_link(
            root,
            project_id=PROJECT_ID,
            source=invalid_source,
            target=target,
            edge_type="MAPS_TO",
            evidence_sha256=_hash("invalid-link"),
            recorded_at=T1,
        )
    assert invalid.value.code == "LEARNING_MEMORY_LOCATOR_BOUNDARY_INVALID"

    with pytest.raises(EvidenceLaneError) as unbounded:
        query_memory_graph(
            root,
            project_id=PROJECT_ID,
            query="plan",
            as_of=T2,
            limit=21,
        )
    assert unbounded.value.code == "LEARNING_MEMORY_QUERY_BOUNDS_INVALID"


def test_host_memory_import_is_explicit_non_authoritative_and_tamper_evident(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    imported = record_host_memory_import(
        root,
        project_id=PROJECT_ID,
        source_kind="CODEX_LOCAL_MEMORY",
        source_locator="codex-local-memory://memory/fixture-1",
        source_record_sha256=_hash("host-memory-record"),
        source_context_id="context-fixture-1",
        observed_at=T0,
        imported_at=T1,
        imported_by="fixture-user",
        purpose="Link one explicit continuity memory to its Plan Delta.",
        task_id="task-fixture",
        delta_id="EL-FIXTURE-DELTA-001",
        pv_ref="PV12",
    )
    queried = query_memory_graph(
        root,
        project_id=PROJECT_ID,
        query="host memory provenance",
        as_of=T2,
        sectors=["HOST_MEMORY"],
        limit=4,
    )

    assert imported["state"] == "IMPORTED_AS_NONAUTHORITATIVE_EVIDENCE_REFERENCE"
    assert imported["memory_graph"]["source_sector"] == "HOST_MEMORY"
    assert imported["memory_graph"]["target_sector"] == "PLAN"
    assert imported["raw_host_memory_stored"] is False
    assert imported["candidate_created"] is False
    assert imported["learning_hil_invoked"] is False
    assert imported["project_truth_pointer_moved"] is False
    assert queried["receipt"]["hit_count"] == 1

    receipt_path = Path(imported["receipt_path"])
    tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
    tampered["source"]["locator"] = "codex-local-memory://memory/tampered"
    atomic_write_json(receipt_path, tampered)
    with pytest.raises(EvidenceLaneError) as mismatch:
        inspect_learning_authority(root, project_id=PROJECT_ID)
    assert mismatch.value.code == "LEARNING_HOST_MEMORY_IMPORT_RECEIPT_HASH_MISMATCH"


def test_v1_ledger_migrates_additively_to_cross_sector_memory_graph(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    inspect_learning_authority(root, project_id=PROJECT_ID)
    ledger = root / "ai_learning" / "agent-learning.sqlite"
    connection = sqlite3.connect(ledger)
    connection.execute("DROP TABLE memory_edge")
    connection.execute("DROP TABLE memory_locator_fts")
    connection.execute("DROP TABLE memory_locator")
    connection.execute(
        """
        UPDATE learning_schema_metadata
        SET schema_version=1,ddl_sha256=?,schema_signature_sha256=?
        WHERE singleton=1
        """,
        (
            sha256_bytes(learning_module._LEARNING_V1_SCHEMA_DDL.encode("utf-8")),
            sha256_bytes(
                canonical_json_bytes(learning_module._LEARNING_V1_EXPECTED_SCHEMA)
            ),
        ),
    )
    connection.commit()
    connection.close()

    inspected = inspect_learning_authority(root, project_id=PROJECT_ID)
    assert inspected["runtime_contract"]["ledger_schema_version"] == 2
    assert inspected["memory_locator_count"] == 0
    assert inspected["memory_edge_count"] == 0
    assert inspected["indexed_memory_locator_count"] == 0
    assert inspected["memory_search_engine"] == "SQLITE_FTS5_BM25"


def test_retrieval_excludes_expired_before_event_materialization(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    sealed = _seal(
        root,
        statement="Temporal expiry is retrieval owned.",
        expires_at="2026-08-13T14:30:00+00:00",
    )
    _decide(root, sealed, "APPROVE", decided_at=T2)

    result = retrieve_accepted_learning(
        root,
        project_id=PROJECT_ID,
        query="temporal expiry retrieval",
        scope_selectors=[PROJECT_ID],
        as_of="2026-08-13T14:31:00+00:00",
    )

    assert result["result"] == "NO_HIT"
    assert result["suppressed"][0]["state"] == "SUPPRESSED_TEMPORAL_EXPIRY"
    assert result["suppressed"][0]["expiry_event_materialized"] is False
    assert result["search_engine"] == "SQLITE_FTS5_BM25"
    assert inspect_learning_authority(root, project_id=PROJECT_ID)[
        "candidate_states"
    ][sealed["candidate"]["candidate_id"]] == "ACCEPTED"


def test_expiry_materialization_rejects_every_other_owner(tmp_path: Path) -> None:
    root = _root(tmp_path)
    sealed = _seal(
        root,
        statement="Only Learning maintenance may materialize expiry.",
        expires_at="2026-08-13T14:30:00+00:00",
    )
    _decide(root, sealed, "APPROVE", decided_at=T2)

    with pytest.raises(EvidenceLaneError) as blocked:
        expire_learning_candidates(
            root,
            project_id=PROJECT_ID,
            as_of="2026-08-13T14:31:00+00:00",
            expiry_owner="CODEX_HOOK",
        )

    assert blocked.value.code == "LEARNING_EXPIRY_OWNER_REQUIRED"
    assert inspect_learning_authority(root, project_id=PROJECT_ID)[
        "candidate_states"
    ][sealed["candidate"]["candidate_id"]] == "ACCEPTED"


def test_v0_ledger_migrates_additively_and_rebuilds_fts(tmp_path: Path) -> None:
    root = _root(tmp_path)
    sealed = _seal(root, statement="Legacy learning row survives migration.")
    ledger = root / "ai_learning" / "agent-learning.sqlite"
    connection = sqlite3.connect(ledger)
    connection.execute("DROP TABLE learning_candidate_fts")
    connection.execute("DROP TABLE learning_schema_metadata")
    connection.commit()
    connection.close()

    inspected = inspect_learning_authority(root, project_id=PROJECT_ID)

    assert inspected["candidate_count"] == 1
    assert inspected["indexed_candidate_count"] == 1
    assert inspected["candidate_states"][sealed["candidate"]["candidate_id"]] == (
        "PENDING_LEARNING_HIL"
    )


def test_v1_schema_drift_fails_closed_without_auto_repair(tmp_path: Path) -> None:
    root = _root(tmp_path)
    inspect_learning_authority(root, project_id=PROJECT_ID)
    ledger = root / "ai_learning" / "agent-learning.sqlite"
    connection = sqlite3.connect(ledger)
    connection.execute("DROP INDEX idx_learning_candidate_dedup")
    connection.commit()
    connection.close()

    with pytest.raises(EvidenceLaneError) as drifted:
        inspect_learning_authority(root, project_id=PROJECT_ID)

    assert drifted.value.code == "LEARNING_LEDGER_SCHEMA_MISMATCH"
    connection = sqlite3.connect(ledger)
    index = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_learning_candidate_dedup",),
    ).fetchone()
    connection.close()
    assert index is None


def test_failed_v0_migration_rolls_back_every_schema_change(tmp_path: Path) -> None:
    root = _root(tmp_path)
    learning_root = root / "ai_learning"
    learning_root.mkdir()
    ledger = learning_root / "agent-learning.sqlite"
    connection = sqlite3.connect(ledger)
    connection.execute(
        "CREATE TABLE learning_candidate(candidate_id TEXT PRIMARY KEY) STRICT"
    )
    connection.commit()
    connection.close()

    with pytest.raises(EvidenceLaneError) as failed:
        inspect_learning_authority(root, project_id=PROJECT_ID)

    assert failed.value.code == "LEARNING_LEDGER_SCHEMA_MISMATCH"
    connection = sqlite3.connect(ledger)
    objects = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        )
    }
    columns = [
        str(row[1])
        for row in connection.execute("PRAGMA table_info(learning_candidate)")
    ]
    connection.close()
    assert "learning_schema_metadata" not in objects
    assert "learning_candidate_fts" not in objects
    assert columns == ["candidate_id"]


def test_legacy_learning_root_migrates_once_to_single_ai_learning_authority(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    inspect_learning_authority(root, project_id=PROJECT_ID)
    canonical = root / "ai_learning"
    legacy = root / "learning"
    ledger_before = (canonical / "agent-learning.sqlite").read_bytes()
    project_pointer_before = (root / "active_pointer.json").read_bytes()
    canonical.rename(legacy)
    canonical.mkdir()
    atomic_write_json(
        canonical / "authority.ref.json",
        {
            "schema": "evidence-lane.named-project-authority-reference.v1",
            "authority": "AI_LEARNING",
            "project_id": PROJECT_ID,
        },
    )

    inspected = inspect_learning_authority(root, project_id=PROJECT_ID)

    assert inspected["status"] == "PASS"
    assert not legacy.exists()
    assert (canonical / "agent-learning.sqlite").read_bytes() == ledger_before
    assert (canonical / "authority.ref.json").is_file()
    receipt = json.loads(
        (
            canonical
            / "receipts"
            / "authority-layout-migration-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["status"] == "PASS"
    assert receipt["canonical_directory"] == "ai_learning"
    assert receipt["legacy_directory"] == "learning"
    assert receipt["legacy_root_present_after"] is False
    assert receipt["reference_hold_present_after"] is False
    assert receipt["project_truth_pointer_moved"] is False
    assert receipt["learning_pointer_moved"] is False
    assert (root / "active_pointer.json").read_bytes() == project_pointer_before


def test_two_substantive_learning_roots_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    inspect_learning_authority(root, project_id=PROJECT_ID)
    legacy = root / "learning"
    legacy.mkdir()
    (legacy / "agent-learning.sqlite").write_bytes(b"conflicting legacy bytes")

    with pytest.raises(EvidenceLaneError) as conflict:
        inspect_learning_authority(root, project_id=PROJECT_ID)

    assert conflict.value.code == "LEARNING_AUTHORITY_LAYOUT_CONFLICT"
    assert legacy.is_dir()
    assert (root / "ai_learning" / "agent-learning.sqlite").is_file()

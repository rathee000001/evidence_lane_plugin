"""Record the post-research active-contract mismatch and native correction."""

from __future__ import annotations

import json
from pathlib import Path

from research_db import (
    append_audit_event,
    connect,
    sha256_file,
    upsert_fts,
    utc_now,
)

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = (
    ROOT
    / "evidence"
    / "task6_delta_receipts"
    / "EL-CODEX-T6-PARITY-001A-ACTIVE-CONTRACT-REBIND.json"
)
FINDING_ID = "GAP-ACTIVE-ROW196-READONLY-CONTRACT"
PROPOSAL_ID = "DELTA-ACTIVE-CONTRACT-REBIND"
TASK_ID = "EL-CODEX-T6-PARITY-001A-ACTIVE-CONTRACT-REBIND"
BATCH_ID = "T6-ACTIVE-CONTRACT-REBIND-CORRECTION-001"


def compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def main() -> None:
    before_sha256 = sha256_file(Path(__file__).with_name("TASK6_PARITY_RESEARCH.sqlite"))
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    receipt_sha256 = sha256_file(RECEIPT)
    now = utc_now()
    contract = {
        "task_id": TASK_ID,
        "task_class": "fix_bug",
        "group_name": "PLAN_RUNTIME_CONTINUITY",
        "dependency_keys": [
            "EL-CODEX-T6-PARITY-001-PLAN-BATCH-PREHIL-INSERT-ROUTE"
        ],
        "git_boundary": "NONE",
        "requested_outcome": (
            "Add one recoverable visible-user-receipt-bound route that append-amends "
            "the sole active Plan contract and atomically rebinds the same governed "
            "session/runtime task without changing row, Task6, Goal, candidate, HIL, "
            "pointer, Git, install, helper, or tunnel identity."
        ),
        "evidence_locator": RECEIPT.relative_to(ROOT).as_posix(),
    }
    native_receipt = {
        "status": "PASS",
        "operation": "APPEND_BATCH_AND_REBIND_EXISTING_ACTIVE_ROW",
        "linked_task_id": TASK_ID,
        "inserted_row": 198,
        "active_row": 196,
        "physical_final_row": 249,
        "batch_sha256": (
            "07603F92E9C43FC6E0469C2B2CF5BB233F41B4FC995D685F1A8444CCA00D5DE9"
        ),
        "batch_input_sha256": (
            "1C7F88380CE50D5AB9475562B4D814470683125945725005E6B3A1AB11B73859"
        ),
        "before_backlog_sha256": (
            "587516A6A24C33645655D2985555604ADDB695B298F5551AF72AC08C274C60C3"
        ),
        "after_append_backlog_sha256": (
            "2030CEBA5D7706C29E28F4EB15E8EF4AC48FA1361D2794B83D5E90AEAD87A319"
        ),
        "after_rebind_backlog_sha256": receipt["native_rebind"][
            "current_backlog_sha256"
        ],
        "rebind_request_sha256": receipt["native_rebind"]["request_sha256"],
        "rebind_result_sha256": receipt["native_rebind"]["result_sha256"],
        "implementation_receipt_sha256": receipt_sha256,
        "active_row_replaced": False,
        "runtime_task_identity_changed": False,
        "pointer_moved": False,
        "candidate_created": False,
        "hil_invoked": False,
        "goal_completion_mutated": False,
        "git_executed": False,
        "install_executed": False,
        "helper_launched": False,
        "tunnel_launched": False,
    }
    connection = connect()
    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO gap_finding(
                finding_id, domain, severity, classification, statement,
                evidence_locator, route_gap, required_contract,
                proposed_delta_key, status, confidence, recorded_at
            ) VALUES (?, 'plan-goal-runtime', 'CRITICAL',
                'IMPLEMENTED_CORE_BUT_UNROUTED', ?, ?, ?, ?, ?,
                'resolved', 'HIGH', ?)
            """,
            (
                FINDING_ID,
                (
                    "The sole active Row196 Plan contract remained run_test/read-only "
                    "after the user accepted dependency-ordered implementation, while "
                    "linked steer prose could not legally broaden classifier authority."
                ),
                RECEIPT.relative_to(ROOT).as_posix(),
                "No native append-only Plan/session active-contract amendment route.",
                (
                    "Exact CAS over backlog/projection/session/pointer/task/approval "
                    "authorities with one crash-recoverable Plan plus session rebind."
                ),
                PROPOSAL_ID,
                now,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO delta_proposal(
                proposal_id, title, task_class, group_name, dependency_keys,
                contract_json, git_boundary, status, recorded_at
            ) VALUES (?, ?, 'fix_bug', 'PLAN_RUNTIME_CONTINUITY', ?, ?,
                'NONE', 'appended', ?)
            """,
            (
                PROPOSAL_ID,
                "Active Plan Contract Amendment and Exact Session Rebind",
                compact_json(contract["dependency_keys"]),
                compact_json(contract),
                now,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO delta_normalization_map(
                finding_id, proposal_id, disposition, linked_task_id,
                normalization_reason, recorded_at
            ) VALUES (?, ?, 'APPEND_NEW_ROW', ?, ?, ?)
            """,
            (
                FINDING_ID,
                PROPOSAL_ID,
                TASK_ID,
                (
                    "The mismatch was discovered only after the normalized batch "
                    "append, so it required one explicit correction row before the "
                    "first research implementation successor."
                ),
                now,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO delta_append_batch(
                batch_id, proposal_count, append_new_row_count,
                link_existing_row_count, proposal_sha256,
                required_insertions_json, native_receipt_json, status, recorded_at
            ) VALUES (?, 1, 1, 0, ?, ?, ?, 'APPENDED', ?)
            """,
            (
                BATCH_ID,
                native_receipt["batch_sha256"],
                compact_json(
                    [{"insert_before_task_id": "EL-CODEX-T6-PARITY-002-PER-DELTA-LOCAL-VERIFICATION", "task_id": TASK_ID}]
                ),
                compact_json(native_receipt),
                now,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO native_plan_receipt(
                receipt_id, proposal_id, operation, linked_task_id,
                task_count_changed, native_status, receipt_json, recorded_at
            ) VALUES (?, ?, 'APPEND_BATCH', ?, 1, 'PASS', ?, ?)
            """,
            (
                "T6-NATIVE-ACTIVE-CONTRACT-REBIND-001",
                PROPOSAL_ID,
                TASK_ID,
                compact_json(native_receipt),
                now,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO source_authority(
                authority_id, authority_kind, path, sha256, byte_count,
                freshness, authority_role, notes, recorded_at
            ) VALUES (?, 'IMPLEMENTATION_RECEIPT', ?, ?, ?, 'CURRENT',
                'POST_RESEARCH_CORRECTION_PROOF', ?, ?)
            """,
            (
                "SRC-T6-ACTIVE-CONTRACT-REBIND-RECEIPT",
                RECEIPT.relative_to(ROOT).as_posix(),
                receipt_sha256,
                RECEIPT.stat().st_size,
                "Local tests plus native source-MCP journal and lineage receipts.",
                now,
            ),
        )
        tests = [
            (
                "TEST-T6-ACTIVE-CONTRACT-REBIND-FOCUSED",
                "tests/test_active_task_contract_rebind.py",
                "tests/test_active_task_contract_rebind.py",
                "success, mismatch, replay, crash, identity, and capture binding",
            ),
            (
                "TEST-T6-ACTIVE-CONTRACT-REBIND-PLAN-REGRESSION",
                "tests/test_plan_atomic_insertions.py;tests/test_plan_steers.py;tests/test_plan_normalization.py",
                "25 affected Plan tests",
                "atomic insertion, steer, and normalization compatibility",
            ),
        ]
        for test_id, path, selector, proves in tests:
            connection.execute(
                """
                INSERT OR IGNORE INTO test_evidence(
                    test_id, domain, path, test_selector, evidence_class,
                    status, proves, does_not_prove, recorded_at
                ) VALUES (?, 'plan-goal-runtime', ?, ?, 'LOCAL_SOURCE_RUNTIME',
                    'PASS', ?, 'No Git, install, candidate, HIL, or pointer promotion.', ?)
                """,
                (test_id, path, selector, proves, now),
            )
        upsert_fts(
            connection,
            doc_id=FINDING_ID,
            doc_type="gap_finding",
            title="Active Row196 read-only classifier mismatch",
            body=(
                "Row196 stayed run_test READ_ONLY while visible authority required "
                "bounded implementation. The correction append-amends the Plan "
                "contract and rebinds the same session/runtime task exactly once."
            ),
            evidence_locator=RECEIPT.relative_to(ROOT).as_posix(),
        )
        upsert_fts(
            connection,
            doc_id=PROPOSAL_ID,
            doc_type="delta_proposal",
            title="Active contract amendment and exact rebind",
            body=compact_json(contract),
            evidence_locator=RECEIPT.relative_to(ROOT).as_posix(),
        )
        append_audit_event(
            connection,
            event_id="T6-POST-RESEARCH-ACTIVE-CONTRACT-REBIND-001",
            event_type="POST_RESEARCH_MISMATCH_CORRECTED",
            payload={
                "finding_id": FINDING_ID,
                "proposal_id": PROPOSAL_ID,
                "task_id": TASK_ID,
                "native_receipt": native_receipt,
                "research_steps_reopened": False,
                "research_plan_folded_into_project_plan": False,
            },
            occurred_at=now,
        )
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    after_sha256 = sha256_file(Path(__file__).with_name("TASK6_PARITY_RESEARCH.sqlite"))
    print(
        json.dumps(
            {
                "status": "PASS",
                "finding_id": FINDING_ID,
                "proposal_id": PROPOSAL_ID,
                "task_id": TASK_ID,
                "receipt_sha256": receipt_sha256,
                "before_sha256": before_sha256,
                "after_sha256": after_sha256,
                "quick_check": quick_check,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

"""Append the Task6 derived Plan SQLite parity correction evidence."""

from __future__ import annotations

import json
from pathlib import Path

from research_db import append_audit_event, connect, sha256_file, upsert_fts, utc_now

ROOT = Path(__file__).resolve().parents[2]
DATABASE = Path(__file__).with_name("TASK6_PARITY_RESEARCH.sqlite")
RECEIPT = (
    ROOT
    / "evidence"
    / "task6_delta_receipts"
    / "EL-CODEX-T6-PARITY-001B-PLAN-SQLITE-ACTIVE-CONTRACT-PARITY.json"
)
AUTHORITY_ID = "SRC-T6-PLAN-SQLITE-ACTIVE-CONTRACT-PARITY-RECEIPT"
EVENT_ID = "T6-POST-RESEARCH-PLAN-SQLITE-PARITY-001"
FTS_DOC_ID = "EVIDENCE-T6-PLAN-SQLITE-ACTIVE-CONTRACT-PARITY"


def main() -> None:
    before_sha256 = sha256_file(DATABASE)
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    receipt_sha256 = sha256_file(RECEIPT)
    receipt_path = RECEIPT.relative_to(ROOT).as_posix()
    now = utc_now()
    connection = connect()
    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO source_authority(
                authority_id, authority_kind, path, sha256, byte_count,
                freshness, authority_role, notes, recorded_at
            ) VALUES (?, 'IMPLEMENTATION_RECEIPT', ?, ?, ?, 'CURRENT',
                'POST_RESEARCH_DERIVED_INDEX_CORRECTION_PROOF', ?, ?)
            """,
            (
                AUTHORITY_ID,
                receipt_path,
                receipt_sha256,
                RECEIPT.stat().st_size,
                (
                    "CAS-bound refresh proof for disposable Plan runtime SQLite; "
                    "canonical Plan, Goal, pointer, candidate, HIL, Git, install, "
                    "helper, and tunnel authority remained unchanged."
                ),
                now,
            ),
        )
        tests = (
            (
                "TEST-T6-PLAN-SQLITE-PARITY-FOCUSED",
                "tests/test_active_task_contract_rebind.py",
                "10 passed in 104.15s",
                "active-contract projection precedence and stale-index recovery",
            ),
            (
                "TEST-T6-PLAN-SQLITE-PARITY-PLAN",
                (
                    "tests/test_plan_atomic_insertions.py;tests/test_plan_steers.py;"
                    "tests/test_plan_normalization.py"
                ),
                "25 passed across three isolated suites",
                "atomic insertion, steer, and normalization compatibility",
            ),
            (
                "TEST-T6-PLAN-SQLITE-PARITY-HOST",
                "tests/test_host_plan_rehydration.py",
                "20 passed in 171.26s",
                "bounded exact-ID host Plan rehydration compatibility",
            ),
        )
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
            doc_id=FTS_DOC_ID,
            doc_type="implementation_evidence",
            title="Derived Plan SQLite active-contract parity",
            body=(
                "The active Row196 canonical contract was correct while the disposable "
                "Plan runtime SQLite preferred stale linked-steer metadata. The corrected "
                "projection gives ACTIVE_CONTRACT_REBIND precedence for group, batch, "
                "dependencies, and Git stage and refreshes only the stale derived index."
            ),
            evidence_locator=receipt_path,
        )
        append_audit_event(
            connection,
            event_id=EVENT_ID,
            event_type="POST_RESEARCH_DERIVED_INDEX_PARITY_CORRECTED",
            payload={
                "receipt_path": receipt_path,
                "receipt_sha256": receipt_sha256,
                "native_refresh": receipt["native_refresh"],
                "bounded_plan_proof": receipt["bounded_plan_proof"],
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
    print(
        json.dumps(
            {
                "status": "PASS",
                "authority_id": AUTHORITY_ID,
                "event_id": EVENT_ID,
                "receipt_sha256": receipt_sha256,
                "before_sha256": before_sha256,
                "after_sha256": sha256_file(DATABASE),
                "quick_check": quick_check,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

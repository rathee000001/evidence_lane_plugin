from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = ROOT / "evidence" / "task6_parity_research"
sys.path.insert(0, str(RESEARCH_ROOT))

from research_db import (
    DB_PATH,
    append_audit_event,
    connect,
    sha256_file,
    transition_step,
    upsert_fts,
    utc_now,
)

BATCH_ID = "T6-PARITY-NORMALIZATION-BATCH-001"
ATOMIC_BATCH_ID = "T6-PARITY-NORMALIZED-ATOMIC-REMAINDER-001"
RESEARCH_BATCH_SHA256 = (
    "E5725883DA8343128FE632F4081AF422AEDC8F1944207D950DD547F39380D6D8"
)
PHYSICAL_FINAL_TASK_ID = (
    "EL-CODEX-NATIVE-FUSED-RELEASE-HIL-DELTA-141-NORMALIZED-SUCCESSOR"
)
RECEIPT = {
    "schema": "evidence-lane.task6-native-plan-atomic-batch-receipt.v1",
    "status": "PASS",
    "batch_id": ATOMIC_BATCH_ID,
    "research_batch_sha256": RESEARCH_BATCH_SHA256,
    "input_sha256": (
        "C131B4261837B613D02A198CCE40E5A0AC7149A44CFDC247A5A0987F5F9160E8"
    ),
    "before_backlog_sha256": (
        "1FCA941F73067CA5BE21C60F9E6BF3150E84683562A112A59E85E01C54E981B0"
    ),
    "after_backlog_sha256": (
        "587516A6A24C33645655D2985555604ADDB695B298F5551AF72AC08C274C60C3"
    ),
    "journal_sha256": (
        "F11E32EE9C5A9DB89EE951533C5F2B8C6E94200B39C4F697959046F9992D42BE"
    ),
    "journal_state": "COMMITTED",
    "source_2_2_after_canonical_plan_sha256": (
        "83AA739CE54DE7FC45585BF48DF41D41947A264E188E9322A62463F711C0AE4E"
    ),
    "source_2_2_after_executable_projection_sha256": (
        "727E802CE1F0A911CDA94F74A104E01E5768D3A5765F8AB133F994FFF9C99930"
    ),
    "installed_2_1_after_canonical_plan_sha256": (
        "B7743D4B15B95C70D744A3EA5C30FEDD0BC277120DFF66DF728D49B22E645339"
    ),
    "installed_2_1_after_executable_projection_sha256": (
        "F4500FFA8C525CFFA10EE8CC100546A0A90683098D7D7F81204C202B7B4EA870"
    ),
    "canonical_task_count": 223,
    "executable_task_count": 168,
    "history_task_count": 55,
    "active_row": 196,
    "active_task_id": "EL-CODEX-GITHUB_ACTIONS_CLEAN_CI-PROPOSAL-31",
    "bootstrap_row": 197,
    "forced_route_row": 229,
    "install_successor_row": 239,
    "physical_final_row": 248,
    "physical_final_task_id": PHYSICAL_FINAL_TASK_ID,
    "runtime_status_in_installed_2_1": "STALE",
    "pointer_moved": False,
    "candidate_created": False,
    "hil_invoked": False,
    "goal_mutated": False,
    "git_executed": False,
}


def main() -> None:
    before_db_sha256 = sha256_file(DB_PATH)
    now = utc_now()
    receipt_json = json.dumps(RECEIPT, sort_keys=True, separators=(",", ":"))
    with connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"
        batch = connection.execute(
            "SELECT proposal_sha256 FROM delta_append_batch WHERE batch_id=?",
            (BATCH_ID,),
        ).fetchone()
        assert batch is not None and str(batch[0]) == RESEARCH_BATCH_SHA256
        pending = connection.execute(
            """
            WITH latest_map AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY proposal_id
                    ORDER BY recorded_at DESC, rowid DESC
                ) AS rn
                FROM delta_normalization_map
            ), receipted AS (
                SELECT DISTINCT proposal_id FROM native_plan_receipt
            )
            SELECT p.proposal_id,
                   json_extract(p.contract_json, '$.task_id') AS task_id
            FROM delta_proposal AS p
            JOIN latest_map AS m USING(proposal_id)
            LEFT JOIN receipted AS r USING(proposal_id)
            WHERE m.rn=1
              AND m.disposition='APPEND_NEW_ROW'
              AND r.proposal_id IS NULL
            ORDER BY CAST(json_extract(p.contract_json, '$.proposal_order') AS INTEGER)
            """
        ).fetchall()
        assert len(pending) == 37
        for proposal_id, task_id in pending:
            per_task_receipt = {
                **RECEIPT,
                "proposal_id": str(proposal_id),
                "linked_task_id": str(task_id),
            }
            connection.execute(
                """
                INSERT INTO native_plan_receipt(
                    receipt_id, proposal_id, operation, linked_task_id,
                    task_count_changed, native_status, receipt_json, recorded_at
                ) VALUES (?, ?, 'APPEND_BATCH', ?, 1, 'PASS', ?, ?)
                """,
                (
                    f"{ATOMIC_BATCH_ID}__{proposal_id}",
                    str(proposal_id),
                    str(task_id),
                    json.dumps(
                        per_task_receipt,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
        connection.execute(
            """
            UPDATE delta_append_batch
            SET required_insertions_json=?, native_receipt_json=?,
                status='APPENDED', recorded_at=?
            WHERE batch_id=?
            """,
            (
                json.dumps(
                    {
                        "PRE_ROW197": 32,
                        "PRE_ROW201": 6,
                        "current_final_row": 248,
                        "physical_final_task_id": PHYSICAL_FINAL_TASK_ID,
                        "atomic_remainder_appended": 37,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                receipt_json,
                now,
                BATCH_ID,
            ),
        )
        transition_step(
            connection,
            step_no=25,
            to_status="completed",
            evidence_locator=(
                f"delta_append_batch:{BATCH_ID};"
                f"native_plan_receipt:{ATOMIC_BATCH_ID};"
                "plan_rows:196-248"
            ),
            event_id="T6-RESEARCH-STEP25-ATOMIC-BATCH-COMPLETE-001",
            occurred_at=now,
        )
        for key, value in (
            (
                "canonical_plan_boundary",
                "FROZEN_168_EXECUTABLE_ROWS_ACTIVE_R196_PHYSICAL_FINAL_R248",
            ),
            ("source_implementation_action", "RESUMED_AFTER_RESEARCH_COMPLETE"),
        ):
            connection.execute(
                """
                INSERT INTO research_meta(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, now),
            )
        for test_row in (
            (
                "TEST-T6-ATOMIC-BATCH-NATIVE-CLONE",
                "plan_runtime",
                "evidence/task6_delta_receipts/verify_atomic_batch_on_native_store_clone.py",
                "37-row clone insertion plus installed 2.1 backward read",
                "CLONED_NATIVE_STORE_RUNTIME",
                "PASS",
                "The exact batch inserts at two stable targets, preserves Row196 and final Row248, and remains readable by installed 2.1.",
                "The clone run does not prove the live store was mutated.",
            ),
            (
                "TEST-T6-ATOMIC-BATCH-NATIVE-LIVE",
                "plan_runtime",
                "C:/Users/rathe/EvidenceLanePV/projects/test-codex-evidence-lane-plugin/task_backlog.json",
                "source MCP pv_plan_tasks atomic_insertion then installed 2.1 pv_task_backlog",
                "LIVE_NATIVE_RUNTIME",
                "PASS",
                "The live Plan now has 223 canonical and 168 executable records with Row196 active and the same final HIL at Row248.",
                "This Plan mutation does not install source 2.2, implement later Deltas, invoke HIL, or move PV12.",
            ),
        ):
            connection.execute(
                """
                INSERT OR REPLACE INTO test_evidence(
                    test_id, domain, path, test_selector, evidence_class,
                    status, proves, does_not_prove, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*test_row, now),
            )
        append_audit_event(
            connection,
            event_id="AUDIT-T6-NATIVE-ATOMIC-BATCH-APPENDED-001",
            event_type="NATIVE_PLAN_ATOMIC_BATCH_APPENDED",
            payload=RECEIPT,
            occurred_at=now,
        )
        upsert_fts(
            connection,
            doc_id="T6-NATIVE-ATOMIC-BATCH-APPENDED-001",
            doc_type="native_plan_receipt",
            title="Task6 native atomic Plan batch appended",
            body=receipt_json,
            evidence_locator=f"native_plan_receipt:{ATOMIC_BATCH_ID}",
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    after_db_sha256 = sha256_file(DB_PATH)
    print(
        json.dumps(
            {
                "status": "PASS",
                "receipt_count_added": 37,
                "batch_status": "APPENDED",
                "research_step_25": "completed",
                "before_research_db_sha256": before_db_sha256,
                "after_research_db_sha256": after_db_sha256,
                "native_plan_after_backlog_sha256": RECEIPT[
                    "after_backlog_sha256"
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

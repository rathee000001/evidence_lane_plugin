"""Record the bounded native Plan receipts produced after Step25 normalization."""

from __future__ import annotations

import json

from research_db import append_audit_event, connect, utc_now


BOOTSTRAP = {
    "proposal_id": "DELTA-PLAN-BATCH-PREHIL-INSERT-ROUTE",
    "linked_task_id": "EL-CODEX-T6-PARITY-001-PLAN-BATCH-PREHIL-INSERT-ROUTE",
    "status": "PASS",
    "idempotent_reuse": False,
    "task_count_changed": True,
    "row": 197,
    "active_row": 196,
    "active_task_id": "EL-CODEX-GITHUB_ACTIONS_CLEAN_CI-PROPOSAL-31",
    "final_row": 211,
    "final_task_id": "EL-CODEX-NATIVE-FUSED-RELEASE-HIL-DELTA-141-NORMALIZED-SUCCESSOR",
    "goal_projection_sha256": "079F56248D00F3B436EB943EA9B537D68DF0857019597007B2A3C8D8029F83E9",
}

LINKS = [
    ("DELTA-CHAT-LINEAGE-TYPED-RUNTIME", "EL-CODEX-CHATLINEAGE-REVISION-CURSOR-ATOMIC-APPEND-003"),
    ("DELTA-CHATLINEAGE-AUTO-CAPTURE", "EL-CODEX-CHATLINEAGE-REVISION-CURSOR-ATOMIC-APPEND-003"),
    ("DELTA-CHATLINEAGE-DURABLE-SCHEMA", "EL-CODEX-CHATLINEAGE-REVISION-CURSOR-ATOMIC-APPEND-003"),
    ("DELTA-COMPACTION-CONTINUITY", "EL-CODEX-STATE-TRAVEL-BOUNDED-HOST-ORCHESTRATION-DELTA-001"),
    ("DELTA-ENTRY-EXIT-LIFECYCLE-ROUTE", "EL-CODEX-CHATLINEAGE-REVISION-CURSOR-ATOMIC-APPEND-003"),
    ("DELTA-GOAL-NATIVE-LIFECYCLE-BINDING", "EL-CODEX-STATE-TRAVEL-BOUNDED-HOST-ORCHESTRATION-DELTA-001"),
    ("DELTA-HOST-PLAN-PANEL-CONTINUITY", "EL-CODEX-STATE-TRAVEL-BOUNDED-HOST-ORCHESTRATION-DELTA-001"),
    ("DELTA-PLAN-BOUNDED-WINDOW", "EL-CODEX-STATE-TRAVEL-BOUNDED-HOST-ORCHESTRATION-DELTA-001"),
    ("DELTA-PLAN-GOAL-COMMAND-PARITY", "EL-CODEX-STATE-TRAVEL-BOUNDED-HOST-ORCHESTRATION-DELTA-001"),
    ("DELTA-PLAN-SQLITE-AUTHORITY", "EL-CODEX-PLAN-LANE-REUSABLE-SQLITE-DELTA-EXECUTION-LOG-004"),
    ("DELTA-STATE-TRAVEL-FORCED-SAME-WORKTREE", "EL-CODEX-DIRECT-FORCED-SAME-WORKTREE-STATE-TRAVEL-ROUTE-DELTA-002"),
    ("DELTA-TASK-BINDING-EXACT-ATTACHMENT", "EL-CODEX-DIRECT-FORCED-SAME-WORKTREE-STATE-TRAVEL-ROUTE-DELTA-002"),
    ("DELTA-TASK6-BINDING-ROUTE", "EL-CODEX-DIRECT-FORCED-SAME-WORKTREE-STATE-TRAVEL-ROUTE-DELTA-002"),
]

DDL = """
CREATE TABLE IF NOT EXISTS native_plan_receipt (
    receipt_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES delta_proposal(proposal_id),
    operation TEXT NOT NULL CHECK (operation IN ('APPEND_BOOTSTRAP_ROW', 'LINK_EXISTING_ROW', 'APPEND_BATCH')),
    linked_task_id TEXT NOT NULL,
    task_count_changed INTEGER NOT NULL CHECK (task_count_changed IN (0, 1)),
    native_status TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;
"""


def compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def main() -> None:
    connection = connect()
    now = utc_now()
    try:
        connection.executescript(DDL)
        connection.execute(
            """
            INSERT OR REPLACE INTO native_plan_receipt(
                receipt_id, proposal_id, operation, linked_task_id,
                task_count_changed, native_status, receipt_json, recorded_at
            ) VALUES (?, ?, 'APPEND_BOOTSTRAP_ROW', ?, 1, 'PASS', ?, ?)
            """,
            (
                "T6-NATIVE-BOOTSTRAP-ROW-001",
                BOOTSTRAP["proposal_id"],
                BOOTSTRAP["linked_task_id"],
                compact_json(BOOTSTRAP),
                now,
            ),
        )
        connection.execute(
            "UPDATE delta_proposal SET status='appended' WHERE proposal_id=?",
            (BOOTSTRAP["proposal_id"],),
        )
        for index, (proposal_id, task_id) in enumerate(LINKS, start=1):
            receipt = {
                "status": "PASS",
                "idempotent_reuse": False,
                "task_count_changed": False,
                "proposal_id": proposal_id,
                "linked_task_id": task_id,
            }
            connection.execute(
                """
                INSERT OR REPLACE INTO native_plan_receipt(
                    receipt_id, proposal_id, operation, linked_task_id,
                    task_count_changed, native_status, receipt_json, recorded_at
                ) VALUES (?, ?, 'LINK_EXISTING_ROW', ?, 0, 'PASS', ?, ?)
                """,
                (
                    f"T6-NATIVE-LINK-{index:02d}",
                    proposal_id,
                    task_id,
                    compact_json(receipt),
                    now,
                ),
            )
            connection.execute(
                "UPDATE delta_proposal SET status='appended' WHERE proposal_id=?",
                (proposal_id,),
            )
        connection.execute(
            """
            UPDATE delta_append_batch
               SET native_receipt_json=?, status='BLOCKED'
             WHERE batch_id='T6-PARITY-NORMALIZATION-BATCH-001'
            """,
            (
                compact_json(
                    {
                        "bootstrap_appended": 1,
                        "existing_links_attached": len(LINKS),
                        "new_rows_remaining": 37,
                        "active_row": 196,
                        "bootstrap_row": 197,
                        "final_row": 211,
                        "execution_blocker": "ACTIVE_ROW196_REMAINS_IN_PROGRESS",
                    }
                ),
            ),
        )
        append_audit_event(
            connection,
            event_id="T6-RESEARCH-STEP25-NATIVE-RECEIPTS-001",
            event_type="BOOTSTRAP_APPENDED_AND_EXISTING_ROWS_LINKED",
            payload={
                "bootstrap": BOOTSTRAP,
                "linked_receipt_count": len(LINKS),
                "task_count": 186,
                "executable_count": 131,
                "active_row": 196,
                "bootstrap_row": 197,
                "physical_final_row": 211,
                "new_rows_remaining": 37,
                "canonical_plan_mutated_only_by_native_server": True,
                "source_mutated": False,
                "goal_mutated": False,
                "git_mutated": False,
                "pointer_moved": False,
                "hil_invoked": False,
            },
            occurred_at=now,
        )
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        connection.commit()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "bootstrap_receipts": 1,
                    "linked_receipts": len(LINKS),
                    "new_rows_remaining": 37,
                    "active_row": 196,
                    "bootstrap_row": 197,
                    "physical_final_row": 211,
                    "quick_check": quick,
                },
                indent=2,
            )
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()

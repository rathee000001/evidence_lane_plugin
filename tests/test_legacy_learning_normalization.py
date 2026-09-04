from __future__ import annotations

import sqlite3
from pathlib import Path

from evidence_lane_plugin.agent_learning import (
    inspect_learning_authority,
    seal_learning_candidate,
)
from evidence_lane_plugin.hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
)
from evidence_lane_plugin.legacy_learning_normalization import (
    LEGACY_LEARNING_NORMALIZATION_CONFIRMATION,
    migrate_legacy_learning_files,
)


def test_legacy_learning_json_is_verified_ingested_and_quarantined(
    tmp_path: Path,
) -> None:
    root = tmp_path / "User Selected Root"
    root.mkdir()
    project_id = "project-a"
    atomic_write_json(
        root / "project.json",
        {
            "schema": "evidence-lane.project-registry.v1",
            "project_id": project_id,
            "project_authority_root": str(root.resolve()),
        },
    )
    atomic_write_json(
        root / "active_pointer.json",
        {
            "schema": "evidence-lane.pointer.v1",
            "project_id": project_id,
            "accepted_pv": "PV12",
            "generation": 12,
            "accepted_manifest_sha256": sha256_bytes(b"manifest"),
        },
    )
    sealed = seal_learning_candidate(
        root,
        project_id=project_id,
        tier="DELTA_OBSERVATION",
        lesson_type="PROCEDURAL",
        statement="Use exact runtime receipts.",
        scope={"kind": "PROJECT", "selectors": [project_id]},
        evidence=[
            {
                "project_id": project_id,
                "task_id": "task-a",
                "delta_id": "delta-a",
                "pv_ref": "PV12",
                "ref": "receipt://delta-a",
                "sha256": sha256_bytes(b"delta-a"),
            }
        ],
        outcome="SUCCEEDED",
        confidence=1.0,
        counterevidence=[],
        contradictions=[],
        temporal={
            "observed_at": "2026-09-01T00:00:00Z",
            "valid_from": "2026-09-01T00:00:00Z",
            "expires_at": None,
        },
        privacy="PROJECT_PRIVATE",
        source_lineage_head_sha256=sha256_bytes(b"lineage"),
    )
    learning_root = root / "ai_learning"
    candidates = learning_root / "candidates"
    receipts = learning_root / "receipts"
    candidates.mkdir()
    receipts.mkdir()
    candidate = sealed["candidate"]
    atomic_write_json(candidates / f"{candidate['candidate_id']}.json", candidate)
    receipt_body = {
        "schema": "evidence-lane.learning-bootstrap-receipt.v1",
        "project_id": project_id,
        "candidate_id": candidate["candidate_id"],
        "recorded_at": "2026-09-01T00:00:00Z",
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    atomic_write_json(receipts / f"{receipt['receipt_sha256']}.json", receipt)

    quarantine = tmp_path / "quarantine"
    migrated = migrate_legacy_learning_files(
        root,
        project_id=project_id,
        quarantine_root=quarantine,
        confirmation=LEGACY_LEARNING_NORMALIZATION_CONFIRMATION,
    )

    assert migrated["status"] == "PASS"
    assert not candidates.exists()
    assert not receipts.exists()
    assert (quarantine / "candidates").is_dir()
    assert (quarantine / "receipts").is_dir()
    connection = sqlite3.connect(learning_root / "agent-learning.sqlite")
    try:
        assert connection.execute("SELECT COUNT(*) FROM learning_candidate").fetchone() == (
            1,
        )
        assert connection.execute("SELECT COUNT(*) FROM learning_receipt").fetchone() == (
            2,
        )
    finally:
        connection.close()
    inspected = inspect_learning_authority(root, project_id=project_id)
    assert inspected["candidate_storage"] == "SQLITE_ONLY"
    assert inspected["receipt_storage"] == "SQLITE_ONLY"

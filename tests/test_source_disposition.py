from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.source_disposition import (
    CORRECTION_BASE_SOURCE_COMMIT,
    DISPOSITIONS,
    build_all_source_disposition_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = (
    ROOT
    / "evidence"
    / "implementation_v42"
    / "ALL_SOURCE_DISPOSITION_RECEIPT.json"
)


def test_all_source_disposition_receipt_is_complete_and_reproducible() -> None:
    committed = json.loads(RECEIPT.read_text(encoding="utf-8"))
    rebuilt = build_all_source_disposition_receipt(ROOT)

    assert committed == rebuilt
    assert rebuilt["status"] == "PASS"
    assert rebuilt["correction_base_source_commit"] == CORRECTION_BASE_SOURCE_COMMIT
    assert rebuilt["source_count"] == 48
    assert [row["ordinal"] for row in rebuilt["sources"]] == list(range(1, 49))
    assert len({row["name"] for row in rebuilt["sources"]}) == 48
    assert set(rebuilt["disposition_counts"]) == set(DISPOSITIONS)
    assert all(row["evidence_files"] and row["test_files"] for row in rebuilt["sources"])
    assert rebuilt["historical_crosswalk_preserved"] is True
    assert rebuilt["source_payloads_mutated"] is False

    body = dict(rebuilt)
    expected_receipt_sha256 = body.pop("receipt_sha256")
    assert sha256_bytes(canonical_json_bytes(body)) == expected_receipt_sha256


def test_archive_and_upstream_dispositions_preserve_exact_boundaries() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    zip_rows = [row for row in receipt["sources"] if row["kind"] == "zip"]
    assert len(zip_rows) == 9
    assert sum(
        row["disposition"] == "EVIDENCE_BACKED_REJECTION" for row in zip_rows
    ) == 7
    assert sum(
        row["disposition"] == "ADOPTED_CONTRACT_AND_TEST" for row in zip_rows
    ) == 2

    upstream = {row["repository"]: row for row in receipt["upstream_references"]}
    harness = upstream["https://github.com/github/gh-aw-harness"]
    assert harness["disposition"] == "ADOPTED_CONTRACT_AND_TEST"
    assert harness["contract_id"] == "REGISTERED_AGENT_EXECUTION_HARNESS_V1"
    assert harness["sqlite_continuity_harness_used"] is False
    assert receipt["safety"][
        "sqlite_continuity_harness_distinct_from_agent_execution_harness"
    ] is True

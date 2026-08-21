from __future__ import annotations

from pathlib import Path

from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.successor_addendum import (
    LEGACY_EXPECTED_STATE_TRAVEL_SHA256,
    build_successor_addendum,
)


def test_successor_addendum_preserves_original_and_is_idempotent(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original-state-travel"
    original.mkdir()
    member = original / "manifest.json"
    member.write_text('{"immutable":true}\n', encoding="utf-8")
    before = sha256_file(member)
    arguments = {
        "original_package": original,
        "addendum_root": tmp_path / "successor-addendum",
        "release_sha": "a" * 40,
        "candidate_id": "PV3_CANDIDATE__RUN_TEST",
        "candidate_manifest_sha256": "B" * 64,
        "candidate_package_sha256": "C" * 64,
        "built_by": "human-test",
    }
    first = build_successor_addendum(**arguments)
    second = build_successor_addendum(**arguments)
    assert first["status"] == "PASS"
    assert second["status"] == "PASS"
    assert first["legacy_expected_sha256"] == (
        LEGACY_EXPECTED_STATE_TRAVEL_SHA256
    )
    assert first["original_unchanged"] is True
    assert second["original_tree_sha256_before"] == (
        first["original_tree_sha256_before"]
    )
    assert sha256_file(member) == before
    assert all(value == "REUSED" for value in second["actions"].values())
    assert second["candidate_accepted"] is False
    assert second["state_travel_performed"] is False

from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.site_operator_export import (
    build_mode_operator_site_payload,
    write_mode_operator_site_payload,
)


def test_site_operator_projection_is_source_backed_and_lane_specific() -> None:
    payload = build_mode_operator_site_payload()
    assert payload["mode_count"] == 16
    assert payload["selection_variants"] == ["plugin", "prompt"]
    assert payload["authority_hil_token_vocabulary"] == [
        "APPROVE",
        "APPROVE_WITH_DELTA",
        "MORE_RESEARCH",
        "ROLLBACK",
        "REJECT",
        "FAIL",
    ]
    assert payload["decision_count_is_behavior_ceiling"] is False
    modes = {row["id"]: row for row in payload["modes"]}
    code = modes["CD"]["variants"]["plugin"]
    analysis = modes["AL"]["variants"]["plugin"]
    assert code["ci_cd"]["required"] is True
    assert code["formula"]["rule"] == "ALL_REQUIRED_GATES == PASS"
    assert code["ci_cd"]["loop"] == "ALL_REQUIRED_GATES == PASS"
    assert {row["family"] for row in code["operators"]} >= {"MBA", "CHEMISTRY"}
    assert analysis["ci_cd"]["required"] is False
    assert code["hil"]["accepted_object"] != analysis["hil"]["accepted_object"]
    assert code["hil"]["choices"] != analysis["hil"]["choices"]
    assert all(
        variant["pointer_moved"] is False
        and variant["candidate_created"] is False
        and variant["lifecycle_effect"] == "NONE"
        for mode in modes.values()
        for variant in mode["variants"].values()
    )


def test_site_operator_projection_hash_and_file_are_deterministic(
    tmp_path: Path,
) -> None:
    first = build_mode_operator_site_payload()
    second = write_mode_operator_site_payload(tmp_path / "mode-governance.json")
    assert first == second
    written = json.loads(
        (tmp_path / "mode-governance.json").read_text(encoding="utf-8")
    )
    expected = written.pop("export_sha256")
    assert expected == sha256_bytes(canonical_json_bytes(written))


def test_checked_in_site_projection_is_fresh() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    generated_path = (
        repository_root
        / "apps"
        / "evidence-lane-app"
        / "app"
        / "_data"
        / "mode-governance.json"
    )
    checked_in = json.loads(generated_path.read_text(encoding="utf-8"))
    assert checked_in == build_mode_operator_site_payload()

from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes, sha256_file
from evidence_lane_plugin.project_authority import (
    nest_accepted_lane_history_in_current_sectors,
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_nests_only_non_plan_non_lineage_accepted_lanes_in_parallel(
    tmp_path: Path,
) -> None:
    project_id = "project-one"
    lanes_root = tmp_path / "accepted" / "PV12" / "lanes"
    sectors_root = tmp_path / "sectors"
    emitted = ["github_code", "local_code", "chat_lineage", "analysis", "plan"]
    for lane_id in emitted:
        source = lanes_root / lane_id
        source.mkdir(parents=True)
        (source / f"{lane_id}.mmd").write_text(
            f"flowchart TD\n  A[{lane_id}]\n", encoding="utf-8"
        )
        (source / f"{lane_id}.dot").write_text(
            f'digraph "{lane_id}" {{ A; }}\n', encoding="utf-8"
        )
    for lane_id in emitted:
        sector = sectors_root / lane_id
        sector.mkdir(parents=True)
        (sector / "live.txt").write_text(f"live:{lane_id}\n", encoding="utf-8")
    (sectors_root / "registry.json").write_text("{}\n", encoding="utf-8")
    (sectors_root / "routes.json").write_text("{}\n", encoding="utf-8")
    sector_members = {
        path.relative_to(sectors_root).as_posix(): sha256_file(path)
        for path in sorted(sectors_root.rglob("*"))
        if path.is_file()
    }
    _write_json(
        sectors_root / "SHA256SUMS.json",
        {
            "schema": "evidence-lane.recursive-sha256.v1",
            "members": sector_members,
            "member_count": len(sector_members),
        },
    )
    _write_json(
        sectors_root / "manifest.json",
        {
            "schema": "evidence-lane.universal-lane-bundle.v2",
            "bundle_sha256": sha256_bytes(canonical_json_bytes(sector_members)),
            "member_count": len(sector_members) + 2,
        },
    )
    accepted_members = {
        path.relative_to(lanes_root).as_posix(): sha256_file(path)
        for path in sorted(lanes_root.rglob("*"))
        if path.is_file()
    }
    _write_json(
        lanes_root / "SHA256SUMS.json",
        {
            "schema": "evidence-lane.recursive-sha256.v1",
            "members": accepted_members,
            "member_count": len(accepted_members),
        },
    )
    _write_json(
        lanes_root / "manifest.json",
        {
            "schema": "evidence-lane.universal-lane-bundle.v2",
            "emitted_lane_ids": emitted,
            "bundle_sha256": sha256_bytes(canonical_json_bytes(accepted_members)),
            "member_count": len(accepted_members) + 2,
        },
    )
    _write_json(
        tmp_path / "active_pointer.json",
        {
            "project_id": project_id,
            "accepted_pv": "PV12",
            "generation": 12,
        },
    )
    pointer_before = (tmp_path / "active_pointer.json").read_bytes()
    accepted_before = {
        path.relative_to(lanes_root).as_posix(): sha256_file(path)
        for path in sorted(lanes_root.rglob("*"))
        if path.is_file()
    }

    result = nest_accepted_lane_history_in_current_sectors(
        tmp_path,
        project_id=project_id,
        accepted_pv="PV12",
        pointer_generation=12,
    )

    assert result["status"] == "PASS"
    assert result["parallel_copy"] is True
    assert result["copied_lane_ids"] == ["github_code", "analysis"]
    assert result["excluded_lane_ids"] == ["local_code", "chat_lineage", "plan"]
    for lane_id in ("github_code", "analysis"):
        target = sectors_root / lane_id / "accepted_history" / "PV12"
        assert target.is_dir()
        assert (target / f"{lane_id}.mmd").is_file()
        assert (target / f"{lane_id}.dot").is_file()
        assert (sectors_root / lane_id / "live.txt").read_text(encoding="utf-8") == (
            f"live:{lane_id}\n"
        )
    for lane_id in ("local_code", "chat_lineage", "plan"):
        assert not (sectors_root / lane_id / "accepted_history").exists()
    assert (tmp_path / "active_pointer.json").read_bytes() == pointer_before
    accepted_after = {
        path.relative_to(lanes_root).as_posix(): sha256_file(path)
        for path in sorted(lanes_root.rglob("*"))
        if path.is_file()
    }
    assert accepted_after == accepted_before

    reused = nest_accepted_lane_history_in_current_sectors(
        tmp_path,
        project_id=project_id,
        accepted_pv="PV12",
        pointer_generation=12,
    )
    assert all(row["action"] == "REUSED" for row in reused["reports"])

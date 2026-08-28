from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.connector_governance import ConnectorGovernance
from evidence_lane_plugin.errors import EvidenceLaneError


def _brain(lane_id: str, marker: str) -> dict[str, str]:
    values = iter("ABCDE")
    return {
        "lane_id": lane_id,
        "database_sha256": next(values) * 64,
        "mmd_sha256": next(values) * 64,
        "dot_sha256": next(values) * 64,
        "tools_sha256": next(values) * 64,
        "content_identity_sha256": marker * 64,
    }


def _register(
    governance: ConnectorGovernance,
    *,
    project_id: str,
    brains: list[dict[str, str]],
    head: str = "B",
) -> dict[str, object]:
    return governance.register_universe_project(
        project_id=project_id,
        project_root_identity_sha256="A" * 64,
        universe_head_sha256=head * 64,
        pointer_generation=3,
        mini_brains=brains,
    )


def test_federation_reuses_hashes_and_advances_only_changed_heads(
    tmp_path: Path,
) -> None:
    database = tmp_path / "connector-brain.sqlite"
    governance = ConnectorGovernance(database)
    initial = _register(
        governance,
        project_id="project-a",
        brains=[_brain("local_code", "1"), _brain("research", "2")],
    )
    assert initial["state"] == "ADVANCED_ATOMICALLY"
    assert initial["writes_performed"] is True
    assert initial["changed_lane_ids"] == ["local_code", "research"]

    reused = _register(
        governance,
        project_id="project-a",
        brains=[_brain("local_code", "1"), _brain("research", "2")],
    )
    assert reused["state"] == "REUSED_NO_WRITE"
    assert reused["writes_performed"] is False
    assert reused["mini_brain_ids"] == initial["mini_brain_ids"]

    advanced = _register(
        governance,
        project_id="project-a",
        brains=[_brain("local_code", "3"), _brain("research", "2")],
        head="C",
    )
    assert advanced["state"] == "ADVANCED_ATOMICALLY"
    assert advanced["changed_lane_ids"] == ["local_code"]
    assert len(advanced["inserted_mini_brain_ids"]) == 1
    assert len(advanced["reused_mini_brain_ids"]) == 1
    assert advanced["historical_mini_brain_refs_preserved"] is True

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM universe_mini_brain_ref WHERE project_id='project-a'"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM universe_project_lane_head WHERE project_id='project-a'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM universe_federation_fts WHERE project_id='project-a'"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_cross_project_edges_require_grant_and_hash_only_evidence(
    tmp_path: Path,
) -> None:
    governance = ConnectorGovernance(tmp_path / "connector-brain.sqlite")
    left = _register(
        governance,
        project_id="project-a",
        brains=[_brain("local_code", "1")],
    )
    right = _register(
        governance,
        project_id="project-b",
        brains=[_brain("research", "2")],
    )
    source = str(left["mini_brain_ids"][0])
    target = str(right["mini_brain_ids"][0])

    with pytest.raises(EvidenceLaneError) as missing_grant:
        governance.link_universe_mini_brains(
            source_mini_brain_id=source,
            target_mini_brain_id=target,
            relation="supports",
            explicit_grant_sha256="invalid",
            evidence={"relation_evidence_sha256": "C" * 64},
        )
    assert missing_grant.value.code == "CROSS_PROJECT_UNIVERSE_GRANT_REQUIRED"

    with pytest.raises(EvidenceLaneError) as raw_evidence:
        governance.link_universe_mini_brains(
            source_mini_brain_id=source,
            target_mini_brain_id=target,
            relation="supports",
            explicit_grant_sha256="D" * 64,
            evidence={"raw_text": "do not copy this"},
        )
    assert raw_evidence.value.code == (
        "CROSS_PROJECT_UNIVERSE_EVIDENCE_HASHES_REQUIRED"
    )

    appended = governance.link_universe_mini_brains(
        source_mini_brain_id=source,
        target_mini_brain_id=target,
        relation="supports",
        explicit_grant_sha256="D" * 64,
        evidence={"relation_evidence_sha256": "C" * 64},
    )
    assert appended["state"] == "APPENDED"
    assert appended["writes_performed"] is True
    assert appended["project_truth_merged"] is False
    assert appended["raw_cross_project_payload_copied"] is False

    reused = governance.link_universe_mini_brains(
        source_mini_brain_id=source,
        target_mini_brain_id=target,
        relation="supports",
        explicit_grant_sha256="D" * 64,
        evidence={"relation_evidence_sha256": "C" * 64},
    )
    assert reused["state"] == "REUSED_NO_WRITE"
    assert reused["writes_performed"] is False
    assert reused["edge_id"] == appended["edge_id"]
    assert reused["recorded_at"] == appended["recorded_at"]

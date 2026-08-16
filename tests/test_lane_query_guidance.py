from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
ROOT_SKILL = PLUGIN / "skills" / "evi" / "SKILL.md"
SOURCE_INTAKE_SKILL = PLUGIN / "skills" / "evi-source-intake" / "SKILL.md"

WORKFLOW_ID = "EVIDENCE_LANE_BOUNDED_LANE_QUERY_V1"
ACCEPTED_TEMPLATE = (
    "<EVIDENCE_LANE_DATA_ROOT>/projects/<project_id>/accepted/<PVn>/lanes/"
    "<canonical_lane_id>/<sqlite_filename>"
)
CANDIDATE_TEMPLATE = (
    "<EVIDENCE_LANE_DATA_ROOT>/projects/<project_id>/candidates/"
    "<candidate_id>/lanes/<canonical_lane_id>/<sqlite_filename>"
)


def test_root_routes_to_the_single_authoritative_lane_query_workflow() -> None:
    root = ROOT_SKILL.read_text(encoding="utf-8")

    assert root.count(WORKFLOW_ID) == 1
    assert "../evi-source-intake/SKILL.md" in root
    assert ACCEPTED_TEMPLATE in root
    assert CANDIDATE_TEMPLATE in root
    assert "never permission to hunt for, open, copy, or" in root
    assert "offload a whole lane SQLite database" in root


def test_source_intake_defines_bounded_order_limits_and_path_templates() -> None:
    source_intake = SOURCE_INTAKE_SKILL.read_text(encoding="utf-8")

    assert source_intake.count(WORKFLOW_ID) == 1
    positions = [
        source_intake.index(f"`{tool}`")
        for tool in ("lane_catalog", "lane_status", "lane_search", "lane_fetch")
    ]
    assert positions == sorted(positions)
    assert 'retrieval="hybrid"' in source_intake
    assert "`limit=20`" in source_intake
    assert "1 through 100 results" in source_intake
    assert "first 12 lexical query terms" in source_intake
    assert "`max_bytes=100000`" in source_intake
    assert "1 through 1,000,000 bytes" in source_intake
    assert ACCEPTED_TEMPLATE in source_intake
    assert CANDIDATE_TEMPLATE in source_intake


def test_source_intake_preserves_result_provenance_and_fails_closed() -> None:
    source_intake = SOURCE_INTAKE_SKILL.read_text(encoding="utf-8")

    for field in (
        "project_id",
        "pv_ref",
        "ref_id",
        "path",
        "locator",
        "chunk_sha256",
        "source_sha256",
        "parser_state",
        "sha256",
        "size_bytes",
        "truncated",
    ):
        assert field in source_intake
    assert "`EMPTY` is a\n   valid no-hit result" in source_intake
    assert "`STALE`, candidate, or dirty-live-source evidence stays" in source_intake
    assert "Do not use shell SQL, direct\nfilesystem discovery, arbitrary SQL" in source_intake
    assert "Fail closed on an invalid bundle" in source_intake
    assert "do not simulate them by widening this single-lane route" in source_intake

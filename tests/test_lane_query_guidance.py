from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
ROOT_SKILL = PLUGIN / "skills" / "evi" / "SKILL.md"
SOURCE_INTAKE_SKILL = PLUGIN / "skills" / "evi-source-intake" / "SKILL.md"

WORKFLOW_ID = "EVIDENCE_LANE_LIVE_ROOT_CURRENT_AUTHORITY_QUERY_V2"
LIVE_ROOT_TEMPLATE = (
    "<project-root>/sectors/<canonical_lane_id>/<sqlite_filename>"
)


def test_root_routes_to_the_single_authoritative_lane_query_workflow() -> None:
    root = ROOT_SKILL.read_text(encoding="utf-8")

    assert root.count(WORKFLOW_ID) == 1
    assert "../evi-source-intake/SKILL.md" in root
    assert "never opens an accepted HIL ZIP or candidate" in root
    assert "never uses an\naccepted-directory diagnostic template" in root
    assert "never substitute transcript, scrollback, or browser history" in root


def test_source_intake_defines_bounded_order_limits_and_path_templates() -> None:
    source_intake = SOURCE_INTAKE_SKILL.read_text(encoding="utf-8")

    assert source_intake.count(WORKFLOW_ID) == 1
    positions = [
        source_intake.index(f"`{tool}`")
        for tool in ("lane_catalog", "search", "lane_status", "lane_search", "lane_fetch")
    ]
    assert positions == sorted(positions)
    assert "SQLite/FTS5 projections with BM25" in source_intake
    assert "Retry those four\n   bounded reads exactly once" in source_intake
    assert LIVE_ROOT_TEMPLATE in source_intake
    assert "never widen to the accepted ZIP" in source_intake


def test_source_intake_preserves_result_provenance_and_fails_closed() -> None:
    source_intake = SOURCE_INTAKE_SKILL.read_text(encoding="utf-8")

    for field in (
        "project",
        "live-root bundle",
        "branch/HEAD",
        "lane",
        "locator",
        "content hash",
        "freshness",
        "ENV/UOP",
        "instruction source-chain",
    ):
        assert field in source_intake
    assert "A continuing no-hit is valid" in source_intake
    assert "Omit every candidate or accepted-archive selector" in source_intake
    assert "Never load a full SQLite/FTS corpus" in source_intake
    assert "fail closed on this workflow" in source_intake

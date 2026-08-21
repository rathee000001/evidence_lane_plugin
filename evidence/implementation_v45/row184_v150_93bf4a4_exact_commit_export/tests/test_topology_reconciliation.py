from __future__ import annotations

import sqlite3
from pathlib import Path

from evidence_lane_plugin import lane_engine as lane_engine_module
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
from evidence_lane_plugin.topology_reconciliation import reconcile_lane_topology


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE source_registry(source_id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE chunk_index(chunk_id INTEGER PRIMARY KEY, source_id INTEGER);
            CREATE TABLE structured_fact(fact_id INTEGER PRIMARY KEY, kind TEXT);
            INSERT INTO source_registry(path) VALUES ('source.py');
            INSERT INTO chunk_index(source_id) VALUES (1);
            INSERT INTO structured_fact(kind) VALUES ('code_symbol');
            """
        )
        connection.commit()
    finally:
        connection.close()


def _graphs(*, source_rows: int = 1) -> tuple[str, str]:
    nodes = (
        ("ROOT", "lane<br/>1 sources &#124; 1 chunks &#124; 1 structured facts"),
        ("SOURCES", f"source_registry<br/>rows={source_rows}"),
        ("CHUNKS", "chunk_index<br/>rows=1"),
        ("FACTS", "structured_fact<br/>rows=1 &#124; kinds=1"),
        ("KIND", "code_symbol<br/>rows=1"),
        ("SEARCH", "search index"),
        ("POINTER", "candidate pointer"),
        ("OUTPUT", "inspectable output"),
    )
    edges = (
        ("ROOT", "SOURCES"),
        ("SOURCES", "CHUNKS"),
        ("CHUNKS", "FACTS"),
        ("FACTS", "KIND"),
        ("KIND", "SEARCH"),
        ("SEARCH", "POINTER"),
        ("POINTER", "OUTPUT"),
    )
    groups = (
        ("INTAKE", nodes[:2]),
        ("SEMANTIC", nodes[2:5]),
        ("RETRIEVAL", nodes[5:6]),
        ("OUTPUTS", nodes[6:]),
    )
    mmd = ["flowchart TB", "    classDef root fill:#fff;"]
    dot = [
        "digraph lane_fixture {",
        '  rankdir="TB";',
        '  graph [fontname="Arial"];',
        '  node [shape="box"];',
        '  edge [color="#667085"];',
    ]
    for group, group_nodes in groups:
        mmd.extend([f'    subgraph {group}["{group}"]', "        direction TB"])
        dot.append(f'  subgraph cluster_{group.lower()} {{ label="{group}";')
        for node_id, label in group_nodes:
            mmd.append(f'        {node_id}["{label}"]:::root')
            dot_label = label.replace("<br/>", "\\n").replace("&#124;", "|")
            dot.append(
                f'    {node_id} [label="{dot_label}",color="#667085"];'
            )
        mmd.append("    end")
        dot.append("  }")
    for source, target in edges:
        mmd.append(f"    {source} --> {target}")
        dot.append(f"  {source} -> {target};")
    dot.append("}")
    return "\n".join(mmd) + "\n", "\n".join(dot) + "\n"


def _write_fixture(root: Path, *, source_rows: int = 1) -> None:
    root.mkdir()
    _database(root / "lane.sqlite")
    mmd, dot = _graphs(source_rows=source_rows)
    (root / "lane.mmd").write_text(mmd, encoding="utf-8")
    (root / "lane.dot").write_text(dot, encoding="utf-8")


def _reconcile(root: Path, *, lane_id: str = "fixture") -> dict[str, object]:
    return reconcile_lane_topology(
        root,
        lane_id=lane_id,
        mmd_filename="lane.mmd",
        dot_filename="lane.dot",
        sqlite_filename="lane.sqlite",
    )


def test_sqlite_mermaid_dot_reconciliation_passes_for_agreeing_graphs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agreeing"
    _write_fixture(root)

    result = _reconcile(root)

    assert result["status"] == "PASS"
    assert result["claims_failed"] == 0
    assert result["rendering_parity"]["status"] == "PASS"


def test_v090_six_line_stub_fails_structural_floors(tmp_path: Path) -> None:
    root = tmp_path / "v090-stub"
    _write_fixture(root)
    (root / "lane.mmd").write_text(
        "flowchart TD\n  ROOT[Lane]\n  DB[(SQLite)]\n  ROOT --> DB\n",
        encoding="utf-8",
    )
    (root / "lane.dot").write_text(
        "digraph lane {\n  ROOT [label=\"Lane\",color=\"#000\"];\n"
        "  DB [label=\"SQLite\",color=\"#000\"];\n  ROOT -> DB;\n}\n",
        encoding="utf-8",
    )

    result = _reconcile(root)

    assert result["status"] == "FAIL"
    assert result["structural"]["mermaid"]["below_minimum"]
    assert result["claims_checked"] == 0


def test_v090_understated_sqlite_count_fails_claim_reconciliation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "understated"
    _write_fixture(root, source_rows=0)

    result = _reconcile(root)

    assert result["status"] == "FAIL"
    assert result["claims_failed"] >= 2
    assert any(
        claim["subject"] == "source_registry"
        for claim in result["failed_claims"]
    )


def test_v090_mermaid_dot_divergence_fails_identity_parity(tmp_path: Path) -> None:
    root = tmp_path / "divergent"
    _write_fixture(root)
    dot_path = root / "lane.dot"
    dot_path.write_text(
        dot_path.read_text(encoding="utf-8").replace("  POINTER -> OUTPUT;\n", ""),
        encoding="utf-8",
    )

    result = _reconcile(root)

    assert result["status"] == "FAIL"
    assert result["rendering_parity"]["status"] == "FAIL"
    assert result["rendering_parity"]["identity_mismatches"]["edges"]


def test_generic_graph_fails_primary_code_logical_contract(tmp_path: Path) -> None:
    root = tmp_path / "generic-code-graph"
    _write_fixture(root)

    result = _reconcile(root, lane_id="github_code")

    assert result["status"] == "FAIL"
    contract = result["logical_code_contract"]
    assert contract["status"] == "FAIL"
    assert contract["mermaid"]["missing_subgraphs"] == [
        "CODE_LOGICAL_TOPOLOGY"
    ]
    assert "CODE_SECTOR" in contract["mermaid"]["missing_nodes"]


def _write_schema_derived_lane(root: Path, lane_id: str) -> None:
    lane = LANE_REGISTRY[lane_id]
    root.mkdir()
    database_path = root / lane.sqlite_filename
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        lane_engine_module._create_lane_schema(connection, lane)
        connection.commit()
    finally:
        connection.close()
    mermaid, dot = lane_engine_module._lane_topology(lane, database_path, {})
    (root / lane.mmd_filename).write_text(mermaid, encoding="utf-8")
    (root / lane.dot_filename).write_text(dot, encoding="utf-8")


def test_all_eighteen_lanes_have_exact_additive_physical_schema_contract(
    tmp_path: Path,
) -> None:
    for lane_id in CANONICAL_LANE_IDS:
        root = tmp_path / lane_id
        _write_schema_derived_lane(root, lane_id)
        lane = LANE_REGISTRY[lane_id]

        result = reconcile_lane_topology(
            root,
            lane_id=lane_id,
            mmd_filename=lane.mmd_filename,
            dot_filename=lane.dot_filename,
            sqlite_filename=lane.sqlite_filename,
        )

        assert result["status"] == "PASS"
        physical = result["physical_schema_contract"]
        assert physical["status"] == "PASS"
        assert physical["mermaid"]["required_contract_tables"] == list(
            lane.schema_contract
        )
        assert physical["mermaid"]["auxiliary_tables"]
        assert physical["mermaid"]["expected_projection_sha256"] == (
            physical["dot"]["claimed_projection_sha256"]
        )


def test_physical_schema_contract_rejects_same_tamper_in_both_renderings(
    tmp_path: Path,
) -> None:
    lane_id = "chat_lineage"
    lane = LANE_REGISTRY[lane_id]
    root = tmp_path / lane_id
    _write_schema_derived_lane(root, lane_id)
    for path in (root / lane.mmd_filename, root / lane.dot_filename):
        text = path.read_text(encoding="utf-8")
        path.write_text(
            "\n".join(
                line for line in text.splitlines() if "PHYSICAL_TABLE_002" not in line
            )
            + "\n",
            encoding="utf-8",
        )

    result = reconcile_lane_topology(
        root,
        lane_id=lane_id,
        mmd_filename=lane.mmd_filename,
        dot_filename=lane.dot_filename,
        sqlite_filename=lane.sqlite_filename,
    )

    assert result["status"] == "FAIL"
    assert result["rendering_parity"]["status"] == "PASS"
    assert "PHYSICAL_TABLE_002" in result["physical_schema_contract"]["mermaid"][
        "missing_nodes"
    ]

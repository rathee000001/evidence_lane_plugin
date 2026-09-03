"""Reconcile lane SQLite truth with its Mermaid and DOT projections.

Prefix-only graph validation allowed the historical v0.9 stubs to pass.  This
module parses both renderings, enforces structural floors, compares their exact
node/edge/subgraph identities, and checks every emitted row-count claim against
the read-only lane database.
"""

from __future__ import annotations

import html
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .lanes import (
    CODE_LOGICAL_TOPOLOGY,
    CORE_SCHEMA_TABLES,
    LANE_REGISTRY,
    PRIMARY_CODE_LANES,
)
from .schema_topology import (
    physical_schema_projection,
    physical_table_groups,
    physical_table_node_ids,
)

RECONCILIATION_SCHEMA = "evidence-lane.topology-reconciliation.v4"
MIN_SUBGRAPHS = 4
MIN_NODES = 8
MIN_EDGES = 6

_MMD_SUBGRAPH = re.compile(
    r'^\s*subgraph\s+(?P<id>[A-Za-z0-9_]+)\["(?P<label>.*)"\]\s*$'
)
_MMD_NODE = re.compile(
    r'^\s*(?P<id>[A-Za-z0-9_]+)\["(?P<label>.*)"\]'
    r'(?::::(?P<kind>[A-Za-z0-9_]+))?\s*$'
)
_MMD_EDGE = re.compile(
    r"^\s*(?P<src>[A-Za-z0-9_]+)\s*-->\s*"
    r"(?:\|(?P<label>[^|]*)\|\s*)?(?P<dst>[A-Za-z0-9_]+)\s*$"
)
_DOT_CLUSTER = re.compile(r"^\s*subgraph\s+(?P<id>cluster_[A-Za-z0-9_]+)\s*\{")
_DOT_NODE = re.compile(
    r'^\s*(?P<id>"[A-Za-z0-9_]+"|[A-Za-z0-9_]+)\s*'
    r"\[(?P<attrs>.+)\]\s*;?\s*$"
)
_DOT_EDGE = re.compile(
    r'^\s*(?P<src>"[A-Za-z0-9_]+"|[A-Za-z0-9_]+)\s*->\s*'
    r'(?P<dst>"[A-Za-z0-9_]+"|[A-Za-z0-9_]+)\s*'
    r"(?:\[(?P<attrs>.+)\])?\s*;?\s*$"
)
_DOT_LABEL = re.compile(
    r'(?:^|[,\s])label=(?:"(?P<quoted>(?:\\.|[^"\\])*)"|(?P<plain>[^,\s\]]+))'
)
_ROWS_CLAIM = re.compile(r"\brows=(\d+)")
_SOURCES_CLAIM = re.compile(r"\b(\d+)\s+sources\b")
_CHUNKS_CLAIM = re.compile(r"\b(\d+)\s+chunks\b")
_FACTS_CLAIM = re.compile(r"\b(\d+)\s+structured facts\b")
_KINDS_CLAIM = re.compile(r"\bkinds=(\d+)")
_PROJECTION_SHA_CLAIM = re.compile(r"\bprojection_sha256=([A-F0-9]{64})\b")
_SAFE_TABLE = re.compile(r"^[a-z][a-z0-9_]*$")
_CODE_LOGICAL_TABLES = {
    logical_table: physical_table
    for logical_table, _display_label, physical_table in CODE_LOGICAL_TOPOLOGY
}
_CODE_LOGICAL_NODE_IDS = {
    logical_table: logical_table.upper()
    for logical_table, _display_label, _physical_table in CODE_LOGICAL_TOPOLOGY
}


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: str
    label: str
    kind: str | None = None

    @property
    def normalized_label(self) -> str:
        return (
            html.unescape(self.label)
            .replace('\\"', '"')
            .replace("\\n", "\n")
        )

    @property
    def parts(self) -> list[str]:
        return [
            part.strip()
            for part in re.split(r"<br\s*/?>|[\r\n]+", self.normalized_label)
        ]

    @property
    def head(self) -> str:
        return self.parts[0] if self.parts else ""


@dataclass(slots=True)
class ParsedGraph:
    subgraphs: list[str] = field(default_factory=list)
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    balanced: bool = True
    errors: list[str] = field(default_factory=list)

    @property
    def node_ids(self) -> set[str]:
        return {node.node_id for node in self.nodes}

    def counts(self) -> dict[str, int]:
        return {
            "subgraphs": len(self.subgraphs),
            "nodes": len(self.nodes),
            "edges": len(self.edges),
        }


def parse_mermaid(text: str) -> ParsedGraph:
    graph = ParsedGraph()
    if not text.startswith("flowchart "):
        graph.errors.append("mermaid does not begin with 'flowchart '")
        return graph
    depth = 0
    in_layout_constraints = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped == "%% EVIDENCE_LANE_LAYOUT_CONSTRAINTS_BEGIN":
            in_layout_constraints = True
            continue
        if stripped == "%% EVIDENCE_LANE_LAYOUT_CONSTRAINTS_END":
            in_layout_constraints = False
            continue
        if in_layout_constraints:
            continue
        if not stripped or stripped.startswith(("classDef ", "direction ", "%%")):
            continue
        if stripped.startswith("flowchart "):
            continue
        match = _MMD_SUBGRAPH.match(line)
        if match:
            graph.subgraphs.append(match.group("id"))
            depth += 1
            continue
        if stripped == "end":
            depth -= 1
            if depth < 0:
                graph.errors.append(f"line {number}: end without an open subgraph")
                graph.balanced = False
                depth = 0
            continue
        match = _MMD_EDGE.match(line)
        if match:
            graph.edges.append((match.group("src"), match.group("dst")))
            continue
        match = _MMD_NODE.match(line)
        if match:
            graph.nodes.append(
                GraphNode(match.group("id"), match.group("label"), match.group("kind"))
            )
            continue
        graph.errors.append(f"line {number}: unparsed Mermaid statement")
    if depth:
        graph.errors.append(f"{depth} Mermaid subgraph(s) never closed")
        graph.balanced = False
    return graph


def parse_dot(text: str) -> ParsedGraph:
    graph = ParsedGraph()
    if not text.startswith("digraph "):
        graph.errors.append("dot does not begin with 'digraph '")
        return graph
    depth = 0
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if stripped.startswith("digraph ") and stripped.endswith("{"):
            depth += 1
            continue
        match = _DOT_CLUSTER.match(line)
        if match:
            graph.subgraphs.append(match.group("id"))
            depth += 1
            continue
        if stripped.startswith(
            ("rankdir", "label=", "graph [", "node [", "edge [")
        ) or ("=" in stripped and "[" not in stripped and "->" not in stripped):
            continue
        if stripped == "}":
            depth -= 1
            if depth < 0:
                graph.errors.append(f"line {number}: closing brace without an opener")
                graph.balanced = False
                depth = 0
            continue
        match = _DOT_EDGE.match(line)
        if match:
            attributes = str(match.group("attrs") or "")
            if re.search(r"(?:^|[,\s])style=invis(?:[,\s]|$)", attributes):
                continue
            graph.edges.append(
                (
                    match.group("src").strip('"'),
                    match.group("dst").strip('"'),
                )
            )
            continue
        match = _DOT_NODE.match(line)
        if match:
            attributes = match.group("attrs")
            label_match = _DOT_LABEL.search(attributes)
            label = (
                (label_match.group("quoted") or label_match.group("plain") or "")
                if label_match
                else match.group("id")
            )
            graph.nodes.append(GraphNode(match.group("id").strip('"'), label))
            continue
        graph.errors.append(f"line {number}: unparsed DOT statement")
    if depth:
        graph.errors.append(f"{depth} DOT block(s) never closed")
        graph.balanced = False
    return graph


def _connect(database_path: Path) -> sqlite3.Connection:
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _count(connection: sqlite3.Connection, table: str) -> int | None:
    if not _SAFE_TABLE.fullmatch(table):
        return None
    try:
        quoted = table.replace('"', '""')
        return int(connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0])  # nosec B608
    except sqlite3.DatabaseError:
        return None


def _fact_kind_counts(connection: sqlite3.Connection) -> dict[str, int]:
    try:
        return {
            str(row["kind"]): int(row["count"])
            for row in connection.execute(
                "SELECT kind, COUNT(*) AS count FROM structured_fact GROUP BY kind"
            )
        }
    except sqlite3.DatabaseError:
        return {}


def _claim(
    *, subject: str, node_id: str, claimed: int, actual: int | None, basis: str
) -> dict[str, Any]:
    agrees = actual is not None and claimed == actual
    return {
        "subject": subject,
        "node_id": node_id,
        "claimed": claimed,
        "actual": actual,
        "basis": basis,
        "status": "PASS" if agrees else "FAIL",
    }


def reconcile_graph_against_database(
    graph: ParsedGraph,
    database_path: str | Path,
    *,
    logical_table_projection: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    projection = logical_table_projection or {}
    connection = _connect(Path(database_path))
    try:
        tables = _table_names(connection)
        kind_counts = _fact_kind_counts(connection)
        sources = _count(connection, "source_registry")
        chunks = _count(connection, "chunk_index")
        facts = _count(connection, "structured_fact")
        claims: list[dict[str, Any]] = []
        for node in graph.nodes:
            if node.node_id.startswith("FACT_SAMPLE_"):
                # Sample labels are source evidence, not generator-owned count
                # assertions.  A sampled DOT/Mermaid/JSON fragment may itself
                # contain ``rows=`` or a table-like head and must never be
                # reinterpreted as a topology claim.
                continue
            label = node.normalized_label
            head = node.head
            root_sources = _SOURCES_CLAIM.search(label)
            root_chunks = _CHUNKS_CLAIM.search(label)
            root_facts = _FACTS_CLAIM.search(label)
            if root_sources and root_chunks and root_facts:
                claims.extend(
                    (
                        _claim(
                            subject="source_registry",
                            node_id=node.node_id,
                            claimed=int(root_sources.group(1)),
                            actual=sources,
                            basis="lane root source count",
                        ),
                        _claim(
                            subject="chunk_index",
                            node_id=node.node_id,
                            claimed=int(root_chunks.group(1)),
                            actual=chunks,
                            basis="lane root chunk count",
                        ),
                        _claim(
                            subject="structured_fact",
                            node_id=node.node_id,
                            claimed=int(root_facts.group(1)),
                            actual=facts,
                            basis="lane root fact count",
                        ),
                    )
                )
                continue
            rows = _ROWS_CLAIM.search(label)
            if rows is not None:
                claimed = int(rows.group(1))
                if node.node_id.startswith("FACT_KIND_") and head in kind_counts:
                    claims.append(
                        _claim(
                            subject=f"structured_fact[{head}]",
                            node_id=node.node_id,
                            claimed=claimed,
                            actual=kind_counts.get(head, 0),
                            basis="structured_fact kind row count",
                        )
                    )
                elif head in projection:
                    physical_table = projection[head]
                    claims.append(
                        _claim(
                            subject=f"{head}->{physical_table}",
                            node_id=node.node_id,
                            claimed=claimed,
                            actual=_count(connection, physical_table),
                            basis="logical code topology SQLite projection",
                        )
                    )
                elif head in tables:
                    claims.append(
                        _claim(
                            subject=head,
                            node_id=node.node_id,
                            claimed=claimed,
                            actual=_count(connection, head),
                            basis="SQLite table row count",
                        )
                    )
                elif head in kind_counts or head.startswith("code_"):
                    claims.append(
                        _claim(
                            subject=f"structured_fact[{head}]",
                            node_id=node.node_id,
                            claimed=claimed,
                            actual=kind_counts.get(head, 0),
                            basis="structured_fact kind row count",
                        )
                    )
                else:
                    claims.append(
                        {
                            "subject": head,
                            "node_id": node.node_id,
                            "claimed": claimed,
                            "actual": None,
                            "basis": "unresolved rows claim",
                            "status": "FAIL",
                        }
                    )
            kinds = _KINDS_CLAIM.search(label)
            if kinds is not None and head == "structured_fact":
                claims.append(
                    _claim(
                        subject="structured_fact.kinds",
                        node_id=node.node_id,
                        claimed=int(kinds.group(1)),
                        actual=len(kind_counts),
                        basis="distinct structured_fact kinds",
                    )
                )
        return claims
    finally:
        connection.close()


def structural_report(graph: ParsedGraph) -> dict[str, Any]:
    counts = graph.counts()
    subgraph_ids = {
        item.removeprefix("cluster_").upper() for item in graph.subgraphs
    }
    dangling = sorted(
        {
            endpoint
            for edge in graph.edges
            for endpoint in edge
            if endpoint not in graph.node_ids and endpoint.upper() not in subgraph_ids
        }
    )
    minimums = {
        "subgraphs": MIN_SUBGRAPHS,
        "nodes": MIN_NODES,
        "edges": MIN_EDGES,
    }
    below = sorted(name for name, minimum in minimums.items() if counts[name] < minimum)
    valid = not below and not dangling and graph.balanced and not graph.errors
    return {
        "counts": counts,
        "minimums": minimums,
        "below_minimum": below,
        "dangling_edge_endpoints": dangling,
        "balanced": graph.balanced,
        "parse_errors": graph.errors,
        "status": "PASS" if valid else "FAIL",
    }


def logical_code_contract_report(
    graph: ParsedGraph, *, lane_id: str
) -> dict[str, Any]:
    """Require the authorized code-builder shape for both primary code lanes."""

    if lane_id not in PRIMARY_CODE_LANES:
        return {
            "applicable": False,
            "required_root": None,
            "required_subgraph": None,
            "missing_subgraphs": [],
            "missing_nodes": [],
            "missing_root_edges": [],
            "head_mismatches": [],
            "status": "NOT_APPLICABLE",
        }

    subgraphs = {
        item.removeprefix("cluster_").upper() for item in graph.subgraphs
    }
    nodes = {node.node_id: node for node in graph.nodes}
    required_nodes = {"CODE_SECTOR", *_CODE_LOGICAL_NODE_IDS.values()}
    required_edges = {
        ("PHYSICAL_SCHEMA_SECTOR", "CODE_SECTOR"),
        *(("CODE_SECTOR", node_id) for node_id in _CODE_LOGICAL_NODE_IDS.values()),
    }
    missing_subgraphs = (
        []
        if "CODE_LOGICAL_TOPOLOGY" in subgraphs
        else ["CODE_LOGICAL_TOPOLOGY"]
    )
    missing_nodes = sorted(required_nodes - nodes.keys())
    missing_root_edges = sorted(required_edges - set(graph.edges))
    head_mismatches = sorted(
        {
            f"{node_id}:{nodes[node_id].head!r}!={logical_table!r}"
            for logical_table, node_id in _CODE_LOGICAL_NODE_IDS.items()
            if node_id in nodes and nodes[node_id].head != logical_table
        }
    )
    valid = not (
        missing_subgraphs or missing_nodes or missing_root_edges or head_mismatches
    )
    return {
        "applicable": True,
        "required_root": "CODE_SECTOR",
        "required_subgraph": "CODE_LOGICAL_TOPOLOGY",
        "required_logical_tables": list(_CODE_LOGICAL_TABLES),
        "physical_table_projection": dict(_CODE_LOGICAL_TABLES),
        "missing_subgraphs": missing_subgraphs,
        "missing_nodes": missing_nodes,
        "missing_root_edges": [list(edge) for edge in missing_root_edges],
        "head_mismatches": head_mismatches,
        "status": "PASS" if valid else "FAIL",
    }


def physical_schema_contract_report(
    graph: ParsedGraph,
    *,
    lane_id: str,
    database_path: str | Path,
) -> dict[str, Any]:
    """Require one exact additive SQLite-schema projection for every lane."""

    if lane_id not in LANE_REGISTRY:
        return {
            "applicable": False,
            "required_subgraph": None,
            "missing_subgraphs": [],
            "missing_nodes": [],
            "duplicate_nodes": [],
            "unexpected_physical_nodes": [],
            "missing_root_edges": [],
            "missing_relation_edges": [],
            "head_mismatches": [],
            "label_mismatches": [],
            "missing_contract_tables": [],
            "expected_projection_sha256": None,
            "claimed_projection_sha256": None,
            "status": "NOT_APPLICABLE",
        }

    lane = LANE_REGISTRY[lane_id]
    connection = _connect(Path(database_path))
    try:
        projection = physical_schema_projection(connection, lane)
    finally:
        connection.close()
    table_nodes = physical_table_node_ids(projection)
    table_groups = physical_table_groups(projection)
    expected_node_ids = {
        "PHYSICAL_SCHEMA_SECTOR",
        *table_groups.keys(),
        *table_nodes.values(),
    }
    subgraphs = {
        item.removeprefix("cluster_").upper() for item in graph.subgraphs
    }
    node_counts = Counter(node.node_id for node in graph.nodes)
    nodes = {node.node_id: node for node in graph.nodes}
    graph_edges = set(graph.edges)
    missing_subgraphs = (
        [] if "SQLITE_PHYSICAL_SCHEMA" in subgraphs else ["SQLITE_PHYSICAL_SCHEMA"]
    )
    missing_nodes = sorted(expected_node_ids - nodes.keys())
    duplicate_nodes = sorted(
        node_id for node_id in expected_node_ids if node_counts[node_id] > 1
    )
    unexpected_physical_nodes = sorted(
        node_id
        for node_id in nodes
        if node_id.startswith(("PHYSICAL_TABLE_", "PHYSICAL_GROUP_"))
        and node_id not in expected_node_ids
    )
    required_root_edges = {
        ("SEMANTIC_SCHEMA_HANDOFF", "PHYSICAL_SCHEMA_SECTOR"),
    }
    for group_id, group_rows in table_groups.items():
        required_root_edges.add(("PHYSICAL_SCHEMA_SECTOR", group_id))
        previous_node = group_id
        for row in group_rows:
            table_node = table_nodes[str(row["table"])]
            required_root_edges.add((previous_node, table_node))
            previous_node = table_node
    missing_root_edges = sorted(required_root_edges - graph_edges)
    expected_relation_edges = {
        (
            table_nodes[str(relation["parent_table"])],
            table_nodes[str(relation["child_table"])],
        )
        for relation in projection["relations"]
        if str(relation["parent_table"]) in table_nodes
        and str(relation["child_table"]) in table_nodes
    }
    missing_relation_edges = sorted(expected_relation_edges - graph_edges)
    head_mismatches: list[str] = []
    label_mismatches: list[str] = []
    for row in projection["tables"]:
        table = str(row["table"])
        node_id = table_nodes[table]
        node = nodes.get(node_id)
        if node is None:
            continue
        if node.head != table:
            head_mismatches.append(f"{node_id}:{node.head!r}!={table!r}")
        required_label_claims = (
            f"rows={row['rows'] if row['rows'] is not None else 'derived'}",
            f'columns={len(row["columns"])}',
            f'role={row["role"]}',
        )
        missing_claims = [
            claim for claim in required_label_claims if claim not in node.normalized_label
        ]
        if missing_claims:
            label_mismatches.append(f'{node_id}:missing={"|".join(missing_claims)}')

    expected_projection_sha256 = str(projection["projection_sha256"])
    sector_node = nodes.get("PHYSICAL_SCHEMA_SECTOR")
    projection_match = (
        _PROJECTION_SHA_CLAIM.search(sector_node.normalized_label)
        if sector_node is not None
        else None
    )
    claimed_projection_sha256 = (
        projection_match.group(1) if projection_match is not None else None
    )
    projection_matches = claimed_projection_sha256 == expected_projection_sha256
    missing_contract_tables = list(projection["missing_contract_tables"])
    valid = not (
        missing_subgraphs
        or missing_nodes
        or duplicate_nodes
        or unexpected_physical_nodes
        or missing_root_edges
        or missing_relation_edges
        or head_mismatches
        or label_mismatches
        or missing_contract_tables
        or not projection_matches
    )
    return {
        "applicable": True,
        "required_root": "SEMANTIC_SCHEMA_HANDOFF -> PHYSICAL_SCHEMA_SECTOR",
        "required_subgraph": "SQLITE_PHYSICAL_SCHEMA",
        "required_contract_tables": list(lane.schema_contract),
        "physical_tables": [row["table"] for row in projection["tables"]],
        "auxiliary_tables": list(projection["auxiliary_tables"]),
        "missing_subgraphs": missing_subgraphs,
        "missing_nodes": missing_nodes,
        "duplicate_nodes": duplicate_nodes,
        "unexpected_physical_nodes": unexpected_physical_nodes,
        "missing_root_edges": [list(edge) for edge in missing_root_edges],
        "missing_relation_edges": [list(edge) for edge in missing_relation_edges],
        "head_mismatches": sorted(head_mismatches),
        "label_mismatches": sorted(label_mismatches),
        "missing_contract_tables": missing_contract_tables,
        "expected_projection_sha256": expected_projection_sha256,
        "claimed_projection_sha256": claimed_projection_sha256,
        "status": "PASS" if valid else "FAIL",
    }


def schema_derived_contract_report(
    graph: ParsedGraph,
    *,
    lane_id: str,
    database_path: str | Path,
) -> dict[str, Any]:
    """Require each non-code lane's exact SQLite entity/relation/sample graph."""

    if lane_id in PRIMARY_CODE_LANES or lane_id not in LANE_REGISTRY:
        return {
            "applicable": False,
            "required_subgraph": None,
            "missing_subgraphs": [],
            "missing_nodes": [],
            "missing_root_edges": [],
            "missing_relation_edges": [],
            "missing_samples": [],
            "head_mismatches": [],
            "status": "NOT_APPLICABLE",
        }
    lane = LANE_REGISTRY[lane_id]
    tables = [
        table
        for table in lane.schema_contract
        if table not in CORE_SCHEMA_TABLES and table != lane.fts_table
    ]
    table_nodes = {
        table: f"SCHEMA_ENTITY_{index}" for index, table in enumerate(tables)
    }
    subgraphs = {
        item.removeprefix("cluster_").upper() for item in graph.subgraphs
    }
    nodes = {node.node_id: node for node in graph.nodes}
    required_nodes = {"SCHEMA_SECTOR", *table_nodes.values()}
    required_root_edges = {
        ("PHYSICAL_SCHEMA_SECTOR", "SCHEMA_SECTOR"),
        *(("SCHEMA_SECTOR", node_id) for node_id in table_nodes.values()),
    }
    expected_relation_edges: set[tuple[str, str]] = set()
    expected_sample_nodes: set[str] = set()
    connection = _connect(Path(database_path))
    try:
        for index, table in enumerate(tables):
            if (_count(connection, table) or 0) > 0:
                expected_sample_nodes.add(f"SCHEMA_SAMPLE_{index}")
            quoted = table.replace('"', '""')
            for row in connection.execute(
                f'PRAGMA foreign_key_list("{quoted}")'  # nosec B608
            ):
                parent_table = str(row["table"])
                parent_node = (
                    "SOURCE_REG"
                    if parent_table == "source_registry"
                    else table_nodes.get(parent_table)
                )
                if parent_node:
                    expected_relation_edges.add((parent_node, table_nodes[table]))
    finally:
        connection.close()
    missing_subgraphs = (
        []
        if "SCHEMA_DERIVED_TOPOLOGY" in subgraphs
        else ["SCHEMA_DERIVED_TOPOLOGY"]
    )
    missing_nodes = sorted(required_nodes - nodes.keys())
    graph_edges = set(graph.edges)
    missing_root_edges = sorted(required_root_edges - graph_edges)
    missing_relation_edges = sorted(expected_relation_edges - graph_edges)
    missing_samples = sorted(expected_sample_nodes - nodes.keys())
    missing_sample_edges = sorted(
        (table_nodes[tables[index]], sample_node)
        for sample_node in expected_sample_nodes
        for index in [int(sample_node.rsplit("_", 1)[1])]
        if (table_nodes[tables[index]], sample_node) not in graph_edges
    )
    head_mismatches = sorted(
        f"{node_id}:{nodes[node_id].head!r}!={table!r}"
        for table, node_id in table_nodes.items()
        if node_id in nodes and nodes[node_id].head != table
    )
    valid = not (
        missing_subgraphs
        or missing_nodes
        or missing_root_edges
        or missing_relation_edges
        or missing_samples
        or missing_sample_edges
        or head_mismatches
    )
    return {
        "applicable": True,
        "required_root": "SCHEMA_SECTOR",
        "required_subgraph": "SCHEMA_DERIVED_TOPOLOGY",
        "required_entities": tables,
        "missing_subgraphs": missing_subgraphs,
        "missing_nodes": missing_nodes,
        "missing_root_edges": [list(edge) for edge in missing_root_edges],
        "missing_relation_edges": [list(edge) for edge in missing_relation_edges],
        "missing_samples": missing_samples,
        "missing_sample_edges": [list(edge) for edge in missing_sample_edges],
        "head_mismatches": head_mismatches,
        "status": "PASS" if valid else "FAIL",
    }


def _rendering_parity(mermaid: ParsedGraph, dot: ParsedGraph) -> dict[str, Any]:
    mmd_subgraphs = {item.upper() for item in mermaid.subgraphs}
    dot_subgraphs = {item.removeprefix("cluster_").upper() for item in dot.subgraphs}
    mismatches = {
        "subgraphs": sorted(mmd_subgraphs ^ dot_subgraphs),
        "nodes": sorted(mermaid.node_ids ^ dot.node_ids),
        "edges": sorted(set(mermaid.edges) ^ set(dot.edges)),
    }
    mismatches = {name: rows for name, rows in mismatches.items() if rows}
    return {
        "mermaid": mermaid.counts(),
        "dot": dot.counts(),
        "identity_mismatches": mismatches,
        "status": "PASS" if not mismatches else "FAIL",
    }


def reconcile_lane_topology(
    lane_directory: str | Path,
    *,
    lane_id: str,
    mmd_filename: str,
    dot_filename: str,
    sqlite_filename: str,
) -> dict[str, Any]:
    root = Path(lane_directory).resolve()
    mermaid = parse_mermaid((root / mmd_filename).read_text(encoding="utf-8"))
    dot = parse_dot((root / dot_filename).read_text(encoding="utf-8"))
    mermaid_structural = structural_report(mermaid)
    dot_structural = structural_report(dot)
    logical_table_projection = (
        _CODE_LOGICAL_TABLES if lane_id in PRIMARY_CODE_LANES else None
    )
    mermaid_claims = reconcile_graph_against_database(
        mermaid,
        root / sqlite_filename,
        logical_table_projection=logical_table_projection,
    )
    dot_claims = reconcile_graph_against_database(
        dot,
        root / sqlite_filename,
        logical_table_projection=logical_table_projection,
    )
    failed_claims = [
        {"rendering": rendering, **claim}
        for rendering, claims in (("mermaid", mermaid_claims), ("dot", dot_claims))
        for claim in claims
        if claim["status"] == "FAIL"
    ]
    parity = _rendering_parity(mermaid, dot)
    logical_contract = {
        "mermaid": logical_code_contract_report(mermaid, lane_id=lane_id),
        "dot": logical_code_contract_report(dot, lane_id=lane_id),
    }
    logical_contract_status = (
        "PASS"
        if all(
            report["status"] in {"PASS", "NOT_APPLICABLE"}
            for report in logical_contract.values()
        )
        else "FAIL"
    )
    physical_contract = {
        "mermaid": physical_schema_contract_report(
            mermaid,
            lane_id=lane_id,
            database_path=root / sqlite_filename,
        ),
        "dot": physical_schema_contract_report(
            dot,
            lane_id=lane_id,
            database_path=root / sqlite_filename,
        ),
    }
    physical_contract_status = (
        "PASS"
        if all(
            report["status"] in {"PASS", "NOT_APPLICABLE"}
            for report in physical_contract.values()
        )
        else "FAIL"
    )
    schema_contract = {
        "mermaid": schema_derived_contract_report(
            mermaid,
            lane_id=lane_id,
            database_path=root / sqlite_filename,
        ),
        "dot": schema_derived_contract_report(
            dot,
            lane_id=lane_id,
            database_path=root / sqlite_filename,
        ),
    }
    schema_contract_status = (
        "PASS"
        if all(
            report["status"] in {"PASS", "NOT_APPLICABLE"}
            for report in schema_contract.values()
        )
        else "FAIL"
    )
    valid = (
        mermaid_structural["status"] == "PASS"
        and dot_structural["status"] == "PASS"
        and parity["status"] == "PASS"
        and bool(mermaid_claims)
        and bool(dot_claims)
        and not failed_claims
        and logical_contract_status == "PASS"
        and physical_contract_status == "PASS"
        and schema_contract_status == "PASS"
    )
    return {
        "schema": RECONCILIATION_SCHEMA,
        "lane_id": lane_id,
        "structural": {
            "mermaid": mermaid_structural,
            "dot": dot_structural,
        },
        "claims_checked": len(mermaid_claims) + len(dot_claims),
        "claims_failed": len(failed_claims),
        "failed_claims": failed_claims,
        "logical_code_contract": {
            **logical_contract,
            "status": logical_contract_status,
        },
        "physical_schema_contract": {
            **physical_contract,
            "status": physical_contract_status,
        },
        "schema_derived_contract": {
            **schema_contract,
            "status": schema_contract_status,
        },
        "rendering_parity": parity,
        "status": "PASS" if valid else "FAIL",
    }


def reconcile_bundle_topology(
    bundle_directory: str | Path,
    *,
    lane_ids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY

    root = Path(bundle_directory).resolve()
    selected_lane_ids = tuple(lane_ids or CANONICAL_LANE_IDS)
    lanes = [
        reconcile_lane_topology(
            root / lane_id,
            lane_id=lane_id,
            mmd_filename=LANE_REGISTRY[lane_id].mmd_filename,
            dot_filename=LANE_REGISTRY[lane_id].dot_filename,
            sqlite_filename=LANE_REGISTRY[lane_id].sqlite_filename,
        )
        for lane_id in selected_lane_ids
    ]
    project_mermaid = parse_mermaid(
        (root / "project_lane_topology.mmd").read_text(encoding="utf-8")
    )
    project_dot = parse_dot(
        (root / "project_lane_topology.dot").read_text(encoding="utf-8")
    )
    project: dict[str, Any] = {
        "structural": {
            "mermaid": structural_report(project_mermaid),
            "dot": structural_report(project_dot),
        },
        "rendering_parity": _rendering_parity(project_mermaid, project_dot),
    }
    project["status"] = (
        "PASS"
        if project["structural"]["mermaid"]["status"] == "PASS"
        and project["structural"]["dot"]["status"] == "PASS"
        and project["rendering_parity"]["status"] == "PASS"
        else "FAIL"
    )
    failed = [lane["lane_id"] for lane in lanes if lane["status"] == "FAIL"]
    valid = not failed and project["status"] == "PASS"
    return {
        "schema": RECONCILIATION_SCHEMA,
        "bundle": str(root),
        "lane_count": len(lanes),
        "lanes": lanes,
        "failed_lanes": failed,
        "failed_lane_count": len(failed),
        "claims_checked": sum(lane["claims_checked"] for lane in lanes),
        "project_topology": project,
        "status": "PASS" if valid else "FAIL",
    }


def reconciliation_markdown(report: dict[str, Any]) -> str:
    rows = "\n".join(
        f"| `{lane['lane_id']}` | "
        f"{lane['structural']['mermaid']['counts']['subgraphs']} | "
        f"{lane['structural']['mermaid']['counts']['nodes']} | "
        f"{lane['structural']['mermaid']['counts']['edges']} | "
        f"{lane['claims_checked']} | {lane['claims_failed']} | {lane['status']} |"
        for lane in report["lanes"]
    )
    return f"""# SQLite to Mermaid and DOT reconciliation

Bundle: `{report['bundle']}`
Lanes: {report['lane_count']} - failed: {report['failed_lane_count']}
Claims checked across both renderings: {report['claims_checked']}
Project topology: **{report['project_topology']['status']}**
Status: **{report['status']}**

| Lane | Subgraphs | Nodes | Edges | Claims | Failed | Status |
|---|---:|---:|---:|---:|---:|---|
{rows}
"""


__all__ = [
    "MIN_EDGES",
    "MIN_NODES",
    "MIN_SUBGRAPHS",
    "RECONCILIATION_SCHEMA",
    "GraphNode",
    "ParsedGraph",
    "logical_code_contract_report",
    "parse_dot",
    "parse_mermaid",
    "reconcile_bundle_topology",
    "reconcile_graph_against_database",
    "reconcile_lane_topology",
    "reconciliation_markdown",
    "schema_derived_contract_report",
    "structural_report",
]

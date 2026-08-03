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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RECONCILIATION_SCHEMA = "evidence-lane.topology-reconciliation.v2"
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
    r'^\s*(?P<id>[A-Za-z0-9_]+)\s*\[label="(?P<label>.*?)",'
)
_DOT_EDGE = re.compile(
    r'^\s*(?P<src>[A-Za-z0-9_]+)\s*->\s*(?P<dst>[A-Za-z0-9_]+)\s*'
    r'(?:\[label="(?P<label>.*?)"\])?\s*;\s*$'
)
_ROWS_CLAIM = re.compile(r"\brows=(\d+)")
_SOURCES_CLAIM = re.compile(r"\b(\d+)\s+sources\b")
_CHUNKS_CLAIM = re.compile(r"\b(\d+)\s+chunks\b")
_FACTS_CLAIM = re.compile(r"\b(\d+)\s+structured facts\b")
_KINDS_CLAIM = re.compile(r"\bkinds=(\d+)")
_SAFE_TABLE = re.compile(r"^[a-z][a-z0-9_]*$")


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
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
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
        if not stripped:
            continue
        if stripped.startswith("digraph ") and stripped.endswith("{"):
            depth += 1
            continue
        if stripped.startswith(("rankdir", "graph [", "node [", "edge [")):
            continue
        match = _DOT_CLUSTER.match(line)
        if match:
            graph.subgraphs.append(match.group("id"))
            depth += 1
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
            graph.edges.append((match.group("src"), match.group("dst")))
            continue
        match = _DOT_NODE.match(line)
        if match:
            graph.nodes.append(GraphNode(match.group("id"), match.group("label")))
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
    graph: ParsedGraph, database_path: str | Path
) -> list[dict[str, Any]]:
    connection = _connect(Path(database_path))
    try:
        tables = _table_names(connection)
        kind_counts = _fact_kind_counts(connection)
        sources = _count(connection, "source_registry")
        chunks = _count(connection, "chunk_index")
        facts = _count(connection, "structured_fact")
        claims: list[dict[str, Any]] = []
        for node in graph.nodes:
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
                if head in tables:
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
    mermaid_claims = reconcile_graph_against_database(
        mermaid, root / sqlite_filename
    )
    dot_claims = reconcile_graph_against_database(dot, root / sqlite_filename)
    failed_claims = [
        {"rendering": rendering, **claim}
        for rendering, claims in (("mermaid", mermaid_claims), ("dot", dot_claims))
        for claim in claims
        if claim["status"] == "FAIL"
    ]
    parity = _rendering_parity(mermaid, dot)
    valid = (
        mermaid_structural["status"] == "PASS"
        and dot_structural["status"] == "PASS"
        and parity["status"] == "PASS"
        and bool(mermaid_claims)
        and bool(dot_claims)
        and not failed_claims
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
        "rendering_parity": parity,
        "status": "PASS" if valid else "FAIL",
    }


def reconcile_bundle_topology(bundle_directory: str | Path) -> dict[str, Any]:
    from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY

    root = Path(bundle_directory).resolve()
    lanes = [
        reconcile_lane_topology(
            root / lane_id,
            lane_id=lane_id,
            mmd_filename=LANE_REGISTRY[lane_id].mmd_filename,
            dot_filename=LANE_REGISTRY[lane_id].dot_filename,
            sqlite_filename=LANE_REGISTRY[lane_id].sqlite_filename,
        )
        for lane_id in CANONICAL_LANE_IDS
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
    "parse_dot",
    "parse_mermaid",
    "reconcile_bundle_topology",
    "reconcile_graph_against_database",
    "reconcile_lane_topology",
    "reconciliation_markdown",
    "structural_report",
]

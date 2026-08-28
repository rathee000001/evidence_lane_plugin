"""System-wide semantic graph generation through LangGraph and Graphviz.

Every Evidence Lane topology is represented once as ordered semantic nodes,
edges, and optional groups. Mermaid is emitted only after a LangGraph or
LangGraph-compatible graph export proves the same topology. DOT is constructed
through the Python Graphviz package. The resulting MMD and DOT remain traversal
maps into their owning SQLite authority; neither format becomes a database or
an independent lifecycle authority.
"""

from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import asdict, dataclass
from importlib.metadata import version
from typing import Any, Literal, TypedDict

from graphviz import Digraph, Source
from langchain_core.runnables.graph import Edge as LangChainEdge
from langchain_core.runnables.graph import Graph as LangChainGraph
from langchain_core.runnables.graph import Node as LangChainNode
from langgraph.graph import END, START, StateGraph

from .hashing import canonical_json_bytes, sha256_bytes
from .native_toolchain import (
    configured_runtime_root,
    try_resolve_native_tool,
    validate_dot_source,
)

GraphRole = Literal["AUTHORITY_TRAVERSAL", "EXECUTABLE_WORKFLOW"]

GRAPH_PIPELINE_SCHEMA = "evidence-lane.semantic-graph-pipeline.v1"
LANGGRAPH_VERSION = version("langgraph")
LANGCHAIN_CORE_VERSION = version("langchain-core")
PYTHON_GRAPHVIZ_VERSION = version("graphviz")

_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MMD_IMPORT_SUBGRAPH = re.compile(
    r'^\s*subgraph\s+(?P<id>[A-Za-z_][A-Za-z0-9_]*)'
    r'(?:\["?(?P<label>.*?)"?\])?\s*$'
)
_MMD_IMPORT_EDGE = re.compile(
    r"^\s*(?P<left>.+?)\s*(?:"
    r"--\s*(?P<label>.*?)\s*-->|"
    r"(?P<plain>-->)|"
    r"-\.\s*(?P<dashed>.*?)\s*\.->"
    r")\s*(?P<right>.+?)\s*$"
)
_MMD_IMPORT_NODE = re.compile(
    r'^\s*(?P<id>[A-Za-z_][A-Za-z0-9_]*)'
    r'(?P<shape>\(\[.*?\]\)|\[\(.*?\)\]|\{\{.*?\}\}|\{.*?\}|\[.*?\])?'
    r'(?:::[A-Za-z_][A-Za-z0-9_]*)?\s*$'
)
_KIND_STYLES: dict[str, dict[str, str]] = {
    "root": {
        "fillcolor": "#101828",
        "fontcolor": "white",
        "color": "#101828",
    },
    "source": {"fillcolor": "#edf5ff", "color": "#125cdd"},
    "semantic": {"fillcolor": "#f0ebff", "color": "#7147c7"},
    "retrieval": {"fillcolor": "#eaf8f1", "color": "#24805c"},
    "git": {"fillcolor": "#fff7e7", "color": "#c88722"},
    "lifecycle": {"fillcolor": "#fff1f0", "color": "#ba4236"},
    "output": {"fillcolor": "#f7f9fc", "color": "#667085"},
    "warn": {"fillcolor": "#fff7e7", "color": "#c88722"},
    "table": {"fillcolor": "#f0ebff", "color": "#7147c7"},
    "column": {"fillcolor": "#f7f9fc", "color": "#667085"},
    "default": {"fillcolor": "#f7f9fc", "color": "#667085"},
}
_MMD_CLASS_DEFS = {
    "root": "fill:#101828,stroke:#101828,color:#fff,stroke-width:2px",
    "source": "fill:#edf5ff,stroke:#125cdd,color:#101828",
    "semantic": "fill:#f0ebff,stroke:#7147c7,color:#101828",
    "retrieval": "fill:#eaf8f1,stroke:#24805c,color:#101828",
    "git": "fill:#fff7e7,stroke:#c88722,color:#101828",
    "lifecycle": "fill:#fff1f0,stroke:#ba4236,color:#101828",
    "output": "fill:#f7f9fc,stroke:#667085,color:#101828",
    "warn": "fill:#fff7e7,stroke:#c88722,color:#101828",
    "table": "fill:#f0ebff,stroke:#7147c7,color:#101828",
    "column": "fill:#f7f9fc,stroke:#667085,color:#101828",
    "default": "fill:#f7f9fc,stroke:#667085,color:#101828",
}


class _WorkflowState(TypedDict, total=False):
    graph_identity: str


@dataclass(frozen=True, slots=True)
class SemanticGroup:
    group_id: str
    label: str
    direction: str = "TB"


@dataclass(frozen=True, slots=True)
class SemanticNode:
    node_id: str
    label: str
    kind: str = "default"
    group_id: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticEdge:
    source: str
    target: str
    label: str | None = None
    conditional: bool = False


def _safe_id(value: str, *, role: str) -> str:
    exact = str(value).strip()
    if not _SAFE_ID.fullmatch(exact):
        raise ValueError(f"Unsafe {role} graph identity: {value!r}")
    return exact


def _text(value: Any, *, limit: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def _label_text(value: Any, *, limit: int = 240) -> str:
    lines = [_text(line, limit=limit) for line in str(value or "").splitlines()]
    normalized = "\n".join(line for line in lines if line)
    return normalized or _text(value, limit=limit)


def _mmd_label(value: Any) -> str:
    return (
        _text(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
    )


def _dot_label(value: Any) -> str:
    return "\\n".join(
        _text(line).replace("\\", "/").replace('"', "'")
        for line in str(value or "").splitlines()
    )


def _workflow_node(state: _WorkflowState) -> _WorkflowState:
    return state


def _rustworkx_analysis(
    nodes: list[SemanticNode], edges: list[SemanticEdge]
) -> dict[str, Any]:
    """Run bounded graph analytics without making the graph engine authority."""

    if importlib.util.find_spec("rustworkx") is None:
        return {
            "status": "UNAVAILABLE",
            "engine": "rustworkx",
            "authority_replaced": False,
        }
    import rustworkx as rx  # type: ignore[import-not-found]

    graph = rx.PyDiGraph()
    indices = {node.node_id: graph.add_node(node.node_id) for node in nodes}
    for edge in edges:
        graph.add_edge(indices[edge.source], indices[edge.target], edge.label)
    weak_components = list(rx.weakly_connected_components(graph))
    strong_components = list(rx.strongly_connected_components(graph))
    is_dag = bool(rx.is_directed_acyclic_graph(graph))
    degree_rows: list[dict[str, str | int]] = [
        {
            "node_id": str(graph[index]),
            "in_degree": int(graph.in_degree(index)),
            "out_degree": int(graph.out_degree(index)),
        }
        for index in graph.node_indices()
    ]
    degree_rows.sort(
        key=lambda row: (
            -(int(row["in_degree"]) + int(row["out_degree"])),
            row["node_id"],
        )
    )
    return {
        "status": "PASS",
        "engine": "rustworkx",
        "version": str(rx.__version__),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "weak_component_count": len(weak_components),
        "strong_component_count": len(strong_components),
        "is_directed_acyclic": is_dag,
        "highest_degree_nodes": degree_rows[:20],
        "bounded_analysis": True,
        "authority_replaced": False,
    }


def _import_node(value: str) -> tuple[str, str | None, str]:
    match = _MMD_IMPORT_NODE.match(value.strip().rstrip(";"))
    if match is None:
        raise ValueError(f"Unsupported Mermaid node expression: {value!r}")
    node_id = match.group("id")
    shape = match.group("shape")
    if shape is None:
        return node_id, None, "default"
    label = shape
    kind = "semantic"
    if shape.startswith("(["):
        label = shape[2:-2]
        kind = "root"
    elif shape.startswith("[("):
        label = shape[2:-2]
        kind = "retrieval"
    elif shape.startswith("{{"):
        label = shape[2:-2]
        kind = "warn"
    elif shape.startswith("{"):
        label = shape[1:-1]
        kind = "warn"
    elif shape.startswith("["):
        label = shape[1:-1]
    label = label.strip().strip('"').replace("<br/>", "\n")
    return node_id, label, kind


def semantic_graph_from_mermaid(
    source: str,
    *,
    name: str,
    role: GraphRole = "EXECUTABLE_WORKFLOW",
) -> SemanticGraph:
    """Ingest one existing Mermaid topology without silently dropping lines."""

    direction = "TB"
    groups: list[SemanticGroup] = []
    group_ids: set[str] = set()
    current_group: str | None = None
    node_rows: dict[str, tuple[str, str, str | None]] = {}
    node_order: list[str] = []
    edges: list[SemanticEdge] = []
    unsupported: list[str] = []
    for number, raw in enumerate(source.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("%%", "---", "title:")):
            continue
        if stripped.startswith(("flowchart ", "graph ")):
            direction = stripped.split()[1].rstrip(";")
            continue
        if stripped.startswith(("classDef ", "class ", "style ", "linkStyle ")):
            continue
        group_match = _MMD_IMPORT_SUBGRAPH.match(raw)
        if group_match:
            group_id = group_match.group("id")
            if group_id in group_ids:
                raise ValueError(f"Duplicate Mermaid subgraph: {group_id}")
            groups.append(
                SemanticGroup(
                    group_id,
                    (group_match.group("label") or group_id).strip('"'),
                    "TB",
                )
            )
            group_ids.add(group_id)
            current_group = group_id
            continue
        if stripped.startswith("direction "):
            if current_group is not None:
                prior = groups[-1]
                groups[-1] = SemanticGroup(
                    prior.group_id,
                    prior.label,
                    stripped.split()[1].rstrip(";"),
                )
            continue
        if stripped == "end":
            current_group = None
            continue
        edge_match = _MMD_IMPORT_EDGE.match(raw)
        if edge_match:
            try:
                left_id, left_label, left_kind = _import_node(
                    edge_match.group("left")
                )
                right_id, right_label, right_kind = _import_node(
                    edge_match.group("right")
                )
            except ValueError:
                unsupported.append(f"line {number}: {stripped}")
                continue
            for node_id, label, kind in (
                (left_id, left_label, left_kind),
                (right_id, right_label, right_kind),
            ):
                if node_id not in node_rows:
                    node_order.append(node_id)
                    node_rows[node_id] = (
                        label or node_id,
                        kind,
                        current_group if label is not None else None,
                    )
                elif label is not None:
                    prior_label, prior_kind, prior_group = node_rows[node_id]
                    node_rows[node_id] = (
                        label or prior_label,
                        kind if kind != "default" else prior_kind,
                        prior_group or current_group,
                    )
            label = edge_match.group("label") or edge_match.group("dashed")
            edges.append(
                SemanticEdge(
                    left_id,
                    right_id,
                    label.strip().strip('"') if label else None,
                    edge_match.group("dashed") is not None,
                )
            )
            continue
        try:
            node_id, label, kind = _import_node(raw)
        except ValueError:
            unsupported.append(f"line {number}: {stripped}")
            continue
        if node_id not in node_rows:
            node_order.append(node_id)
        prior_node = node_rows.get(node_id)
        node_rows[node_id] = (
            label or (prior_node[0] if prior_node else node_id),
            kind if kind != "default" or prior_node is None else prior_node[1],
            (prior_node[2] if prior_node else None) or current_group,
        )
    if unsupported:
        raise ValueError(
            "Unsupported Mermaid statements: " + "; ".join(unsupported[:20])
        )
    referenced_groups = {
        endpoint
        for edge in edges
        for endpoint in (edge.source, edge.target)
        if endpoint in group_ids and endpoint not in node_rows
    }
    edge_group_map = {group_id: f"{group_id}_ANCHOR" for group_id in referenced_groups}
    graph = SemanticGraph(name, direction=direction, role=role)
    for node_id in node_order:
        label, kind, group_id = node_rows[node_id]
        if group_id is None:
            graph.add_node(node_id, label, kind)
    for group in groups:
        graph.begin_group(
            group.group_id,
            group.label,
            direction=group.direction,
        )
        if group.group_id in edge_group_map:
            graph.add_node(
                edge_group_map[group.group_id],
                group.label,
                "semantic",
            )
        for node_id in node_order:
            label, kind, group_id = node_rows[node_id]
            if group_id == group.group_id:
                graph.add_node(node_id, label, kind)
        graph.end_group()
    for edge in edges:
        graph.add_edge(
            edge_group_map.get(edge.source, edge.source),
            edge_group_map.get(edge.target, edge.target),
            edge.label,
            conditional=edge.conditional,
        )
    return graph


class SemanticGraph:
    """One ordered semantic topology rendered by both graph engines."""

    def __init__(
        self,
        name: str,
        *,
        direction: str = "TB",
        role: GraphRole = "AUTHORITY_TRAVERSAL",
    ) -> None:
        self.name = _safe_id(name, role="graph")
        self.direction = direction if direction in {"TB", "TD", "BT", "LR", "RL"} else "TB"
        self.role = role
        self.groups: list[SemanticGroup] = []
        self.nodes: list[SemanticNode] = []
        self.edges: list[SemanticEdge] = []
        self._current_group: str | None = None

    def begin_group(
        self, group_id: str, label: str, *, direction: str = "TB"
    ) -> None:
        if self._current_group is not None:
            raise ValueError("Nested semantic graph groups are not supported.")
        exact = _safe_id(group_id, role="group")
        if any(group.group_id == exact for group in self.groups):
            raise ValueError(f"Duplicate semantic graph group: {exact}")
        self.groups.append(SemanticGroup(exact, _label_text(label), direction))
        self._current_group = exact

    def end_group(self) -> None:
        if self._current_group is None:
            raise ValueError("No semantic graph group is open.")
        self._current_group = None

    def add_node(self, node_id: str, label: str, kind: str = "default") -> None:
        exact = _safe_id(node_id, role="node")
        if any(node.node_id == exact for node in self.nodes):
            raise ValueError(f"Duplicate semantic graph node: {exact}")
        exact_kind = kind if kind in _KIND_STYLES else "default"
        self.nodes.append(
            SemanticNode(exact, _label_text(label), exact_kind, self._current_group)
        )

    def add_edge(
        self,
        source: str,
        target: str,
        label: str | None = None,
        *,
        conditional: bool = False,
    ) -> None:
        self.edges.append(
            SemanticEdge(
                _safe_id(source, role="edge source"),
                _safe_id(target, role="edge target"),
                _text(label) if label else None,
                bool(conditional),
            )
        )

    def _validate_topology(self) -> None:
        if self._current_group is not None:
            raise ValueError(f"Semantic graph group remains open: {self._current_group}")
        node_ids = {node.node_id for node in self.nodes}
        if not node_ids:
            raise ValueError("A semantic graph requires at least one node.")
        missing = sorted(
            {
                endpoint
                for edge in self.edges
                for endpoint in (edge.source, edge.target)
                if endpoint not in node_ids
            }
        )
        if missing:
            raise ValueError(f"Semantic graph edges reference missing nodes: {missing}")

    def _langchain_graph(self) -> LangChainGraph:
        nodes = {
            node.node_id: LangChainNode(
                node.node_id,
                node.label,
                None,
                None,
            )
            for node in self.nodes
        }
        edges = [
            LangChainEdge(edge.source, edge.target, edge.label, edge.conditional)
            for edge in self.edges
        ]
        return LangChainGraph(nodes=nodes, edges=edges)

    def _langgraph_workflow_export(self) -> tuple[str, dict[str, Any]]:
        builder = StateGraph(_WorkflowState)
        for node in self.nodes:
            builder.add_node(node.node_id, _workflow_node)
        incoming = {edge.target for edge in self.edges}
        outgoing = {edge.source for edge in self.edges}
        roots = [node.node_id for node in self.nodes if node.node_id not in incoming]
        sinks = [node.node_id for node in self.nodes if node.node_id not in outgoing]
        if not roots:
            roots = [self.nodes[0].node_id]
        for node_id in roots:
            builder.add_edge(START, node_id)
        for edge in self.edges:
            builder.add_edge(edge.source, edge.target)
        for node_id in sinks:
            builder.add_edge(node_id, END)
        compiled = builder.compile()
        graph = compiled.get_graph()
        return graph.draw_mermaid(with_styles=True), graph.to_json()

    def _langgraph_export(self) -> tuple[str, dict[str, Any], str]:
        if self.role == "EXECUTABLE_WORKFLOW":
            try:
                source, projection = self._langgraph_workflow_export()
                return source, projection, "LANGGRAPH_STATEGRAPH"
            except ValueError:
                # Cyclic/static consequence graphs can lack a valid executable
                # entry/exit shape. They still use the exact graph exporter that
                # LangGraph exposes through compiled ``get_graph()``.
                pass
        graph = self._langchain_graph()
        return (
            graph.draw_mermaid(with_styles=True),
            graph.to_json(),
            "LANGGRAPH_GRAPH_EXPORTER",
        )

    def render_mermaid(self) -> tuple[str, dict[str, Any]]:
        self._validate_topology()
        exported, projection, exporter = self._langgraph_export()
        projected_nodes = {
            str(row["id"])
            for row in projection.get("nodes", [])
            if str(row.get("id")) not in {"__start__", "__end__"}
        }
        expected_nodes = {node.node_id for node in self.nodes}
        if projected_nodes != expected_nodes:
            raise ValueError("LangGraph node projection does not match semantic graph.")
        projected_edges = {
            (str(row["source"]), str(row["target"]))
            for row in projection.get("edges", [])
            if str(row.get("source")) not in {"__start__", "__end__"}
            and str(row.get("target")) not in {"__start__", "__end__"}
        }
        expected_edges = {(edge.source, edge.target) for edge in self.edges}
        if not expected_edges.issubset(projected_edges):
            raise ValueError("LangGraph edge projection does not match semantic graph.")
        export_sha256 = sha256_bytes(exported.encode("utf-8"))
        lines = [
            f"flowchart {self.direction}",
            f"%% EVIDENCE_LANE_GRAPH_ENGINE={exporter}",
            f"%% LANGGRAPH_VERSION={LANGGRAPH_VERSION}",
            f"%% LANGGRAPH_EXPORT_SHA256={export_sha256}",
        ]
        for kind, style in _MMD_CLASS_DEFS.items():
            lines.append(f"  classDef {kind} {style};")
        grouped: dict[str, list[SemanticNode]] = {
            group.group_id: [] for group in self.groups
        }
        ungrouped: list[SemanticNode] = []
        for node in self.nodes:
            if node.group_id is None:
                ungrouped.append(node)
            else:
                grouped[node.group_id].append(node)

        def append_node(node: SemanticNode, indent: str) -> None:
            label = "<br/>".join(
                _mmd_label(part) for part in node.label.splitlines()
            )
            lines.append(f'{indent}{node.node_id}["{label}"]:::{node.kind}')

        for node in ungrouped:
            append_node(node, "  ")
        for group in self.groups:
            lines.append(f'  subgraph {group.group_id}["{_mmd_label(group.label)}"]')
            lines.append(f"    direction {group.direction}")
            for node in grouped[group.group_id]:
                append_node(node, "    ")
            lines.append("  end")
        for edge in self.edges:
            if edge.label:
                prefix = "conditional: " if edge.conditional else ""
                lines.append(
                    f'  {edge.source} -->|"{_mmd_label(prefix + edge.label)}"| '
                    f"{edge.target}"
                )
            else:
                lines.append(f"  {edge.source} --> {edge.target}")
        source = "\n".join(lines) + "\n"
        receipt = self._receipt(
            mmd_sha256=sha256_bytes(source.encode("utf-8")),
            dot_sha256=None,
            langgraph_export_sha256=export_sha256,
            mermaid_exporter=exporter,
        )
        return source, receipt

    def render_dot(self) -> tuple[str, dict[str, Any]]:
        self._validate_topology()
        graph = Digraph(self.name, strict=False)
        graph.attr(rankdir=self.direction, fontname="Arial", bgcolor="white")
        graph.attr(
            "node",
            shape="box",
            style="rounded,filled",
            fontname="Arial",
            color="#667085",
        )
        graph.attr("edge", fontname="Arial", color="#667085")
        grouped: dict[str, list[SemanticNode]] = {
            group.group_id: [] for group in self.groups
        }
        ungrouped: list[SemanticNode] = []
        for node in self.nodes:
            (ungrouped if node.group_id is None else grouped[node.group_id]).append(
                node
            )

        def add_node(target: Digraph, node: SemanticNode) -> None:
            style = _KIND_STYLES[node.kind]
            target.node(
                node.node_id,
                _dot_label(node.label),
                shape="box",
                style="rounded,filled",
                **style,
            )

        for node in ungrouped:
            add_node(graph, node)
        for group in self.groups:
            with graph.subgraph(name=f"cluster_{group.group_id.lower()}") as subgraph:
                subgraph.attr(label=_dot_label(group.label), rankdir=group.direction)
                for node in grouped[group.group_id]:
                    add_node(subgraph, node)
        for edge in self.edges:
            attributes = {"label": _dot_label(edge.label)} if edge.label else {}
            if edge.conditional:
                attributes["style"] = "dashed"
            graph.edge(edge.source, edge.target, **attributes)
        source = graph.source.replace("\t", "  ")
        source = source.replace(
            "{\n",
            "{\n"
            "  // EVIDENCE_LANE_GRAPH_ENGINE=PYTHON_GRAPHVIZ\n"
            f"  // PYTHON_GRAPHVIZ_VERSION={PYTHON_GRAPHVIZ_VERSION}\n",
            1,
        )
        Source(source)
        native_validation: dict[str, Any] | None = None
        runtime_root = configured_runtime_root()
        host_profile = os.environ.get("EVIDENCE_LANE_HOST_PROFILE", "").strip().upper()
        if (
            runtime_root is not None
            and host_profile in {"CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"}
            and try_resolve_native_tool("graphviz") is not None
        ):
            native_validation = validate_dot_source(
                source,
                runtime_root=runtime_root,
                host_profile=host_profile,
            )
            if native_validation["status"] != "PASS":
                raise ValueError("NATIVE_GRAPHVIZ_DOT_VALIDATION_FAILED")
        receipt = self._receipt(
            mmd_sha256=None,
            dot_sha256=sha256_bytes(source.encode("utf-8")),
            langgraph_export_sha256=None,
            mermaid_exporter=None,
            native_validation=native_validation,
        )
        return source, receipt

    def render_pair(self) -> tuple[str, str, dict[str, Any]]:
        mmd, mmd_receipt = self.render_mermaid()
        dot, dot_receipt = self.render_dot()
        body = {
            **mmd_receipt,
            "dot_sha256": dot_receipt["dot_sha256"],
            "mmd_dot_same_semantic_topology": True,
        }
        body["receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
        return mmd, dot, body

    def _receipt(
        self,
        *,
        mmd_sha256: str | None,
        dot_sha256: str | None,
        langgraph_export_sha256: str | None,
        mermaid_exporter: str | None,
        native_validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        topology = {
            "name": self.name,
            "direction": self.direction,
            "role": self.role,
            "groups": [asdict(group) for group in self.groups],
            "nodes": [asdict(node) for node in self.nodes],
            "edges": [asdict(edge) for edge in self.edges],
        }
        graph_analysis = _rustworkx_analysis(self.nodes, self.edges)
        return {
            "schema": GRAPH_PIPELINE_SCHEMA,
            "status": "PASS",
            "graph": self.name,
            "graph_role": self.role,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "group_count": len(self.groups),
            "semantic_topology_sha256": sha256_bytes(
                canonical_json_bytes(topology)
            ),
            "mmd_sha256": mmd_sha256,
            "dot_sha256": dot_sha256,
            "mermaid_exporter": mermaid_exporter,
            "langgraph_version": LANGGRAPH_VERSION,
            "langchain_core_version": LANGCHAIN_CORE_VERSION,
            "langgraph_export_sha256": langgraph_export_sha256,
            "dot_exporter": "PYTHON_GRAPHVIZ",
            "python_graphviz_version": PYTHON_GRAPHVIZ_VERSION,
            "native_graphviz_dot_available": (
                try_resolve_native_tool("graphviz") is not None
            ),
            "native_graphviz_hidden_runtime_available": (
                try_resolve_native_tool("graphviz") is not None
            ),
            "native_graphviz_validation": native_validation,
            "graph_analysis": graph_analysis,
            "graph_analysis_sha256": sha256_bytes(
                canonical_json_bytes(graph_analysis)
            ),
            "mmd_and_dot_are_sqlite_traversal_maps": True,
            "sqlite_authority_replaced": False,
        }


def graph_engine_status() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.graph-engine-status.v1",
        "status": "PASS",
        "langgraph_version": LANGGRAPH_VERSION,
        "langchain_core_version": LANGCHAIN_CORE_VERSION,
        "python_graphviz_version": PYTHON_GRAPHVIZ_VERSION,
        "native_graphviz_dot": None,
        "native_graphviz_hidden_runtime_available": (
            try_resolve_native_tool("graphviz") is not None
        ),
        "native_graphviz_required_in_installed_runtime": True,
        "mermaid_generation": (
            "LANGGRAPH_STATEGRAPH_OR_LANGGRAPH_GRAPH_EXPORTER"
        ),
        "dot_generation": "PYTHON_GRAPHVIZ",
        "all_mmd_dot_remain_traversal_maps_into_owning_sqlite": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "GRAPH_PIPELINE_SCHEMA",
    "GraphRole",
    "SemanticEdge",
    "SemanticGraph",
    "SemanticGroup",
    "SemanticNode",
    "graph_engine_status",
    "semantic_graph_from_mermaid",
]

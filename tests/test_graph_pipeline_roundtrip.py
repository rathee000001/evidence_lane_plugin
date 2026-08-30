from __future__ import annotations

from evidence_lane_plugin.graph_pipeline import (
    SemanticGraph,
    semantic_graph_from_mermaid,
)


def test_rendered_mermaid_round_trips_classes_labels_and_conditional_edges() -> None:
    graph = SemanticGraph("roundtrip", direction="LR", role="EXECUTABLE_WORKFLOW")
    graph.add_node("START", "Intent & exact identity", "root")
    graph.add_node("ENV", "ENV: context | locality", "semantic")
    graph.add_node("UOP", "UOP: governance + gates", "warn")
    graph.add_edge("START", "ENV")
    graph.add_edge("ENV", "UOP", "policy selected", conditional=True)

    rendered, _dot, _receipt = graph.render_pair()
    restored = semantic_graph_from_mermaid(
        rendered,
        name="restored",
        role="EXECUTABLE_WORKFLOW",
    )

    assert [(node.node_id, node.label, node.kind) for node in restored.nodes] == [
        (node.node_id, node.label, node.kind) for node in graph.nodes
    ]
    assert [
        (edge.source, edge.target, edge.label, edge.conditional)
        for edge in restored.edges
    ] == [
        (edge.source, edge.target, edge.label, edge.conditional)
        for edge in graph.edges
    ]


def test_high_fanout_adds_balanced_render_constraints_without_semantic_edges() -> None:
    graph = SemanticGraph("balanced", direction="TB")
    graph.add_node("ROOT", "Root", "root")
    graph.begin_group("SHELL", "Large sibling shell", direction="TB")
    for ordinal in range(1, 26):
        graph.add_node(f"NODE_{ordinal:02d}", f"Node {ordinal}", "semantic")
    graph.end_group()
    for ordinal in range(1, 26):
        graph.add_edge("ROOT", f"NODE_{ordinal:02d}")

    rendered, dot, receipt = graph.render_pair()
    restored = semantic_graph_from_mermaid(rendered, name="balanced_restored")

    assert "EVIDENCE_LANE_LAYOUT=BALANCED_TWO_DIMENSIONAL_FANOUT_V1" in rendered
    assert "EVIDENCE_LANE_LAYOUT_CONSTRAINTS_BEGIN" in rendered
    assert " ~~~ " in rendered
    assert "style=invis" in dot
    assert receipt["balanced_two_dimensional_projection"] is True
    assert receipt["layout_constraints_change_semantic_topology"] is False
    assert receipt["layout_constraint_count"] > 0
    assert len(restored.nodes) == len(graph.nodes)
    assert len(restored.edges) == len(graph.edges)

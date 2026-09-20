from evidence_lane_plugin.graph_pipeline import SemanticGraph


def test_two_dimensional_layout_keeps_the_complete_square_root_sibling_grid():
    graph = SemanticGraph('balanced', direction='TB')
    graph.add_node('root', 'Root')
    graph.begin_group('items', 'Items')
    for ordinal in range(25):
        node = f'item_{ordinal:02d}'
        graph.add_node(node, f'Item {ordinal:02d}')
        graph.add_edge('root', node)
    graph.end_group()
    mmd, receipt = graph.render_mermaid()
    # Five columns of five nodes need four vertical constraints per column.
    assert mmd.count(' ~~~ ') == 20
    assert receipt['layout_constraint_count'] == 20
    assert receipt['balanced_two_dimensional_projection'] is True

"""Declared slide order and native object locators; renders stay separate."""
from .lane_contract import LaneView, ViewGraph
from .presentation_schema import PPT_MIGRATIONS


def presentation_graph(store, scope):
    from .presentation_profile import current_presentations, read_snapshot
    graph = ViewGraph(store.project_id, scope)
    current = current_presentations(store)
    for row in current['presentations']:
        if scope.query and scope.query != row['presentation_id']:
            continue
        manifest, facts = read_snapshot(store, row['snapshot_id'])
        presentation = graph.node('presentation', row['snapshot_id'], manifest['logical_name'], locator={
            'snapshot_id': row['snapshot_id'], 'presentation_id': row['presentation_id'], 'sha256': manifest['raw_object']})
        slide_nodes, previous = {}, None
        for item in facts['items']:
            if item['kind'] not in {'slide', 'notes', 'shape', 'table', 'image', 'chart'}:
                continue
            kind = item['kind']
            child = graph.node('presentation_' + kind, row['snapshot_id'] + ':' + item['item_id'],
                (item['text'][:100] or kind), locator={'snapshot_id': row['snapshot_id'], 'item_id': item['item_id'],
                    'part': item['part'], 'ordinal': item['ordinal'], 'kind': kind, 'slide_number': item['slide_number']})
            if kind == 'slide':
                slide_nodes[item['slide_number']] = child
                graph.edge(presentation, child, 'CONTAINS')
                if previous is not None:
                    graph.edge(previous, child, 'NEXT')
                previous = child
            else:
                graph.edge(slide_nodes.get(item['slide_number']), child, 'CONTAINS')
        if graph.truncated:
            break
    graph.truncated |= current.get('truncated', False)
    return graph.result()


def presentation_pointer(binding, graph, files):
    return {'schema': 'evidence-lane.presentation-locators.v4', 'snapshot_binding': binding,
        'locators': {node['key']: {'node_id': node['id'], **node['locator']} for node in graph['nodes']},
        'artifacts': files, 'page_numbers_from_graph': False}


def register_presentation_views(engine):
    from .presentation_profile import current_presentations
    engine.registry.register_view(LaneView('ppt.structure', 'ppt', 'ppt',
        'Exact presentation versions and declared slide order, notes, editable tables, images and chart locators; independent of page rendering.',
        presentation_graph, ('ppt',), PPT_MIGRATIONS, mmd_filename='ppt.mmd', dot_filename='ppt.dot',
        pointer_filename='ppt.pointer.json', pointer=presentation_pointer, supports_query=True,
        node_kinds=('presentation', 'presentation_slide', 'presentation_shape', 'presentation_table', 'presentation_image', 'presentation_notes', 'presentation_chart'),
        edge_kinds=('CONTAINS', 'NEXT'), head_reader=lambda store: {'ppt': current_presentations(store)}))

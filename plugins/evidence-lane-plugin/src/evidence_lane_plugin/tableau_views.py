"""Tableau document and native extract locators, separate from rendered charts."""
from .lane_contract import LaneView, ViewGraph
from .tableau_schema import TABLEAU_MIGRATIONS


def tableau_graph(store, scope):
    from .tableau_profile import current_tableaus, read_snapshot
    graph = ViewGraph(store.project_id, scope)
    current = current_tableaus(store)
    for row in current['tableaus']:
        if scope.query and scope.query != row['tableau_id']:
            continue
        manifest, facts = read_snapshot(store, row['snapshot_id'])
        root = graph.node('tableau', row['snapshot_id'], manifest['logical_name'], locator={
            'snapshot_id': row['snapshot_id'], 'tableau_id': row['tableau_id'], 'sha256': manifest['raw_object']})
        paths = {}
        for item in facts['items']:
            if item['kind'] not in {'workbook', 'datasource', 'sheet', 'dashboard', 'story', 'connection',
                    'relationship', 'hyper_schema', 'hyper_table'}:
                continue
            node = graph.node('tableau_' + item['kind'], row['snapshot_id'] + ':' + item['item_id'],
                str(item.get('caption') or item.get('name') or item['kind'])[:120], locator={
                    'snapshot_id': row['snapshot_id'], 'item_id': item['item_id'], 'part': item['part'],
                    'kind': item['kind'], 'xml_path': item.get('xml_path'), 'schema_name': item.get('schema_name'),
                    'table_name': item.get('table_name')})
            graph.edge(paths.get((item['part'], item.get('parent_xml_path')), root), node, 'CONTAINS')
            if item.get('xml_path'):
                paths[(item['part'], item['xml_path'])] = node
        if graph.truncated:
            break
    graph.truncated |= current.get('truncated', False)
    return graph.result()


def tableau_pointer(binding, graph, files):
    return {'schema': 'evidence-lane.tableau-locators.v4', 'snapshot_binding': binding,
        'locators': {node['key']: {'node_id': node['id'], **node['locator']} for node in graph['nodes']},
        'artifacts': files, 'hyper_rows_are_bounded_samples': True, 'layout_equivalence': False}


def register_tableau_views(engine):
    from .tableau_profile import current_tableaus
    engine.registry.register_view(LaneView('tableau.structure', 'tableau', 'sectors/tableau',
        'Exact workbook/source/sheet/model metadata and embedded native extract table locators.',
        tableau_graph, ('tableau',), TABLEAU_MIGRATIONS, mmd_filename='tableau.mmd', dot_filename='tableau.dot',
        pointer_filename='tableau.pointer.json', pointer=tableau_pointer, supports_query=True,
        node_kinds=('tableau', 'tableau_workbook', 'tableau_datasource', 'tableau_sheet', 'tableau_dashboard',
            'tableau_story', 'tableau_connection', 'tableau_relationship', 'tableau_hyper_schema', 'tableau_hyper_table'),
        edge_kinds=('CONTAINS',), head_reader=lambda store: {'tableau': current_tableaus(store)}))

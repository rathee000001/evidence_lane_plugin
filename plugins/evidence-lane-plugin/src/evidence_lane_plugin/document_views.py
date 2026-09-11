"""Document structure locators; page renders and relationship diagrams stay distinct."""
from .document_schema import DOC_MIGRATIONS
from .lane_contract import LaneView, ViewGraph


def document_graph(store, scope):
    from .document_profile import current_documents, read_snapshot
    graph = ViewGraph(store.project_id, scope)
    current = current_documents(store)
    for row in current['documents']:
        if scope.query and scope.query != row['document_id']:
            continue
        manifest, facts = read_snapshot(store, row['snapshot_id'])
        document = graph.node('document', row['snapshot_id'], manifest['logical_name'], locator={
            'snapshot_id': row['snapshot_id'], 'document_id': row['document_id'], 'sha256': manifest['raw_object']})
        for item in facts['items']:
            if item['kind'] not in {'heading', 'table', 'image', 'content_control'}:
                continue
            kind = item['kind']
            child = graph.node('document_' + kind, row['snapshot_id'] + ':' + item['item_id'],
                (item['text'][:100] or kind), locator={'snapshot_id': row['snapshot_id'], 'item_id': item['item_id'],
                    'part': item['part'], 'ordinal': item['ordinal'], 'kind': kind})
            graph.edge(document, child, 'CONTAINS')
        if graph.truncated:
            break
    graph.truncated |= current.get('truncated', False)
    return graph.result()


def document_pointer(binding, graph, files):
    return {'schema': 'evidence-lane.document-locators.v4', 'snapshot_binding': binding,
        'locators': {node['key']: {'node_id': node['id'], **node['locator']} for node in graph['nodes']},
        'artifacts': files, 'page_numbers_from_graph': False}


def register_document_views(engine):
    from .document_profile import current_documents
    engine.registry.register_view(LaneView('docs.structure', 'docs', 'sectors/docs',
        'Exact document versions and native headings, tables, images and content-control locators; independent of page rendering.',
        document_graph, ('docs',), DOC_MIGRATIONS, mmd_filename='docs.mmd', dot_filename='docs.dot',
        pointer_filename='docs.pointer.json', pointer=document_pointer, supports_query=True,
        node_kinds=('document', 'document_heading', 'document_table', 'document_image', 'document_content_control'),
        edge_kinds=('CONTAINS',), head_reader=lambda store: {'docs': current_documents(store)}))

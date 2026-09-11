"""Distinct Research, artifact and Custom views over their exact owning facts.

Only source attribution and recorded structure are edges. Prefix-labelled
findings and decisions do not become validated conclusions or Plan actions.
"""
from __future__ import annotations

import hashlib

from .errors import LaneError
from .hashing import canonical_json_bytes
from .lane_contract import LaneView, ViewGraph
from .migrations import read_compatibility
from .sector_evidence_schema import ORIGINAL_KINDS, PREFIX, TABLES, migrations
from .selector_schema import active_selector_sql


def selectors(store, lane_id):
    from .sector_evidence_profile import lane_if_present
    lane = lane_if_present(store, lane_id)
    if lane is None:
        return None, []
    read_compatibility(lane, migrations(lane_id))
    prefixes = [(PREFIX[lane_id], 'local_file')]
    if lane_id == 'research':
        prefixes += [('research_web', 'web_capture'), ('research_discovery', 'provider_discovery')]
    with lane.connection(read_only=True) as connection:
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    return lane, [(prefix, origin) for prefix, origin in prefixes if prefix + '_current' in present]


def source_heads(store, lane_id):
    """Hash every current selector, independent of view writes and graph limits."""
    lane, owners = selectors(store, lane_id)
    result = {}
    if lane is not None:
        with lane.connection(read_only=True) as connection:
            for prefix, origin in owners:
                checksum, count = hashlib.sha256(), 0
                for row in connection.execute(f'SELECT c.source_id,c.snapshot_id FROM {prefix}_current c WHERE {active_selector_sql(lane)} ORDER BY c.source_id'):
                    checksum.update(canonical_json_bytes(list(row)) + b'\n')
                    count += 1
                result[origin] = {'current_sources': count, 'selector_sha256': checksum.hexdigest()}
    return {lane_id: {'initialized': bool(owners), 'current_selectors': result}}


def snapshots(store, lane_id, scope):
    lane, owners = selectors(store, lane_id)
    if lane is None or not owners:
        return [], False
    branches, args = [], []
    for prefix, origin in owners:
        branches.append(f"SELECT c.source_id,c.snapshot_id,'{origin}' AS origin FROM {prefix}_current c WHERE {active_selector_sql(lane)}"
            + (' AND c.source_id=?' if scope.query else ''))
        if scope.query:
            args.append(scope.query)
    with lane.connection(read_only=True) as connection:
        rows = connection.execute(' UNION ALL '.join(branches)
            + ' ORDER BY origin,source_id LIMIT 129', args).fetchall()
    return [dict(row) for row in rows[:128]], len(rows) > 128


def snapshot(store, lane_id, row):
    from .sector_evidence_profile import read_snapshot
    if row['origin'] == 'web_capture':
        from .research_web_profile import read_snapshot as read_web
        value = read_web(store, row['snapshot_id'])[:2]
    elif row['origin'] == 'provider_discovery':
        from .research_discovery_profile import read_snapshot as read_discovery
        value = read_discovery(store, row['snapshot_id'])[:2]
    else:
        value = read_snapshot(store, lane_id, row['snapshot_id'])
    if value[0]['source_id'] != row['source_id']:
        raise LaneError('SECTOR_EVIDENCE_VIEW_SELECTOR', 'The current selector names a different source than its immutable snapshot.')
    return value


def source_node(graph, lane_id, row, manifest):
    kind = {'local_file': 'research_source', 'web_capture': 'captured_page',
        'provider_discovery': 'discovery_query'}[row['origin']] if lane_id == 'research' else ORIGINAL_KINDS[lane_id][0]
    locator = {'lane_id': lane_id, 'source_id': row['source_id'], 'snapshot_id': row['snapshot_id'],
        'path': manifest['logical_name'], 'sha256': manifest['raw_object'], 'origin': row['origin'],
        'representation': 'original_source', 'source_currentness': 'not_rechecked_by_view'}
    if row['origin'] == 'web_capture':
        locator.update(url=manifest['url'], final_url=manifest['capture']['final_url'],
            captured_at=manifest['capture']['captured_at'], extractor=manifest['parser'],
            capture_snapshot=manifest['capture_snapshot'])
        if manifest['capture_snapshot']:
            kind = 'saved_page_extraction'
    elif row['origin'] == 'provider_discovery':
        locator.update(query=manifest['parameters']['query'], target_documents_ingested=False)
    label = locator.get('url') or locator.get('query') or manifest['logical_name']
    return graph.node(kind, row['snapshot_id'], label, locator=locator)


def fact_node(graph, lane_id, row, item):
    locator = {'lane_id': lane_id, 'source_id': row['source_id'], 'snapshot_id': row['snapshot_id'],
        'item_id': item['item_id'], 'kind': item['kind'], 'part': item['part'], 'ordinal': item['ordinal']}
    for field in ('line', 'char_start', 'char_end', 'source_locator', 'url', 'citation_id', 'target_status',
        'assertion_status', 'cell_type', 'table', 'name', 'row_locator', 'safe_path', 'member_content_read',
        'imported_sql_executed', 'native_kind', 'parser', 'output_verified', 'executed_by_intake'):
        if field in item:
            locator[field] = item[field]
    label = item.get('name') or item.get('url') or item['text'] or item['part']
    # A visible state survives the MMD/DOT renderer as well as the pointer.
    state = item.get('assertion_status') or ('review_required' if item['kind'].endswith('review_required') else None)
    if item['kind'] == 'archive_member':
        state = 'unread_archive_member' if item['safe_path'] else 'unsafe_archive_member_path'
    elif item.get('output_verified') is False:
        state = 'saved_output_unverified'
    return graph.node(item['kind'], row['snapshot_id'] + ':' + item['item_id'], label,
        state=state, locator=locator)


def sqlite_structure(graph, lane_id, row, items, nodes):
    """Recorded table/FK/selected-row structure of one exact imported image."""
    tables = {item['name']: nodes.get(item['item_id']) for item in items if item['kind'] == 'sqlite_table'}
    for item in items:
        if graph.truncated:
            break
        child = nodes.get(item['item_id'])
        if child is None:
            continue
        if item['kind'] == 'sqlite_row' and item['table'] in tables:
            graph.edge(tables[item['table']], child, 'SELECTED_ROW')
        elif item['kind'] == 'sqlite_relationship':
            relation = item['foreign_key']
            source, target = tables.get(item['from_table']), tables.get(relation['table'])
            graph.edge(source, child, 'DECLARES_FOREIGN_KEY')
            # Missing target tables are retained as unverified references, not
            # silently invented as actual imported schema entities.
            if target is None:
                target = graph.node('sqlite_table_reference', row['snapshot_id'] + ':table-ref:' + relation['table'],
                    relation['table'], state='target_table_not_in_inspected_schema', locator={
                        'lane_id': lane_id, 'snapshot_id': row['snapshot_id'], 'item_id': item['item_id'],
                        'table': relation['table'], 'resolved': False})
            graph.edge(child, target, 'REFERENCES_TABLE', evidence={
                'snapshot_id': row['snapshot_id'], 'item_id': item['item_id'], 'foreign_key': relation,
                'relationship_validated': False})


def research_graph(store, scope):
    graph = ViewGraph(store.project_id, scope)
    rows, truncated = snapshots(store, 'research', scope)
    for row in rows:
        manifest, facts = snapshot(store, 'research', row)
        root = source_node(graph, 'research', row, manifest)
        if row['origin'] == 'web_capture' and manifest['capture_snapshot']:
            origin = graph.node('saved_response_reference', manifest['capture_snapshot'], 'Selected saved response', locator={
                'lane_id': 'research', 'source_id': row['source_id'], 'snapshot_id': manifest['capture_snapshot'],
                'sha256': manifest['raw_object'], 'path': manifest['logical_name'], 'representation': 'original_source',
                'historical_reference': True, 'url': manifest['url']})
            graph.edge(root, origin, 'EXTRACTED_FROM_SAVED_RESPONSE')
        nodes = {}
        for item in facts['items']:
            if item['kind'] == 'research_source':
                continue
            node = nodes[item['item_id']] = fact_node(graph, 'research', row, item)
            relation = ('REPORTS_CITATION' if item['kind'] == 'research_citation' else
                'RECORDS_QUERY' if row['origin'] == 'provider_discovery' and item['kind'] == 'research_question' else
                'REPORTS_SEARCH_RESULT' if item.get('target_status') == 'unvisited_search_result' else 'CONTAINS')
            graph.edge(root, node, relation)
            if graph.truncated:
                break
        sqlite_structure(graph, 'research', row, facts['items'], nodes)
        if graph.truncated:
            break
    graph.truncated |= truncated
    return graph.result()


def artifacts_graph(store, scope):
    graph = ViewGraph(store.project_id, scope)
    rows, truncated = snapshots(store, 'artifacts', scope)
    for row in rows:
        manifest, facts = snapshot(store, 'artifacts', row)
        root = source_node(graph, 'artifacts', row, manifest)
        nodes = {}
        for item in facts['items']:
            if item['kind'] == 'project_artifact':
                continue
            node = nodes[item['item_id']] = fact_node(graph, 'artifacts', row, item)
            relation = ('LISTS_MEMBER' if item['kind'] == 'archive_member' else
                'REQUIRES_REVIEW' if item['kind'].endswith('review_required') else
                'HAS_EXTRACTED_EVIDENCE' if item['kind'] in {'artifact_text_extract', 'native_fact', 'artifact_media_probe'} else 'CONTAINS')
            graph.edge(root, node, relation)
            if graph.truncated:
                break
        sqlite_structure(graph, 'artifacts', row, facts['items'], nodes)
        if graph.truncated:
            break
    graph.truncated |= truncated
    return graph.result()


def custom_graph(store, scope, *, lane_id='custom'):
    graph = ViewGraph(store.project_id, scope)
    rows, truncated = snapshots(store, lane_id, scope)
    for row in rows:
        manifest, facts = snapshot(store, lane_id, row)
        root = source_node(graph, lane_id, row, manifest)
        nodes = {}
        for item in facts['items']:
            if item['kind'] == 'custom_source':
                continue
            node = nodes[item['item_id']] = fact_node(graph, lane_id, row, item)
            relation = 'REPORTS_ASSERTION' if item.get('assertion_status') else 'CONTAINS'
            graph.edge(root, node, relation)
            if graph.truncated:
                break
        sqlite_structure(graph, lane_id, row, facts['items'], nodes)
        if graph.truncated:
            break
    graph.truncated |= truncated
    return graph.result()


def research_pointer(binding, graph, files):
    return {'schema': 'evidence-lane.research-evidence-navigation.v4', 'snapshot_binding': binding,
        'sources_and_assertions': {node['key']: {'node_id': node['id'], **node['locator']} for node in graph['nodes']},
        'artifacts': files, 'source_assertions_validated': False, 'targets_visited_by_view': False}


def artifacts_pointer(binding, graph, files):
    return {'schema': 'evidence-lane.artifact-evidence-navigation.v4', 'snapshot_binding': binding,
        'artifacts_and_parts': {node['key']: {'node_id': node['id'], **node['locator']} for node in graph['nodes']},
        'artifacts': files, 'archive_members_extracted_by_view': False, 'artifact_execution_or_review_inferred': False}


def custom_pointer(binding, graph, files):
    return {'schema': 'evidence-lane.custom-source-navigation.v4', 'snapshot_binding': binding,
        'items_and_schema': {node['key']: {'node_id': node['id'], **node['locator']} for node in graph['nodes']},
        'artifacts': files, 'source_decisions_applied_to_plan': False, 'imported_sql_executed': False,
        'row_identity': 'ordinal_in_exact_database_image'}


def named_custom_view(template, lane_id):
    """Bind instance callbacks in their producer module for implementation hashing."""
    from dataclasses import replace

    from .lanes import is_named_custom_lane
    if not is_named_custom_lane(lane_id) or template.view_id != 'custom.structure':
        raise LaneError('INVALID_VIEW_CONTRACT', 'A named Custom view derives from its exact retained template.')
    spec = replace(template, view_id=lane_id + '.structure', lane_id=lane_id,
        folder='sectors/' + lane_id, mmd_filename=lane_id + '.mmd', dot_filename=lane_id + '.dot',
        head_owners=(lane_id,), producer=lambda store, scope: custom_graph(store, scope, lane_id=lane_id),
        head_reader=lambda store: source_heads(store, lane_id))
    spec.validate()
    return spec


def register_evidence_views(engine):
    definitions = (
        ('research', research_graph, research_pointer,
            'Exact local sources, captured responses and unvisited provider results with attributed Research assertions and citations.',
            ('captured_page', 'discovery_query', 'saved_page_extraction', 'saved_response_reference'),
            ('REPORTS_CITATION', 'RECORDS_QUERY', 'REPORTS_SEARCH_RESULT', 'EXTRACTED_FROM_SAVED_RESPONSE')),
        ('artifacts', artifacts_graph, artifacts_pointer,
            'Artifact bytes, extracted evidence, archive directory entries and explicit review state; no execution or release approval.',
            (), ('LISTS_MEMBER', 'REQUIRES_REVIEW', 'HAS_EXTRACTED_EVIDENCE')),
        ('custom', custom_graph, custom_pointer,
            'Custom source assertions and selected SQLite table, foreign-key and row locators; no Plan mutation or imported SQL execution.',
            (), ('REPORTS_ASSERTION',)),
    )
    def heads(lane_id):
        return lambda store: source_heads(store, lane_id)
    for lane_id, producer, pointer, meaning, nodes, edges in definitions:
        engine.registry.register_view(LaneView(lane_id + '.structure', lane_id, 'sectors/' + lane_id,
            meaning, producer, (lane_id,), migrations(lane_id),
            mmd_filename=lane_id + '.mmd', dot_filename=lane_id + '.dot', pointer_filename='lane_pointer.json',
            pointer=pointer, supports_query=True, head_reader=heads(lane_id),
            node_kinds=(*TABLES[lane_id], *nodes, 'sqlite_table_reference'),
            edge_kinds=('CONTAINS', 'SELECTED_ROW', 'DECLARES_FOREIGN_KEY', 'REFERENCES_TABLE', *edges)))

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evidence_lane_plugin.graph_pipeline import semantic_graph_from_mermaid
from evidence_lane_plugin.lanes import SECTOR_LANE_IDS
from evidence_lane_plugin.sector_support import IMPLEMENTED_SECTORS

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/evidence-lane-plugin'


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nested_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nested_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_objects(child)


def test_every_current_action_has_one_or_more_declared_package_workflow_owners():
    canonical = {
        row['name'] for row in read(PLUGIN / 'schemas/public-action-schemas.v4.json')['actions']
    }
    represented = {
        row['name']
        for path in (PLUGIN / 'authorities').rglob('workflow.v4.json')
        for row in read(path).get('actions', [])
    }
    operations = read(PLUGIN / 'toolchains/operation-toolchains.v4.json')['operations']
    operation_names = {row['operation'] for row in operations}
    tool_ids = {
        row['tool_id'] for row in read(PLUGIN / 'toolchains/tool-definitions.v4.json')['entries']
    }
    route_tools = {
        tool_id for operation in operations for route in operation['routes']
        for tool_id in route['tool_ids']
    }
    assert represented == canonical
    assert operation_names == canonical
    assert route_tools <= tool_ids
    assert tuple(IMPLEMENTED_SECTORS) == tuple(SECTOR_LANE_IDS)
    assert tuple(
        row['lane_id'] for row in read(
            PLUGIN / 'authorities/project_sectors/sector-runtime-registry.v4.json'
        )['implemented_sectors']
    ) == tuple(SECTOR_LANE_IDS)


def test_every_packaged_mmd_dot_pair_is_connected_and_has_one_current_topology_receipt():
    receipt_paths = [
        *(PLUGIN / 'authorities').rglob('workflow.v4.json'),
        PLUGIN / 'env/SOURCE_PACKET_AUDIT.json',
    ]
    receipts = [
        value
        for path in receipt_paths
        for value in nested_objects(read(path))
        if value.get('mmd_sha256') and value.get('dot_sha256')
    ]
    graphs = sorted([
        *(PLUGIN / 'authorities').rglob('*.mmd'),
        PLUGIN / 'env/env_mmd.mmd',
        PLUGIN / 'uop/uop_mmd.mmd',
    ])
    assert graphs
    for mmd_path in graphs:
        dot_path = mmd_path.with_suffix('.dot')
        assert dot_path.is_file(), mmd_path
        matching = [
            row for row in receipts
            if row['mmd_sha256'].lower() == sha(mmd_path)
            and row['dot_sha256'].lower() == sha(dot_path)
        ]
        assert matching, mmd_path
        assert all(row['mmd_dot_same_semantic_topology'] is True for row in matching)
        graph = semantic_graph_from_mermaid(
            mmd_path.read_text(encoding='utf-8'),
            name=mmd_path.stem.replace('-', '_'),
            role=matching[0]['graph_role'],
        )
        adjacency = {node.node_id: set() for node in graph.nodes}
        for edge in graph.edges:
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)
        unseen = set(adjacency)
        components = 0
        while unseen:
            components += 1
            stack = [next(iter(unseen))]
            visited = set()
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                stack.extend(adjacency[node] - visited)
            unseen -= visited
        assert components == 1, mmd_path
        assert all(adjacency.values()), mmd_path
        assert all(not node.node_id.startswith('ROW_') for node in graph.nodes)


def test_schema_graphs_have_one_owner_root_and_keep_rows_in_sqlite_only():
    workflow_paths = sorted((PLUGIN / 'authorities').rglob('workflow.v4.json'))
    schema_receipts = [
        (path, read(path).get('schema_topology'))
        for path in workflow_paths
        if isinstance(read(path).get('schema_topology'), dict)
    ]
    assert schema_receipts
    for path, receipt in schema_receipts:
        assert receipt['graph_role'] == 'AUTHORITY_TRAVERSAL'
        assert receipt['mmd_and_dot_are_sqlite_traversal_maps'] is True
        assert receipt['sqlite_authority_replaced'] is False
        folder = path.parent
        candidates = [
            candidate for candidate in folder.glob('*.mmd')
            if candidate.name != 'workflow.mmd'
        ]
        assert len(candidates) == 1, path
        graph = semantic_graph_from_mermaid(
            candidates[0].read_text(encoding='utf-8'),
            name=candidates[0].stem.replace('-', '_'),
            role='AUTHORITY_TRAVERSAL',
        )
        assert [node.node_id for node in graph.nodes].count('SCHEMA_ROOT') == 1
        assert all(not node.node_id.startswith('ROW_') for node in graph.nodes)

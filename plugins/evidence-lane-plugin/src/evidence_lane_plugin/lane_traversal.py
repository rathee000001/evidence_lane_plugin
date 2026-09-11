"""Validate lane-owned traversal snapshots without importing them as authority."""
from __future__ import annotations

from .errors import LaneError
from .lane_contract import content_digest


def validate_view_graph(project_id, contract, scope, graph):
    try:
        nodes, edges = graph["nodes"], graph["edges"]
        valid = (isinstance(nodes, list) and isinstance(edges, list) and isinstance(graph["truncated"], bool)
                 and len(nodes) <= scope["node_limit"] <= 200 and len(edges) <= scope["edge_limit"] <= 500)
        ids = set()
        for node in nodes:
            identity = "n" + content_digest([project_id, node["kind"], node["key"]])
            valid &= (node["id"] == identity and identity not in ids and node["kind"] in contract["node_kinds"]
                      and isinstance(node["label"], str) and len(node["label"]) <= 240)
            ids.add(identity)
        keys = set()
        for edge in edges:
            key = (edge["source"], edge["target"], edge["kind"])
            valid &= (edge["source"] in ids and edge["target"] in ids and edge["kind"] in contract["edge_kinds"]
                      and key not in keys)
            keys.add(key)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise LaneError("VIEW_TOPOLOGY_CONTRACT", "The bounded graph does not match its lane's node and relationship contract.")


def validate_snapshot_contract(project_id, contract, manifest):
    try:
        binding, graph, files = manifest["binding"], manifest["graph"], manifest["files"]
        valid = (content_digest(contract) == binding["contract_digest"] and contract["view_id"] == binding["view_id"]
                 and contract["lane_id"] == binding["lane_id"] and contract["version"] == binding["contract_version"]
                 and binding["project_id"] == project_id and binding["node_count"] == len(graph["nodes"])
                 and binding["edge_count"] == len(graph["edges"]) and binding["truncated"] == graph["truncated"]
                 and binding["source_digest"] == content_digest(binding["source_head"])
                 and binding["query_digest"] == content_digest(binding["scope"])
                 and 1 <= len(files) <= 3 and len({item["role"] for item in files}) == len(files))
        for item in files:
            valid &= item["filename"] == contract["files"].get(item["role"])
        validate_view_graph(project_id, contract, binding["scope"], graph)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise LaneError("VIEW_MANIFEST_CONTRACT", "The artifact manifest differs from its original lane and scope contract.")

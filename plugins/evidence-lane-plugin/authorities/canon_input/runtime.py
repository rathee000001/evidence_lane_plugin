"""Binding to the canonical non-sector authority implementation."""

from importlib import import_module

AUTHORITY_ID = "canon_input"
CANONICAL_MODULE = "evidence_lane_plugin.canon_task_graph"

def canonical_module():
    return import_module(CANONICAL_MODULE)

__all__ = ["AUTHORITY_ID", "CANONICAL_MODULE", "canonical_module"]

"""Binding to the canonical non-sector authority implementation."""

from importlib import import_module

AUTHORITY_ID = "connector_brain"
CANONICAL_MODULE = "evidence_lane_plugin.connector_governance"

def canonical_module():
    return import_module(CANONICAL_MODULE)

__all__ = ["AUTHORITY_ID", "CANONICAL_MODULE", "canonical_module"]

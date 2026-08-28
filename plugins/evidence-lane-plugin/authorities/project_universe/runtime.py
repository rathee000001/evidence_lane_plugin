"""Binding to the canonical non-sector authority implementation."""

from importlib import import_module

AUTHORITY_ID = "project_universe"
CANONICAL_MODULE = "evidence_lane_plugin.project_universe"

def canonical_module():
    return import_module(CANONICAL_MODULE)

__all__ = ["AUTHORITY_ID", "CANONICAL_MODULE", "canonical_module"]

"""Binding to the canonical non-sector authority implementation."""

from importlib import import_module

AUTHORITY_ID = "source_authority"
CANONICAL_MODULE = "evidence_lane_plugin.source_authority"

def canonical_module():
    return import_module(CANONICAL_MODULE)

__all__ = ["AUTHORITY_ID", "CANONICAL_MODULE", "canonical_module"]

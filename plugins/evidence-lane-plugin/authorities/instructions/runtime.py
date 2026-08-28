"""Binding to canonical non-SQLite authority modules."""

from importlib import import_module

AUTHORITY_ID = "instructions"
CANONICAL_MODULES = ('evidence_lane_plugin.agent_configuration', 'evidence_lane_plugin.conversation_memory')

def canonical_modules():
    return tuple(import_module(name) for name in CANONICAL_MODULES)

__all__ = ["AUTHORITY_ID", "CANONICAL_MODULES", "canonical_modules"]

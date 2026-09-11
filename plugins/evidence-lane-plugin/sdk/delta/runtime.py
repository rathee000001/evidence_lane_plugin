"""Bind SDK callers to the current Plan-owned bounded execution service."""

from evidence_lane_plugin.adaptive_delta_entry import (
    DeltaAdmission,
    DeltaEnter,
    DeltaService,
    PlannedDeltaEnter,
)
from evidence_lane_plugin.adaptive_delta_exit import DeltaExit, read_recorded_exit

from ..contracts import load_sdk_contract


def delta_workflow_catalog():
    """Return current Plan-owned entry/query/exit/steer/continuation contracts."""

    return load_sdk_contract("delta/delta-workflow-registry.v4.json")

__all__ = [
    "DeltaAdmission",
    "DeltaEnter",
    "DeltaExit",
    "DeltaService",
    "PlannedDeltaEnter",
    "delta_workflow_catalog",
    "read_recorded_exit",
]

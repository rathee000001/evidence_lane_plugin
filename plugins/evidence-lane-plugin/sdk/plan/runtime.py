"""Bind the SDK to project Plan authority and full linked PLAN.md projection."""

from evidence_lane_plugin.plan_runtime import (
    HostPlanBind,
    HostPlanBinding,
    HostPlanProjection,
    HostPlanProjectionRead,
    HostPlanProjectionSync,
    PlanCreate,
    PlanRead,
    PlanReplace,
    PlanSnapshot,
    PlanStore,
    TaskDefinition,
)

from ..contracts import load_sdk_contract


def plan_contract_catalog():
    """Return Plan, steer and full host-projection SDK contracts."""

    return load_sdk_contract("plan/plan-contract-registry.v4.json")

__all__ = [
    "HostPlanBind",
    "HostPlanBinding",
    "HostPlanProjection",
    "HostPlanProjectionRead",
    "HostPlanProjectionSync",
    "PlanCreate",
    "PlanRead",
    "PlanReplace",
    "PlanSnapshot",
    "PlanStore",
    "TaskDefinition",
    "plan_contract_catalog",
]

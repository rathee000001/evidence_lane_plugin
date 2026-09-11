"""Bind SDK inspection to the direct current Codex ENV/UOP runtime."""

from evidence_lane_plugin.mode_governance import (
    bind_env_uop_action_policy,
    env_uop_authority_boundary,
    govern_mode_selection,
    load_env_uop_runtime_authority,
    route_env_uop_operation,
    validate_mode_governance_selection,
)

from ..contracts import load_sdk_contract


def env_uop_contract_catalog():
    """Return direct current Codex ENV/UOP contracts without Formula routes."""

    return load_sdk_contract("env_uop/env-uop-contract-registry.v4.json")

__all__ = [
    "bind_env_uop_action_policy",
    "env_uop_authority_boundary",
    "env_uop_contract_catalog",
    "govern_mode_selection",
    "load_env_uop_runtime_authority",
    "route_env_uop_operation",
    "validate_mode_governance_selection",
]

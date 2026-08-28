"""Evidence Lane private stateful code/PV engine."""

from .constants import ENGINE_VERSION, SCHEMA_VERSION
from .state_travel_contract import (
    build_direct_destination_orchestration,
    direct_forced_same_worktree_binding_contract,
    preflight_direct_forced_same_worktree_binding,
    verify_direct_destination_plan_acceptance,
    verify_direct_source_option2_closeout,
)
from .task_attachment_rehydration import (
    GLOBAL_PLUGIN_UPDATE_REHYDRATION_LAW,
    plan_global_plugin_update_rehydration,
)

__all__ = [
    "ENGINE_VERSION",
    "GLOBAL_PLUGIN_UPDATE_REHYDRATION_LAW",
    "SCHEMA_VERSION",
    "build_direct_destination_orchestration",
    "direct_forced_same_worktree_binding_contract",
    "plan_global_plugin_update_rehydration",
    "preflight_direct_forced_same_worktree_binding",
    "verify_direct_destination_plan_acceptance",
    "verify_direct_source_option2_closeout",
]
__version__ = ENGINE_VERSION

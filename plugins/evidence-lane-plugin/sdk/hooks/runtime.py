"""Executable SDK binding for packaged native hook events."""

from evidence_lane_plugin.hook_contract import (
    HOOK_EVENT_NAMES,
    context_hook_output,
    hook_event_contract,
    hook_event_input_schema,
    prepare_hook,
    submit_hook,
)

from ..contracts import load_sdk_contract


def hook_sdk_catalog():
    """Return exact packaged hook-event references and the current hook runtime."""

    return load_sdk_contract("hooks/hook-runtime.ref.v4.json")

__all__ = [
    "HOOK_EVENT_NAMES",
    "context_hook_output",
    "hook_event_contract",
    "hook_event_input_schema",
    "hook_sdk_catalog",
    "prepare_hook",
    "submit_hook",
]

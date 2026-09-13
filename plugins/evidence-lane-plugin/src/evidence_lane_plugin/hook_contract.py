"""Native Hook contracts and thin event entrypoints.

Admission, classification, event handling, authenticated transport, receipt
sealing, lifecycle boundaries and bounded output each have a distinct owner.
Hooks capture attributed host events; they never run the project lifecycle.
"""

from __future__ import annotations

from pathlib import Path

from .capture_routing import HOOK_EVENT_ORDER, HookEnvelope
from .errors import LaneError
from .hook_admission import HOOK_COMMON_FIELDS, HOOK_EVENT_FIELDS, MAX_HOOK_INPUT_BYTES, admit_hook
from .hook_classification import classify_hook
from .hook_event_handlers import NativeHookHandler, handler_class_for_event
from .hook_output import project_hook_output
from .hook_pipeline import HOOK_PIPELINE, run_hook_pipeline
from .hook_receipts import seal_hook_receipt
from .hook_stage_runtime import HOST_HOOK_STAGES
from .hook_transport import deliver_hook
from .storage import json_text

HOOK_CONTRACT_SCHEMA = "evidence-lane.native-hook-contract.v4"
HOOK_EVENT_NAMES = HOOK_EVENT_ORDER


def hook_event_handler_path(name: str) -> str:
    if name not in HOOK_EVENT_NAMES:
        raise LaneError("HOOK_EVENT_UNSUPPORTED", "Select a documented packaged Hook event.")
    return f"hooks/events/{name}/handler.py"


def hook_event_input_schema(name: str) -> dict:
    if name not in HOOK_EVENT_NAMES:
        raise LaneError("HOOK_EVENT_UNSUPPORTED", "Select a documented packaged Hook event.")
    properties: dict[str, dict] = {
        "hook_event_name": {"const": name},
        "session_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "turn_id": {"type": ["string", "null"], "maxLength": 128},
        "model": {"type": ["string", "null"], "maxLength": 200},
    }
    required = ["hook_event_name", "session_id"]
    for field in sorted(HOOK_EVENT_FIELDS[name]):
        if field in {"tool_input", "tool_response"}:
            properties[field] = {}
        elif field == "stop_hook_active":
            properties[field] = {"type": "boolean"}
        else:
            properties[field] = {"type": ["string", "null"]}
    for field in {
        "UserPromptSubmit": {"prompt"},
        "PreToolUse": {"tool_name", "tool_use_id"},
        "PermissionRequest": {"tool_name"},
        "PostToolUse": {"tool_name", "tool_use_id"},
        "SubagentStart": {"agent_id", "agent_type"},
        "SubagentStop": {"agent_id", "agent_type"},
    }.get(name, set()):
        required.append(field)
        properties[field] = {"type": "string", "minLength": 1}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"evidence-lane://hooks/{name}/input/v4",
        "title": f"Evidence Lane {name} visible input",
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": True,
        "x-evidence-lane-captured-fields": sorted(HOOK_COMMON_FIELDS | HOOK_EVENT_FIELDS[name]),
        "x-native-task-attestation": "not_provided",
    }


def hook_event_contract(name: str) -> dict:
    handler = handler_class_for_event(name)
    return {
        "schema": "evidence-lane.native-hook-event.v4",
        "event": name,
        "order": HOOK_EVENT_NAMES.index(name) + 1,
        "entrypoint": hook_event_handler_path(name),
        "handler_class": f"evidence_lane_plugin.hook_event_handlers.{handler.__name__}",
        "runtime_bridge": "hooks/runner.mjs",
        "host_stage_runner": "hooks/stage_runner.py",
        "host_stage_count": len(HOST_HOOK_STAGES),
        "host_stages": [stage["id"] for stage in HOST_HOOK_STAGES],
        "ambient_python_required": False,
        "input_schema": f"hooks/events/{name}/event.schema.json",
        "pipeline": f"hooks/events/{name}/pipeline.v4.json",
        "execution_owner": "evidence_lane_plugin.hook_pipeline",
        "route": "/v4/capture",
        "remote_route": "/remote/v4/capture",
        "requires_explicit_project_session_binding": True,
        "automatic_retry": False,
        "bounded_context_output": name in {"SessionStart", "UserPromptSubmit"},
        "host_control_output": False,
        "subagent_control_output": False,
        "native_installation_verified": False,
    }


def hook_manifest() -> dict:
    hooks = {}
    for name in HOOK_EVENT_NAMES:
        handlers = []
        for stage in HOST_HOOK_STAGES:
            handler = {
                "type": "command",
                "command": (
                    "node \"${PLUGIN_ROOT}/hooks/runner.mjs\" "
                    + name
                    + " "
                    + stage["id"]
                ),
                "commandWindows": (
                    "& node \"${PLUGIN_ROOT}\\hooks\\runner.mjs\" "
                    + name
                    + " "
                    + stage["id"]
                ),
                "timeout": 3 if name in {"Interrupt", "SessionEnd"} else 10,
                "statusMessage": stage["status"].format(event=name),
            }
            if stage["id"] == "EMIT" and name in {"SessionStart", "UserPromptSubmit"}:
                handler["additionalContextLimit"] = 3000
            handlers.append(handler)
        hooks[name] = [{"hooks": handlers}]
    return {"description": "Supported v4 visible-event capture through the plugin-owned engine; no lifecycle or host-control instructions.", "hooks": hooks}


def hook_registry() -> dict:
    return {
        "schema": HOOK_CONTRACT_SCHEMA,
        "source": "capture_routing.HOOK_EVENT_ORDER",
        "documentation": "https://learn.chatgpt.com/docs/hooks",
        "events": [
            {
                "name": name,
                "order": index,
                "input_fields": sorted(HOOK_COMMON_FIELDS | HOOK_EVENT_FIELDS[name]),
                "handler": hook_event_handler_path(name),
                "handler_class": f"evidence_lane_plugin.hook_event_handlers.{handler_class_for_event(name).__name__}",
                "pipeline_owner": "src/evidence_lane_plugin/hook_pipeline.py",
                "input_schema": f"hooks/events/{name}/event.schema.json",
                "event_contract": f"hooks/events/{name}/event.v4.json",
                "pipeline_contract": f"hooks/events/{name}/pipeline.v4.json",
                "route": "/v4/capture",
                "requires_explicit_project_session_binding": True,
                "remote_route": "/remote/v4/capture",
                "remote_config_env": "EVIDENCE_LANE_REMOTE_CONFIG",
                "host_control_output": False,
                "bounded_context_output": name in {"SessionStart", "UserPromptSubmit"},
                "automatic_retry": False,
            }
            for index, name in enumerate(HOOK_EVENT_NAMES, 1)
        ],
        "pipeline": [row["id"] for row in HOOK_PIPELINE],
        "stage_owners": {row["id"]: row["implementation"] for row in HOOK_PIPELINE},
        "host_pipeline": [stage["id"] for stage in HOST_HOOK_STAGES],
        "host_stage_count": len(HOST_HOOK_STAGES),
        "host_stage_runner": "hooks/stage_runner.py",
        "separate_host_process_per_stage": True,
        "runtime_bridge": "hooks/runner.mjs",
        "ambient_python_required": False,
        "event_isolation_owner": "src/evidence_lane_plugin/hook_event_isolation.py",
        "behavior_handoff_owner": "src/evidence_lane_plugin/hook_behavior_handoff.py",
        "lifecycle_boundary_owner": "src/evidence_lane_plugin/hook_lifecycle_boundary.py",
        "native_installation_verified": False,
    }


def prepare_hook(raw: bytes, expected_event: str) -> HookEnvelope:
    return admit_hook(raw, expected_event)


def submit_hook(envelope: HookEnvelope, selected_root: Path | None = None) -> dict:
    name = str(envelope.event.get("hook_event_name"))
    handler = handler_class_for_event(name)
    classification = classify_hook(envelope, name)
    return deliver_hook(handler.handle(envelope, classification), selected_root)


def context_hook_output(envelope: HookEnvelope, delivery: dict) -> dict:
    name = str(envelope.event.get("hook_event_name"))
    handler = handler_class_for_event(name)
    classification = classify_hook(envelope, name)
    handled = handler.handle(envelope, classification)
    result = delivery.get("result")
    if (
        delivery.get("captured") is True
        and isinstance(result, dict)
        and result.get("event_id") != envelope.event_id
    ):
        raise LaneError("HOOK_CONTEXT_BINDING", "The returned context differs from this exact Hook input.")
    normalized = delivery
    if "handler_id" not in delivery:
        normalized = {
            **delivery,
            "handler_id": handled.handler_id,
            "transport": delivery.get("transport", "direct_engine_capture"),
        }
    return project_hook_output(handled, seal_hook_receipt(handled, normalized))


def main_for_handler(handler: type[NativeHookHandler]) -> int:
    import sys
    try:
        raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
        output, _receipt = run_hook_pipeline(raw, handler)
        print(json_text(output))
        return 0
    except LaneError as error:
        print(json_text({"systemMessage": "Evidence Lane capture unavailable: " + error.code}))
        return 0


def main(argv=None, *, expected_event: str | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", choices=HOOK_EVENT_NAMES, required=expected_event is None)
    arguments = parser.parse_args(argv)
    return main_for_handler(handler_class_for_event(expected_event or arguments.event))


def main_for_event(expected_event: str) -> int:
    return main_for_handler(handler_class_for_event(expected_event))


__all__ = [
    "HOOK_COMMON_FIELDS", "HOOK_CONTRACT_SCHEMA", "HOOK_EVENT_FIELDS", "HOOK_EVENT_NAMES",
    "HOOK_PIPELINE", "MAX_HOOK_INPUT_BYTES", "context_hook_output", "hook_event_contract",
    "hook_event_handler_path", "hook_event_input_schema", "hook_manifest", "hook_registry",
    "main", "main_for_event", "main_for_handler", "prepare_hook", "submit_hook",
]

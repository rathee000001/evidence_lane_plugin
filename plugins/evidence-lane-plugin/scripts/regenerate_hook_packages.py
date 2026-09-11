"""Generate one executable package for every supported native hook event."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from evidence_lane_plugin.hook_contract import (
    HOOK_EVENT_NAMES,
    HOOK_PIPELINE,
    hook_event_contract,
    hook_event_handler_path,
    hook_event_input_schema,
    hook_manifest,
    hook_registry,
)
from evidence_lane_plugin.hook_event_handlers import handler_class_for_event


def encoded(value) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def handler_source(name: str) -> str:
    handler = handler_class_for_event(name).__name__
    return f'''"""Packaged {name} hook entrypoint; generated from the v4 hook contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

if __name__ == "__main__":
    from evidence_lane_plugin.hook_contract import main_for_handler
    from evidence_lane_plugin.hook_event_handlers import {handler}
    raise SystemExit(main_for_handler({handler}))
'''


def invoke_source() -> str:
    return '''"""Generic documented Hook launcher; event packages use fixed handler classes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    from evidence_lane_plugin.hook_contract import main
    raise SystemExit(main())
'''


def event_readme(name: str) -> str:
    context = "may return bounded additional context" if name in {"SessionStart", "UserPromptSubmit"} else "returns no control output"
    return f'''# {name}

This directory owns the packaged Evidence Lane handler for the documented
`{name}` event. Its fixed handler class runs the distinct admission,
classification, event handling, authenticated transport, receipt sealing and
bounded-output owners, and {context}.

The handler never selects a project from a title or path, starts lifecycle work,
controls a subagent, changes a Goal, retries an uncertain delivery, or reads a
host transcript. `event.schema.json` describes admitted input and
`pipeline.v4.json` and the numbered stage contracts bind every step to its
executable owner. Event isolation prevents stage reuse or automatic replay.
'''


def exports() -> dict[str, object]:
    outputs: dict[str, object] = {
        "hooks/hooks.json": hook_manifest(),
        "hooks/hook-event-registry.v4.json": hook_registry(),
        "hooks/invoke_hook.py": invoke_source(),
        "hooks/event-isolation.policy.v4.json": {
            "schema": "evidence-lane.hook-event-isolation-policy.v4",
            "owner": "src/evidence_lane_plugin/hook_event_isolation.py",
            "one_process_per_occurrence": True,
            "raw_input_persisted": False,
            "automatic_retry": False,
            "terminal_replay": False,
            "lifecycle_control": False,
        },
        "hooks/hook-stage-registry.v4.json": {
            "schema": "evidence-lane.hook-stage-registry.v4",
            "stage_count": len(HOOK_PIPELINE),
            "stages": list(HOOK_PIPELINE),
            "event_specific_handler_classes": True,
            "monolithic_hook_implementation": False,
            "runtime_bridge": "hooks/runner.mjs",
            "ambient_python_required": False,
        },
    }
    hook_events = []
    sdk_events = []
    schemas = []
    for name in HOOK_EVENT_NAMES:
        handler_path = hook_event_handler_path(name)
        schema_path = f"hooks/events/{name}/event.schema.json"
        pipeline_path = f"hooks/events/{name}/pipeline.v4.json"
        readme_path = f"hooks/events/{name}/README.md"
        contract_path = f"hooks/events/{name}/event.v4.json"
        central_schema_path = f"schemas/hooks/{name}.v4.schema.json"
        sdk_path = f"sdk/hooks/events/{name}.v4.json"
        handler_class = handler_class_for_event(name)
        outputs[handler_path] = handler_source(name)
        outputs[schema_path] = hook_event_input_schema(name)
        stage_paths = []
        for ordinal, stage in enumerate(HOOK_PIPELINE, 1):
            stage_path = f"hooks/events/{name}/{ordinal:02d}-{stage['id']}.stage.v4.json"
            implementation = stage["implementation"]
            if stage["id"] == "handle_event":
                implementation = f"hook_event_handlers.{handler_class.__name__}.handle"
            outputs[stage_path] = {
                "schema": "evidence-lane.native-hook-stage.v4",
                "event": name,
                "ordinal": ordinal,
                "stage": stage["id"],
                "owner": stage["owner"],
                "implementation": implementation,
                "handler_class": f"evidence_lane_plugin.hook_event_handlers.{handler_class.__name__}",
                "automatic_retry": False,
                "capture_only": True,
            }
            stage_paths.append(stage_path)
        outputs[pipeline_path] = {
            "schema": "evidence-lane.native-hook-pipeline.v4",
            "event": name,
            "stages": list(HOOK_PIPELINE),
            "stage_contracts": stage_paths,
            "distinct_executable_owners": True,
            "separate_process_per_stage": False,
            "automatic_retry": False,
        }
        outputs[readme_path] = event_readme(name)
        contract = hook_event_contract(name)
        contract["members"] = [
            {"path": path, "sha256": digest(outputs[path])}
            for path in (handler_path, schema_path, *stage_paths, pipeline_path, readme_path)
        ]
        outputs[contract_path] = contract
        outputs[central_schema_path] = outputs[schema_path]
        outputs[sdk_path] = {
            "schema": "evidence-lane.sdk-hook-binding.v4",
            "event": name,
            "entrypoint": handler_path,
            "event_contract": contract_path,
            "event_contract_sha256": digest(contract),
            "input_schema": central_schema_path,
            "input_schema_sha256": digest(outputs[central_schema_path]),
            "shared_runtime": "sdk/hooks/runtime.py",
            "execution_owner": "evidence_lane_plugin.hook_pipeline",
            "handler_class": f"evidence_lane_plugin.hook_event_handlers.{handler_class.__name__}",
            "runtime_bridge": "hooks/runner.mjs",
            "ambient_python_required": False,
            "stage_contracts": stage_paths,
            "automatic_retry": False,
            "installed_execution_claimed": False,
        }
        hook_events.append({"event": name, "path": contract_path, "sha256": digest(contract)})
        sdk_events.append({"event": name, "path": sdk_path, "sha256": digest(outputs[sdk_path])})
        schemas.append({"event": name, "path": central_schema_path, "sha256": digest(outputs[central_schema_path])})
    outputs["hooks/events/event-package-registry.v4.json"] = {
        "schema": "evidence-lane.hook-event-package-registry.v4",
        "event_count": len(hook_events),
        "events": hook_events,
        "pipeline_owner": "src/evidence_lane_plugin/hook_pipeline.py",
        "stage_registry": "hooks/hook-stage-registry.v4.json",
        "event_handler_owner": "src/evidence_lane_plugin/hook_event_handlers.py",
        "runtime_bridge": "hooks/runner.mjs",
        "ambient_python_required": False,
        "old_tunnel_host_required": False,
        "native_installation_verified": False,
    }
    outputs["sdk/hooks/hook-runtime.v4.json"] = {
        "schema": "evidence-lane.sdk-hook-runtime.v4",
        "event_count": len(sdk_events),
        "events": sdk_events,
        "runtime": "sdk/hooks/runtime.py",
        "pipeline_owner": "src/evidence_lane_plugin/hook_pipeline.py",
        "event_handler_owner": "src/evidence_lane_plugin/hook_event_handlers.py",
        "runtime_bridge": "hooks/runner.mjs",
        "ambient_python_required": False,
        "native_installation_verified": False,
    }
    outputs["schemas/hooks/hook-family.v4.json"] = {
        "schema": "evidence-lane.hook-schema-family.v4",
        "event_count": len(schemas),
        "events": schemas,
        "admission_owner": "evidence_lane_plugin.hook_admission.admit_hook",
        "classification_owner": "evidence_lane_plugin.hook_classification.classify_hook",
        "native_installation_verified": False,
    }
    return outputs


def generate(root: Path = PLUGIN_ROOT, *, check: bool = False) -> dict:
    root = Path(root).resolve()
    outputs = exports()
    changed = []
    for relative, value in outputs.items():
        target = root / relative
        body = encoded(value)
        if target.is_file() and target.read_bytes() == body:
            continue
        changed.append(relative)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
    actual_events = {
        path.name for path in (root / "hooks/events").iterdir() if path.is_dir()
    } if (root / "hooks/events").is_dir() else set()
    if actual_events != set(HOOK_EVENT_NAMES):
        changed.append("hooks/events:unexpected-event-directories")
    if check and changed:
        raise RuntimeError("Hook packages require regeneration: " + ", ".join(changed))
    return {
        "event_count": len(HOOK_EVENT_NAMES),
        "generated_members": len(outputs),
        "changed_members": len(changed),
        "check": check,
        "native_installation_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(generate(check=arguments.check)))


if __name__ == "__main__":
    main()

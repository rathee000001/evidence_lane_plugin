from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))

from evidence_lane_plugin.hook_contract import (
    HOOK_EVENT_NAMES,
    HOOK_PIPELINE,
    hook_event_handler_path,
    hook_manifest,
    hook_registry,
)
from evidence_lane_plugin.hook_event_handlers import HOOK_HANDLER_CLASSES


def test_every_registered_hook_has_one_executable_owned_event_package():
    manifest = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))
    registry = json.loads((PLUGIN / "hooks/hook-event-registry.v4.json").read_text(encoding="utf-8"))
    assert manifest == hook_manifest()
    assert registry == hook_registry()
    assert tuple(row["name"] for row in registry["events"]) == HOOK_EVENT_NAMES
    for event in registry["events"]:
        name = event["name"]
        folder = PLUGIN / "hooks/events" / name
        assert event["handler"] == hook_event_handler_path(name)
        stage_names = {
            f"{index:02d}-{stage['id']}.stage.v4.json"
            for index, stage in enumerate(HOOK_PIPELINE, 1)
        }
        assert {path.name for path in folder.iterdir() if path.name != '__pycache__'} == {
            "README.md", "event.schema.json", "event.v4.json", "handler.py",
            "pipeline.v4.json", *stage_names}
        contract = json.loads((folder / "event.v4.json").read_text(encoding="utf-8"))
        for member in contract["members"]:
            path = PLUGIN / member["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]
        pipeline = json.loads((folder / "pipeline.v4.json").read_text(encoding="utf-8"))
        assert pipeline["stages"] == list(HOOK_PIPELINE)
        assert pipeline["stage_contracts"] == [
            f"hooks/events/{name}/{stage}" for stage in sorted(stage_names)
        ]
        assert pipeline["distinct_executable_owners"]
        assert pipeline["separate_process_per_host_stage"] and not pipeline["automatic_retry"]

    assert len(HOOK_HANDLER_CLASSES) == len(HOOK_EVENT_NAMES) == 12
    assert {handler.event_name for handler in HOOK_HANDLER_CLASSES} == set(HOOK_EVENT_NAMES)

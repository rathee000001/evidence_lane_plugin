"""Current implementations derived from the engine's one typed registry."""
from __future__ import annotations

import hashlib

from .registry import ActionRegistry
from .storage import json_text


def current_implementation_registry(registry: ActionRegistry) -> dict:
    actions = registry.schemas()
    payload = {
        "schema": "evidence-lane.current-implementation-registry.v4",
        "authority": "engine_typed_action_registry",
        "routes": [
            {"action": action["name"], "owner": action["profile"], "workflow": action["workflow"], "permission": action["permission"],
             "mutates": action["mutates"], "project_required": action["project_required"],
             "implementation": "evidence_lane_plugin.sdk.dispatch"}
            for action in actions
        ],
        "removed_routes_have_fallback": False,
    }
    payload["digest"] = hashlib.sha256(json_text(payload).encode()).hexdigest()
    return payload

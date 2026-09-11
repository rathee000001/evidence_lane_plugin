"""Public SDK/MCP contract projection; no historical static-count fallback."""
from __future__ import annotations

import hashlib

from .current_route_registry import current_implementation_registry
from .registry import ActionRegistry
from .storage import json_text


class PublicSurfaceRegistryError(RuntimeError):
    pass

def derive_public_surface_registry(registry: ActionRegistry) -> dict:
    if not isinstance(registry, ActionRegistry) or not registry.frozen:
        raise PublicSurfaceRegistryError("Use the immutable registry of a started engine.")
    actions = registry.schemas()
    routes = current_implementation_registry(registry)
    payload = {
        "schema": "evidence-lane.public-surface.v4",
        "authority": "engine_typed_action_registry",
        "actions": actions,
        "workflows": registry.workflow_schemas(),
        "counts": {"tools": len(actions), "read": sum(not action["mutates"] for action in actions),
                   "write": sum(action["mutates"] for action in actions)},
        "route_digest": routes["digest"],
        "native_installation_verified": False,
    }
    payload["digest"] = hashlib.sha256(json_text(payload).encode()).hexdigest()
    return payload

def derive_runtime_catalog_constants(registry: ActionRegistry) -> dict:
    return derive_public_surface_registry(registry)["counts"]

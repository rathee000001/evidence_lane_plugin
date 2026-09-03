"""One exact project-root binding shared by every project authority."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .errors import require


def validate_project_root_binding(
    project_root: str | Path,
    *,
    project_id: str,
    error_code: str = "PROJECT_ROOT_BINDING_INVALID",
) -> Path:
    """Resolve the registered root; leaf-name identity is legacy-test fallback only."""

    root = Path(project_root).resolve()
    registry_path = root / "project.json"
    if registry_path.is_file():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        configured_value = str(
            registry.get("project_authority_root") or ""
        ).strip()
        configured_root = (
            Path(os.path.expandvars(configured_value)).resolve()
            if configured_value
            else None
        )
        internal_store_binding = bool(
            configured_root is None and root.name == project_id
        )
        external_authority_binding = configured_root == root
        require(
            registry.get("schema") == "evidence-lane.project-registry.v1"
            and registry.get("project_id") == project_id
            and (internal_store_binding or external_authority_binding),
            error_code,
            "The project registry does not bind this exact project ID and root.",
            status="MISMATCH",
            project_id=project_id,
            project_root=str(root),
            configured_project_authority_root=(
                str(configured_root) if configured_root is not None else None
            ),
        )
        return root
    require(
        not os.environ.get("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT", "").strip()
        and root.is_dir()
        and root.name == project_id,
        error_code,
        "Installed/runtime work requires project.json exact-root authority.",
        status="BLOCKED",
        project_id=project_id,
        project_root=str(root),
    )
    return root


__all__ = ["validate_project_root_binding"]

from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import atomic_write_json
from evidence_lane_plugin.project_root_binding import validate_project_root_binding


def test_explicit_project_root_name_is_independent_from_project_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "User Selected Project Root"
    root.mkdir()
    atomic_write_json(
        root / "project.json",
        {
            "schema": "evidence-lane.project-registry.v1",
            "project_id": "project-a",
            "project_authority_root": str(root.resolve()),
        },
    )

    assert validate_project_root_binding(root, project_id="project-a") == root.resolve()


def test_internal_store_registry_binds_null_authority_root_by_exact_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project-a"
    root.mkdir()
    atomic_write_json(
        root / "project.json",
        {
            "schema": "evidence-lane.project-registry.v1",
            "project_id": "project-a",
            "project_authority_root": None,
            "repository_path": str(tmp_path / "repository"),
        },
    )

    assert validate_project_root_binding(root, project_id="project-a") == root.resolve()


def test_explicit_project_root_registry_mismatch_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "selected-root"
    root.mkdir()
    atomic_write_json(
        root / "project.json",
        {
            "schema": "evidence-lane.project-registry.v1",
            "project_id": "other-project",
            "project_authority_root": str(root.resolve()),
        },
    )

    with pytest.raises(EvidenceLaneError) as mismatch:
        validate_project_root_binding(root, project_id="project-a")
    assert mismatch.value.code == "PROJECT_ROOT_BINDING_INVALID"


def test_runtime_work_rejects_leaf_name_only_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project-a"
    root.mkdir()
    monkeypatch.setenv(
        "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT",
        str(tmp_path / ".codex" / "plugins" / "runtime" / "evidence-lane-plugin"),
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        validate_project_root_binding(root, project_id="project-a")
    assert blocked.value.code == "PROJECT_ROOT_BINDING_INVALID"

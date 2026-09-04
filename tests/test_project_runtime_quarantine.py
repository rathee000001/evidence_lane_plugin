from __future__ import annotations

from pathlib import Path

from evidence_lane_plugin.hashing import atomic_write_json
from evidence_lane_plugin.project_runtime_quarantine import (
    PROJECT_RUNTIME_QUARANTINE_CONFIRMATION,
    quarantine_project_runtime_history,
)


def test_project_runtime_history_moves_recoverably_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "selected-root"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "receipt.json").write_text("{}", encoding="utf-8")
    atomic_write_json(
        root / "project.json",
        {
            "schema": "evidence-lane.project-registry.v1",
            "project_id": "project-a",
            "project_authority_root": str(root.resolve()),
        },
    )

    quarantine = tmp_path / "quarantine"
    receipt = quarantine_project_runtime_history(
        root,
        project_id="project-a",
        quarantine_root=quarantine,
        confirmation=PROJECT_RUNTIME_QUARANTINE_CONFIRMATION,
    )

    assert receipt["status"] == "PASS"
    assert receipt["recoverable"] is True
    assert not runtime.exists()
    assert (quarantine / "receipt.json").is_file()
    assert quarantine.with_name("quarantine.manifest.json").is_file()

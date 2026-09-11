"""Studio projects the complete current engine without exposing removed profiles."""

import json
from pathlib import Path

from . import test_studio
from .test_studio import add_project, login

studio = test_studio.studio


def test_snapshot_exposes_every_current_operation_and_its_execution_contract(studio, tmp_path):
    engine, endpoint, client = studio
    entry = add_project(engine, tmp_path, "complete-registry")
    login(endpoint, client)
    response = client.get(
        "/studio/api/snapshot",
        params={"project_id": entry["project_id"]},
        headers={"X-Studio-Read": "1"},
    )
    assert response.status_code == 200
    body = response.json()
    expected = {row["name"]: row for row in engine.registry.schemas()}
    observed = {row["name"]: row for row in body["actions"]}
    assert len(observed) == len(expected) == 297
    assert set(observed) == set(expected)
    fields = {
        "name",
        "description",
        "permission",
        "profile",
        "workflow",
        "mutates",
        "requires_delta",
        "required_tools",
        "worker_operations",
        "queryable_in_delta",
        "studio_read",
        "cross_project_read",
    }
    assert all(set(row) == fields for row in observed.values())
    assert all(row == {key: expected[name][key] for key in fields} for name, row in observed.items())
    assert body["plugin_scopes"]["lanes"]
    assert not {"access", "onenote", "visio", "outlook", "project", "publisher"} & set(
        body["plugin_scopes"]["lanes"]
    )


def test_packaged_studio_contains_operation_and_three_office_scope_views() -> None:
    root = Path(__file__).resolve().parents[1]
    static = root / "plugins/evidence-lane-plugin/src/evidence_lane_plugin/studio"
    manifest = json.loads((static / "assets-manifest.json").read_bytes())
    bundle = b"".join(
        (static / row["file"]).read_bytes()
        for row in manifest["assets"].values()
        if Path(row["file"]).suffix in {".js", ".html"}
    )
    for phrase in (
        b"Every registered plugin operation",
        b"Search operations",
        b"Execution requirements",
        b"Office coverage is Word, PowerPoint, and Excel",
        b"OneDrive is storage",
        b"Excluded Office rows",
    ):
        assert phrase in bundle

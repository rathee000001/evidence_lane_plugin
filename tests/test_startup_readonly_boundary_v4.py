"""Login-started Studio remains a read-only view of the current engine."""

from __future__ import annotations

import pytest

from . import test_studio
from .test_studio import add_project, login

studio = test_studio.studio


@pytest.mark.parametrize(
    ("route", "payload"),
    [
        ("project", {}),
        ("accelerator", {}),
        ("plugin", {}),
        ("revoke-plugin", {}),
        ("cancel-job", {}),
        ("revoke-learning", {}),
        ("view-export", {}),
        ("backup", {}),
        ("git-restore", {}),
        ("control", {"operation": "shutdown"}),
    ],
)
def test_studio_browser_session_cannot_mutate_project_or_engine(
    studio,
    tmp_path,
    route: str,
    payload: dict,
) -> None:
    engine, endpoint, client = studio
    entry = add_project(engine, tmp_path, route)
    store = engine.directory.open(entry["project_id"], write=True)
    login(endpoint, client)
    before = {
        path: path.read_bytes()
        for path in store.root.rglob("*")
        if path.is_file()
    }
    response = client.post("/studio/api/" + route, json=payload)
    assert response.status_code == 404
    assert before == {
        path: path.read_bytes()
        for path in store.root.rglob("*")
        if path.is_file()
    }
    assert engine.phase == "running"


def test_studio_snapshot_uses_current_registry_and_open_only_control(studio, tmp_path) -> None:
    engine, endpoint, client = studio
    entry = add_project(engine, tmp_path, "current")
    login(endpoint, client)
    snapshot = client.get(
        "/studio/api/snapshot",
        params={"project_id": entry["project_id"]},
        headers={"X-Studio-Read": "1"},
    )
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert len(body["actions"]) == len(engine.registry.schemas()) == 297
    assert body["engine"]["runtime_identity"] == engine.runtime_identity
    assert body["engine"]["native_task_attestation"] == "not_provided"
    # The browser Studio exposes no control route. Window open/reopen remains
    # an authenticated owner-process operation outside the browser session.
    denied = client.post("/studio/api/control", json={"operation": "open_studio"})
    assert denied.status_code == 404
    assert engine.phase == "running"

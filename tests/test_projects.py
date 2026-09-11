from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.projects import ProjectAccess, ProjectDirectory
from evidence_lane_plugin.storage import ProjectStore


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    directory = ProjectDirectory(tmp_path / "runtime")
    record = directory.register(
        tmp_path / "state", source_root=source, create=True, read_only=False
    )
    store = directory.open(record["project_id"], write=True)
    access = ProjectAccess(store)
    access.initialize()
    return directory, store, access


def test_selected_roots_are_reopened_in_place(project):
    directory, store, _ = project
    assert directory.open(store.project_id).root == store.root
    assert directory.open(store.project_id).read_only
    assert not (directory.root / "project.sqlite3").exists()


def test_read_only_registration_never_elevates_on_open(project):
    directory, store, _ = project
    directory.register(store.root)
    with pytest.raises(LaneError) as error:
        directory.open(store.project_id, write=True)
    assert error.value.code == "READ_ONLY_PROJECT"


def test_copied_identity_requires_explicit_resolution(project, tmp_path):
    directory, store, _ = project
    copied = tmp_path / "duplicate"
    shutil.copytree(store.root, copied)
    with pytest.raises(LaneError) as error:
        directory.register(copied)
    assert error.value.code == "PROJECT_IDENTITY_COLLISION"


def test_registered_root_replacement_is_detected(project, tmp_path):
    directory, store, _ = project
    replacement = ProjectStore.create(tmp_path / "replacement", store.source_root)
    shutil.copyfile(replacement.database, store.database)
    with pytest.raises(LaneError) as error:
        directory.open(store.project_id)
    assert error.value.code == "PROJECT_BINDING_CHANGED"


def test_grants_bind_principal_permission_and_path(project, tmp_path):
    _, store, access = project
    access.issue("client-a", ["read"], [store.source_root])
    access.authorize("client-a", "read", path=store.source_root / "input.txt")
    for principal, permission, path in [
        ("client-b", "read", store.source_root / "input.txt"),
        ("client-a", "write", store.source_root / "output.txt"),
        ("client-a", "read", tmp_path / "outside.txt"),
    ]:
        with pytest.raises(LaneError) as error:
            access.authorize(principal, permission, path=path)
        assert error.value.code == "PERMISSION_DENIED"


def test_revocation_takes_effect_on_next_request(project):
    _, store, access = project
    grant = access.issue("client-a", ["write"], [store.source_root])
    access.authorize("client-a", "write")
    access.revoke(grant)
    with pytest.raises(LaneError):
        access.authorize("client-a", "write")


def test_expired_grant_is_not_reused(project):
    _, store, _ = project
    clock = [datetime.now(UTC)]
    access = ProjectAccess(store, clock=lambda: clock[0])
    access.issue("client-a", ["read"], [], expires_at=clock[0] + timedelta(seconds=5))
    access.authorize("client-a", "read")
    clock[0] += timedelta(seconds=6)
    with pytest.raises(LaneError):
        access.authorize("client-a", "read")


def test_grants_are_project_local(project, tmp_path):
    _, store, access = project
    access.issue("client-a", ["read"], [])
    other = ProjectStore.create(tmp_path / "other-state", store.source_root)
    other_access = ProjectAccess(other)
    other_access.initialize()
    with pytest.raises(LaneError):
        other_access.authorize("client-a", "read")

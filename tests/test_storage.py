from __future__ import annotations

import hashlib
import sqlite3

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.storage import DATABASE_NAME, ProjectStore


@pytest.fixture
def store(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    return ProjectStore.create(tmp_path / "state", source)


def test_project_identity_and_content_survive_reopen(store):
    store = store.lane('docs', create=True)
    digest = store.put_object(b"actual content")
    reopened = ProjectStore(store.root).lane('docs')
    assert reopened.project_id == store.project_id
    assert reopened.read_object(digest) == b"actual content"
    assert digest == hashlib.sha256(b"actual content").hexdigest()
    assert store.put_object(b"actual content") == digest
    with store.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM objects").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_failed_transaction_does_not_publish_receipt(store):
    store = store.lane('receipts', create=True)
    with pytest.raises(RuntimeError), store.transaction() as connection:
        store.append_receipt("test", {"result": "uncommitted"}, connection=connection)
        raise RuntimeError("crash")
    with store.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0


def test_read_only_project_blocks_sql_and_content_writes(store):
    readonly = ProjectStore(store.root, read_only=True)
    with pytest.raises(LaneError):
        readonly.put_object(b"not allowed")
    with pytest.raises(LaneError):
        readonly.append_receipt("test", {})
    with readonly.connection() as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM project")


def test_corrupted_object_is_never_silently_replaced(store):
    store = store.lane('docs', create=True)
    digest = store.put_object(b"original")
    store.object_path(digest).write_bytes(b"corrupt")
    with pytest.raises(LaneError) as error:
        store.put_object(b"original")
    assert error.value.code == "OBJECT_INTEGRITY_FAILED"
    assert store.object_path(digest).read_bytes() == b"corrupt"


def test_object_budget_and_digest_path_escape_are_rejected(store):
    store = store.lane('docs', create=True)
    with pytest.raises(LaneError) as error:
        store.put_object(b"too large", limit=1)
    assert error.value.code == "OBJECT_TOO_LARGE"
    with pytest.raises(LaneError) as error:
        store.read_object("../../outside")
    assert error.value.code == "INVALID_DIGEST"


def test_create_does_not_replace_existing_database(store):
    with pytest.raises(LaneError) as error:
        ProjectStore.create(store.root, store.source_root)
    assert error.value.code == "PROJECT_ALREADY_EXISTS"
    assert ProjectStore(store.root).project_id == store.project_id


def test_project_state_must_be_external_to_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(LaneError) as error:
        ProjectStore.create(source / "state", source)
    assert error.value.code == "EXTERNAL_STATE_REQUIRED"


def test_foreign_database_is_not_reinitialized(tmp_path):
    state = tmp_path / "foreign"
    state.mkdir()
    database = state / DATABASE_NAME
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE foreign_data(value TEXT)")
        connection.execute("INSERT INTO foreign_data VALUES('preserve')")
    before = database.read_bytes()
    with pytest.raises(LaneError) as error:
        ProjectStore(state)
    assert error.value.code == "UNRECOGNIZED_PROJECT"
    assert database.read_bytes() == before


def test_unsupported_project_version_is_not_repaired(store):
    with store.transaction() as connection:
        connection.execute("UPDATE project SET format_version=99")
    with pytest.raises(LaneError) as error:
        ProjectStore(store.root)
    assert error.value.code == "UNSUPPORTED_PROJECT_VERSION"

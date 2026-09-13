from __future__ import annotations

import sqlite3

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.migrations import Migration, apply_migrations, read_compatibility
from evidence_lane_plugin.storage import ProjectStore


@pytest.fixture
def store(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    return ProjectStore.create(tmp_path / "state", source).lane("docs", create=True)


def migration(version=1, statements=None):
    return Migration(
        "docs",
        version,
        "Document records",
        statements or ("CREATE TABLE doc_documents(id TEXT PRIMARY KEY, title TEXT NOT NULL)",),
    )


def test_migrations_are_idempotent_and_preserve_existing_rows(store):
    first = migration()
    assert len(apply_migrations(store, [first])) == 1
    with store.transaction() as connection:
        connection.execute("INSERT INTO doc_documents VALUES('one','preserved')")
    second = migration(2, ("ALTER TABLE doc_documents ADD COLUMN language TEXT",))
    assert len(apply_migrations(store, [first, second])) == 1
    assert apply_migrations(store, [first, second]) == []
    with store.connection(read_only=True) as connection:
        assert connection.execute("SELECT title FROM doc_documents").fetchone()[0] == "preserved"
        assert (
            connection.execute(
                "SELECT owner FROM schema_ownership WHERE object_name='doc_documents'"
            ).fetchone()[0]
            == "docs"
        )


def test_failed_migration_rolls_back_ddl_and_ledger(store):
    with pytest.raises(LaneError) as error:
        apply_migrations(
            store,
            [
                migration(
                    statements=(
                        "CREATE TABLE doc_partial(id TEXT)",
                        "INSERT INTO missing_table VALUES('bad')",
                    )
                )
            ],
        )
    assert error.value.code == "MIGRATION_FAILED"
    with store.connection(read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE name='doc_partial'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM project",
        "DROP TABLE objects",
        "PRAGMA writable_schema=ON",
        "CREATE TABLE excel_stolen(id TEXT)",
        "ATTACH DATABASE ':memory:' AS stolen",
    ],
)
def test_owner_cannot_mutate_other_authorities(store, statement):
    with pytest.raises(LaneError) as error:
        apply_migrations(store, [migration(statements=(statement,))])
    assert error.value.code == "MIGRATION_FAILED"
    assert ProjectStore(store.root).project_id == store.project_id


def test_changed_migration_and_downgrade_are_rejected(store):
    first = migration()
    apply_migrations(store, [first])
    with pytest.raises(LaneError) as error:
        apply_migrations(store, [migration(statements=("CREATE TABLE doc_other(id TEXT)",))])
    assert error.value.code == "MIGRATION_DRIFT"
    second = migration(2, ("CREATE INDEX doc_title ON doc_documents(title)",))
    apply_migrations(store, [first, second])
    with pytest.raises(LaneError) as error:
        apply_migrations(store, [first])
    assert error.value.code == "SCHEMA_NEWER_THAN_ENGINE"


def test_migration_cannot_modify_read_only_project(store):
    with pytest.raises(LaneError) as error:
        apply_migrations(ProjectStore(store.root, read_only=True), [migration()])
    assert error.value.code == "READ_ONLY_PROJECT"


def test_rename_cannot_escape_owner_namespace(store):
    first = migration()
    apply_migrations(store, [first])
    with pytest.raises(LaneError) as error:
        apply_migrations(
            store, [first, migration(2, ("ALTER TABLE doc_documents RENAME TO excel_stolen",))]
        )
    # SQLite versions may deny the rename during authorization or at the postcheck.
    assert error.value.code in {"MIGRATION_FAILED", "SCHEMA_OWNER_CONFLICT"}
    with store.connection(read_only=True) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE name='doc_documents'"
        ).fetchone()
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE name='excel_stolen'"
            ).fetchone()
            is None
        )


def test_deferred_trigger_cannot_bypass_migration_owner_checks(store):
    first = migration()
    apply_migrations(store, [first])
    with pytest.raises(LaneError) as error:
        apply_migrations(
            store,
            [
                first,
                migration(
                    2,
                    (
                        "CREATE TRIGGER doc_delete AFTER INSERT ON doc_documents BEGIN DELETE FROM project; END",
                    ),
                ),
            ],
        )
    assert error.value.code == "MIGRATION_FAILED"


def test_owned_table_rebuild_cleans_only_its_internal_statistics(store):
    first = migration(
        statements=(
            "CREATE TABLE doc_documents(id INTEGER PRIMARY KEY, title TEXT NOT NULL)",
        )
    )
    apply_migrations(store, [first])
    with store.transaction() as connection:
        connection.execute("INSERT INTO doc_documents(title) VALUES('preserved')")
        connection.execute("ANALYZE doc_documents")
        assert connection.execute(
            "SELECT 1 FROM sqlite_stat1 WHERE tbl='doc_documents'"
        ).fetchone()
    second = migration(
        2,
        (
            "CREATE TABLE doc_documents_next(id INTEGER PRIMARY KEY, title TEXT NOT NULL)",
            "INSERT INTO doc_documents_next SELECT * FROM doc_documents",
            "DROP TABLE doc_documents",
            "ALTER TABLE doc_documents_next RENAME TO doc_documents",
        ),
    )
    apply_migrations(store, [first, second])
    with store.connection(read_only=True) as connection:
        assert connection.execute("SELECT title FROM doc_documents").fetchone()[0] == "preserved"
        assert connection.execute(
            "SELECT 1 FROM sqlite_stat1 WHERE tbl='doc_documents'"
        ).fetchone() is None
    unauthorized = migration(3, ("DELETE FROM sqlite_stat1",))
    with pytest.raises(LaneError) as error:
        apply_migrations(store, [first, second, unauthorized])
    assert error.value.code == "MIGRATION_FAILED"
    assert error.value.details == {
        "owner": "docs",
        "version": 3,
        "statement_index": 1,
        "sqlite_error_code": sqlite3.SQLITE_AUTH,
        "sqlite_error_name": "SQLITE_AUTH",
    }


def test_owner_routes_to_its_own_lane_with_durable_schema_history(store):
    project = store.project
    change = Migration('memory', 1, 'Memory records', ('CREATE TABLE memory_records(value TEXT)',))
    apply_migrations(project, [change])
    memory = project.lane('memory')
    with memory.connection(read_only=True) as connection:
        row = connection.execute('SELECT * FROM schema_history_files').fetchone()
        assert row['owner'] == 'memory'
        assert row['filename'] == 'schema-history.v4.json'
        assert memory.schema_history.is_file()
    before = {path: path.read_bytes() for path in (project.database, memory.database)}
    assert read_compatibility(project, [change])[0]['status'] == 'compatible'
    assert all(path.read_bytes() == content for path, content in before.items())
    with project.connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='memory_records'").fetchone() is None
    with pytest.raises(LaneError, match='another lane'):
        apply_migrations(store, [change])


def test_multilane_migrations_publish_or_roll_back_together(store):
    project = store.project
    one = Migration('plan', 1, 'Plan fixture', ('CREATE TABLE plan_fixture(value TEXT)',))
    bad = Migration('memory', 1, 'Memory fixture', ('CREATE TABLE memory_fixture(value TEXT)', 'SELECT * FROM missing'))
    before = project.pv_head()
    with pytest.raises(LaneError, match='migration failed'):
        apply_migrations(project, [one, bad])
    assert project.pv_head() == before
    for lane_id in ('plan', 'memory'):
        with project.lane(lane_id).connection(read_only=True) as connection:
            assert connection.execute('SELECT name FROM sqlite_schema WHERE name=?', (lane_id + '_fixture',)).fetchone() is None
    good = Migration('memory', 1, 'Memory fixture', ('CREATE TABLE memory_fixture(value TEXT)',))
    apply_migrations(project, [one, good])
    assert project.pv_head()['revision'] == before['revision'] + 1
    catalog = {item['lane_id']: item for item in project.lane_catalog()}
    assert catalog['plan']['commit_id'] == catalog['memory']['commit_id'] == catalog['receipts']['commit_id']


def test_changed_schema_history_blocks_read_compatibility_and_publication(store):
    apply_migrations(store, [migration()])
    with store.connection(read_only=True) as connection:
        filename = connection.execute('SELECT filename FROM schema_history_files').fetchone()[0]
    assert filename == 'schema-history.v4.json'
    path = store.schema_history
    path.write_bytes(b'changed immutable history')
    before = store.project.pv_head()
    with pytest.raises(LaneError, match='direct schema-history projection'):
        read_compatibility(store, [migration()])
    with pytest.raises(LaneError, match='direct schema-history projection'), store.transaction() as connection:
        connection.execute("INSERT INTO doc_documents VALUES('lost','never published')")
    assert store.project.pv_head() == before
    with store.connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM doc_documents').fetchone()[0] == 0


def test_shared_view_owner_never_defaults_to_root(store):
    views = Migration('views', 1, 'Lane view', ('CREATE TABLE views_fixture(value TEXT)',))
    with pytest.raises(LaneError, match='selected lane'):
        apply_migrations(store.project, [views])
    apply_migrations(store, [views])
    assert read_compatibility(store, [views])[0]['status'] == 'compatible'

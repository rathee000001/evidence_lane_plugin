import hashlib
import json
import sqlite3
from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lane_storage_migration import migrate_shared_project
from evidence_lane_plugin.migrations import Migration, read_compatibility
from evidence_lane_plugin.storage import APPLICATION_ID, ProjectStore

MIGRATIONS = (
    Migration('plan', 1, 'Fixture Plan storage', ('CREATE TABLE plan_tasks(task_id TEXT PRIMARY KEY,title TEXT)',)),
    Migration('memory', 1, 'Fixture Memory storage', (
        'CREATE TABLE memory_locators(locator_id TEXT PRIMARY KEY,digest TEXT REFERENCES objects(digest))',
        'CREATE VIRTUAL TABLE memory_fts USING fts5(locator_id UNINDEXED,text)',
    )),
)


@pytest.fixture
def prototype(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    old = tmp_path / 'prototype'
    old.mkdir()
    database = old / 'project.sqlite3'
    content = b'original prototype content'
    digest = hashlib.sha256(content).hexdigest()
    path = old / 'objects' / digest[:2] / digest[2:]
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    connection = sqlite3.connect(database)
    connection.executescript('''
        CREATE TABLE project(singleton INTEGER PRIMARY KEY,project_id TEXT,source_root TEXT,format_version INTEGER,created_at TEXT);
        CREATE TABLE objects(digest TEXT PRIMARY KEY,size_bytes INTEGER,created_at TEXT);
        CREATE TABLE receipts(receipt_id TEXT PRIMARY KEY,kind TEXT,body_json TEXT,created_at TEXT);
        CREATE TABLE schema_migrations(owner TEXT,version INTEGER,digest TEXT,description TEXT,applied_at TEXT);
        CREATE TABLE schema_ownership(object_name TEXT PRIMARY KEY,owner TEXT,object_type TEXT);
        CREATE TABLE plan_tasks(task_id TEXT PRIMARY KEY,title TEXT);
        CREATE TABLE memory_locators(locator_id TEXT PRIMARY KEY,digest TEXT REFERENCES objects(digest));
        CREATE VIRTUAL TABLE memory_fts USING fts5(locator_id UNINDEXED,text);
        INSERT INTO plan_tasks VALUES('P1','Preserve the task');
        INSERT INTO memory_fts VALUES('M1','Searchable source memory');
        INSERT INTO schema_ownership VALUES('plan_tasks','plan','table');
        INSERT INTO schema_ownership VALUES('memory_locators','memory','table');
        INSERT INTO schema_ownership VALUES('memory_fts','memory','table');
        INSERT INTO schema_migrations VALUES('plan',1,'fixture-plan','fixture only','2026-09-05');
        INSERT INTO schema_migrations VALUES('memory',1,'fixture-memory','fixture only','2026-09-05');
    ''')
    for migration in MIGRATIONS:
        connection.execute('UPDATE schema_migrations SET digest=?,description=? WHERE owner=? AND version=?',
            (migration.digest, migration.description, migration.owner, migration.version))
    for name, kind in connection.execute("SELECT name,type FROM sqlite_schema WHERE name LIKE 'memory_%' OR name LIKE 'plan_%'").fetchall():
        connection.execute('INSERT OR REPLACE INTO schema_ownership VALUES(?,?,?)', (name, name.split('_')[0], kind))
    connection.execute(f'PRAGMA application_id={APPLICATION_ID}')
    connection.execute('INSERT INTO project VALUES(1,?,?,4,?)', (str(uuid4()), str(source), '2026-09-05'))
    connection.execute('INSERT INTO objects VALUES(?,?,?)', (digest, len(content), '2026-09-05'))
    connection.execute('INSERT INTO memory_locators VALUES(?,?)', ('M1', digest))
    connection.execute('INSERT INTO receipts VALUES(?,?,?,?)', ('R1', 'fixture', '{}', '2026-09-05'))
    connection.commit()
    connection.close()
    return old, digest, content


def source_hash(old):
    return hashlib.sha256((old / 'project.sqlite3').read_bytes()).hexdigest()


def test_prototype_migrates_into_owned_databases_without_source_changes(prototype, tmp_path):
    old, digest, content = prototype
    before = {p.relative_to(old): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    target = tmp_path / 'separate'
    report = migrate_shared_project(old, target, expected_source_sha256=source_hash(old), object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)
    assert report['status'] == 'complete'
    assert report['native_session_trust_transferred'] is False
    project = ProjectStore(target)
    with project.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT title FROM plan_tasks').fetchone()[0] == 'Preserve the task'
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name='memory_locators'").fetchone() is None
    with project.lane('memory').connection(read_only=True) as connection:
        assert connection.execute("SELECT locator_id FROM memory_fts WHERE memory_fts MATCH 'searchable'").fetchone()[0] == 'M1'
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('SELECT digest FROM schema_migrations').fetchone()[0] == MIGRATIONS[1].digest
    assert project.lane('memory').read_object(digest) == content
    assert len(list(project.lane('memory').schema_history.glob('memory.1.*.json'))) == 1
    assert all(row['status'] == 'compatible' for row in read_compatibility(project, MIGRATIONS))
    with pytest.raises(LaneError):
        project.lane('plan').read_object(digest)
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 2
    assert {p.relative_to(old): p.read_bytes() for p in old.rglob('*') if p.is_file()} == before
    assert not (target / 'project.sqlite3').exists()


def test_missing_object_ownership_and_bad_hash_fail_before_target_creation(prototype, tmp_path):
    old, digest, _ = prototype
    target = tmp_path / 'missing-ownership'
    with pytest.raises(LaneError, match='every registered object'):
        migrate_shared_project(old, target, expected_source_sha256=source_hash(old), object_lanes={})
    assert not target.exists()
    with pytest.raises(LaneError, match='selected hash'):
        migrate_shared_project(old, target, expected_source_sha256='0' * 64, object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)
    assert not target.exists()


def test_unmapped_or_cross_lane_schema_fails_before_target_creation(prototype, tmp_path):
    old, digest, _ = prototype
    connection = sqlite3.connect(old / 'project.sqlite3')
    connection.execute('CREATE TABLE mystery (value TEXT)')
    connection.commit()
    connection.close()
    target = tmp_path / 'unmapped'
    with pytest.raises(LaneError, match='explicit lane'):
        migrate_shared_project(old, target, expected_source_sha256=source_hash(old), object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)
    assert not target.exists()
    connection = sqlite3.connect(old / 'project.sqlite3')
    connection.execute('DROP TABLE mystery')
    connection.execute('CREATE TABLE memory_link (task TEXT REFERENCES plan_tasks(task_id))')
    connection.execute("INSERT INTO schema_ownership VALUES('memory_link','memory','table')")
    connection.commit()
    connection.close()
    with pytest.raises(LaneError, match='Cross-lane foreign keys'):
        migrate_shared_project(old, target, expected_source_sha256=source_hash(old), object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)
    assert not target.exists()


def test_interrupted_target_is_preserved_but_cannot_open(prototype, tmp_path, monkeypatch):
    old, digest, _ = prototype
    target = tmp_path / 'interrupted'
    from evidence_lane_plugin.storage import LaneStore

    monkeypatch.setattr(LaneStore, 'put_object', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('fixture interruption')))
    with pytest.raises(OSError, match='fixture interruption'):
        migrate_shared_project(old, target, expected_source_sha256=source_hash(old), object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)
    marker = json.loads((target / '.lane-migration.json').read_text(encoding='utf-8'))
    assert marker['status'] == 'failed'
    with pytest.raises(LaneError, match='did not complete'):
        ProjectStore(target)
    with pytest.raises(LaneError, match='fresh empty'):
        migrate_shared_project(old, target, expected_source_sha256=source_hash(old), object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)


def test_migration_completion_requires_registered_history_files(prototype, tmp_path):
    old, digest, _ = prototype
    target = tmp_path / 'history-registration'
    migrate_shared_project(old, target, expected_source_sha256=source_hash(old), object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)
    project = ProjectStore(target)
    for lane_id in ('plan', 'memory'):
        with project.lane(lane_id).connection(read_only=True) as connection:
            assert connection.execute('SELECT count(*) FROM schema_history_files').fetchone()[0] == 1


def test_migration_completion_records_verified_root_publication(prototype, tmp_path):
    old, digest, _ = prototype
    target = tmp_path / 'publication'
    result = migrate_shared_project(old, target, expected_source_sha256=source_hash(old), object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)
    assert result['root_publication_pending'] is False
    project = ProjectStore(target)
    assert result['published_root'] == project.pv_head()


@pytest.mark.parametrize('case', ['missing', 'mismatched'])
def test_unverified_history_definitions_are_rejected_before_target_creation(prototype, tmp_path, case):
    old, digest, _ = prototype
    target = tmp_path / case
    definitions = () if case == 'missing' else (Migration('plan', 1, 'Different history', MIGRATIONS[0].statements), MIGRATIONS[1])
    with pytest.raises(LaneError) as error:
        migrate_shared_project(old, target, expected_source_sha256=source_hash(old),
            object_lanes={digest: ('memory',)}, migration_definitions=definitions)
    assert error.value.code == ('MIGRATION_HISTORY_REQUIRED' if case == 'missing' else 'MIGRATION_HISTORY_INVALID')
    assert not target.exists()


def test_fts_row_identity_survives_migration(prototype, tmp_path):
    old, digest, _ = prototype
    with sqlite3.connect(old / 'project.sqlite3') as connection:
        connection.execute('UPDATE memory_fts SET rowid=37')
    target = tmp_path / 'fts-identity'
    migrate_shared_project(old, target, expected_source_sha256=source_hash(old),
        object_lanes={digest: ('memory',)}, migration_definitions=MIGRATIONS)
    with ProjectStore(target).lane('memory').connection(read_only=True) as connection:
        assert connection.execute("SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'searchable'").fetchone()[0] == 37

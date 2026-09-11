import hashlib
import shutil
import sqlite3
import time

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS
from evidence_lane_plugin.storage import DATABASE_NAME, ProjectStore, bounded_project_read


@pytest.fixture
def separate_project(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'untouched.txt').write_bytes(b'original source')
    return ProjectStore.create(tmp_path / 'state', source)


def test_every_lane_has_independent_sqlite_files_and_schema_history(separate_project):
    project = separate_project
    databases = set()
    for lane_id in CANONICAL_LANE_IDS:
        lane = project.lane(lane_id, create=True)
        assert lane.database.parent == lane.folder
        assert lane.files.is_dir() and lane.schema_history.is_dir()
        assert lane.database not in databases
        databases.add(lane.database)
        with lane.transaction() as connection:
            connection.execute('CREATE TABLE sample (value TEXT)')
            connection.execute('INSERT INTO sample VALUES(?)', (lane_id,))
    for lane_id in CANONICAL_LANE_IDS:
        with project.lane(lane_id).connection(read_only=True) as connection:
            assert connection.execute('SELECT value FROM sample').fetchone()[0] == lane_id
    with project.connection(read_only=True) as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        assert 'sample' not in names and 'objects' not in names and 'receipts' not in names
    assert len(project.lane_catalog()) == len(CANONICAL_LANE_IDS)
    assert all(row['head_digest'] for row in project.lane_catalog())
    assert project.pv_head()['revision'] == len(CANONICAL_LANE_IDS)
    assert (project.source_root / 'untouched.txt').read_bytes() == b'original source'


def test_content_and_receipts_have_explicit_owners(separate_project):
    project = separate_project
    memory = project.lane('memory', create=True)
    docs = project.lane('docs', create=True)
    content = b'exact same bytes, separately owned'
    digest = memory.put_object(content)
    assert digest == hashlib.sha256(content).hexdigest()
    with pytest.raises(LaneError, match='not registered'):
        docs.read_object(digest)
    assert docs.put_object(content) == digest
    assert memory.object_path(digest) != docs.object_path(digest)
    assert memory.read_object(digest) == docs.read_object(digest) == content
    with pytest.raises(LaneError, match='owning lane'):
        project.put_object(content)
    receipt = memory.append_receipt('fixture', {'digest': digest})
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute('SELECT receipt_id FROM receipts').fetchone()[0] == receipt
    with memory.connection(read_only=True) as connection:
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name='receipts'").fetchone() is None


def test_wrong_lane_identity_and_missing_lane_do_not_fallback(separate_project):
    project = separate_project
    assert {row['lane_id'] for row in project.lane_catalog()} == {
        'plan', 'chat_lineage', 'canon', 'memory', 'learning', 'sources', 'receipts', 'universe'}
    with pytest.raises(LaneError, match='no initialized'):
        project.lane('docs')
    one = project.lane('memory', create=True)
    two = project.lane('canon', create=True)
    shutil.copyfile(one.database, two.database)
    with pytest.raises(LaneError, match='another lane or project'):
        project.lane('canon')
    with pytest.raises(LaneError, match='another lane or project'), two.connection(read_only=True):
        pass


def test_root_database_rejects_business_tables_even_without_transaction(separate_project):
    with separate_project.connection(read_only=False) as connection:
        with pytest.raises(sqlite3.DatabaseError, match='authorized'):
            connection.execute('CREATE TABLE memory_wrong (value TEXT)')
        connection.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError, match='authorized'):
            connection.execute('CREATE TABLE universal_project_data (value TEXT)')


def test_read_only_and_bounded_reads_cover_every_lane(separate_project, tmp_path):
    project = separate_project
    lane = project.lane('docs', create=True)
    digest = lane.put_object(b'text')
    readonly = ProjectStore(project.root, read_only=True)
    with pytest.raises(LaneError):
        readonly.lane('memory', create=True)
    with pytest.raises(LaneError):
        readonly.lane('docs').put_object(b'forbidden')
    with bounded_project_read(project.root, time.monotonic() + 5):
        assert project.lane('docs').read_object(digest) == b'text'
        with pytest.raises(LaneError):
            project.lane('memory', create=True)
        with pytest.raises(LaneError):
            project.lane('docs').put_object(b'forbidden')
        with project.lane('docs').connection(read_only=True) as connection, pytest.raises(sqlite3.DatabaseError):
            connection.execute("ATTACH DATABASE ':memory:' AS other")
    with pytest.raises(LaneError, match='time budget'), bounded_project_read(project.root, time.monotonic() - 1):
        project.lane('docs')


def test_legacy_database_and_nonempty_target_are_preserved(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    state = tmp_path / 'old-state'
    state.mkdir()
    legacy = state / 'project.sqlite3'
    legacy.write_bytes(b'preserve prototype bytes')
    with pytest.raises(LaneError, match='explicit migration'):
        ProjectStore(state)
    with pytest.raises(LaneError, match='fresh empty'):
        ProjectStore.create(state, source)
    assert legacy.read_bytes() == b'preserve prototype bytes'
    assert not (state / DATABASE_NAME).exists()


def test_unregistered_database_collision_is_not_overwritten(separate_project):
    project = separate_project
    target = project.root / 'sectors/docs/docs_sector_v001.sqlite'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'preserve collision')
    with pytest.raises(LaneError, match='unregistered database'):
        project.lane('docs', create=True)
    assert target.read_bytes() == b'preserve collision'
    assert 'docs' not in {row['lane_id'] for row in project.lane_catalog()}

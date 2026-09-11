"""ZIP allocation bounds and compatibility through the retained Sources owner."""
from __future__ import annotations

import io
import os
import sqlite3
import struct
import zipfile

import pytest
from evidence_lane_plugin import source_sqlite
from evidence_lane_plugin.errors import EvidenceLaneError, LaneError
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    SourceCaptureBudget,
    _archive_members,
    _open_source_archive,
    archive_safety_profile,
    register_source_batch,
)
from evidence_lane_plugin.storage import ProjectStore


def archive_bytes(members, *, comment=b'', compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
        archive.comment = comment
    return output.getvalue()


def zip64(raw, *, extension=b''):
    position = raw.rfind(b'PK\x05\x06')
    end = struct.unpack_from('<4s4H2IH', raw, position)
    record = struct.pack('<4sQ2H2I4Q', b'PK\x06\x06', 44 + len(extension), 45, 45, 0, 0,
        end[3], end[4], end[5], end[6]) + extension
    locator = struct.pack('<4sIQI', b'PK\x06\x07', 0, position, 1)
    footer = struct.pack('<4s4H2IH', b'PK\x05\x06', 0, 0, 65535, 65535, 0xFFFFFFFF, 0xFFFFFFFF, end[7])
    return raw[:position] + record + locator + footer + raw[position + 22:]


def watch_allocations(monkeypatch):
    counts = []
    original = zipfile.ZipInfo
    class ObservedInfo(original):
        def __init__(self, *args, **kwargs):
            counts.append(1)
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(zipfile, 'ZipInfo', ObservedInfo)
    return counts


def test_member_limit_rejects_before_any_zipinfo_allocation(tmp_path, monkeypatch):
    path = tmp_path / 'many.zip'
    path.write_bytes(archive_bytes({f'entry-{i}.txt': b'' for i in range(5000)}))
    before = sha256_file(path)
    allocated = watch_allocations(monkeypatch)
    result = archive_safety_profile(path, {'max_members': 2})
    assert result['status'] == 'BLOCKED'
    assert result['code'] in {'SOURCE_ARCHIVE_METADATA_ENTRY_BUDGET', 'SOURCE_AUTHORITY_ARCHIVE_MEMBER_LIMIT_EXCEEDED'}
    assert allocated == [] and sha256_file(path) == before


def test_false_low_declared_count_cannot_bypass_actual_record_bound(tmp_path, monkeypatch):
    path = tmp_path / 'lying-count.zip'
    raw = bytearray(archive_bytes({'first': b'', 'second': b'', 'third': b''}))
    end = raw.rfind(b'PK\x05\x06')
    struct.pack_into('<HH', raw, end + 8, 1, 1)
    path.write_bytes(raw)
    allocated = watch_allocations(monkeypatch)
    result = archive_safety_profile(path, {'max_members': 2})
    assert result['status'] == 'BLOCKED' and allocated == []
    assert result['code'] == 'SOURCE_AUTHORITY_ARCHIVE_MEMBER_LIMIT_EXCEEDED'


def test_directory_byte_limit_precedes_directory_buffer_and_zipinfo(tmp_path, monkeypatch):
    path = tmp_path / 'large-directory.zip'
    raw = bytearray(archive_bytes({'first': b''}))
    end = raw.rfind(b'PK\x05\x06')
    struct.pack_into('<I', raw, end + 12, 64 * 1024 * 1024 + 1)
    path.write_bytes(raw)
    allocated = watch_allocations(monkeypatch)
    result = archive_safety_profile(path)
    assert result['code'] == 'SOURCE_ARCHIVE_DIRECTORY_BYTE_BUDGET' and allocated == []


@pytest.mark.parametrize('variant', ['ordinary', 'empty', 'comment', 'prefix', 'zip64', 'zip64-prefix', 'zip64-extension'])
def test_supported_archive_formats_keep_real_payload_reads(tmp_path, variant):
    expected = {} if variant == 'empty' else {'nested/report.txt': b'retained source payload'}
    raw = archive_bytes(expected, comment=b'Archive comment' if variant == 'comment' else b'')
    if variant.startswith('zip64'):
        raw = zip64(raw, extension=b'bounded ZIP64 extensible metadata' if variant == 'zip64-extension' else b'')
    if 'prefix' in variant:
        raw = b'MZ-illustrative-container-prefix\x00' + raw
    # Establish that the fixture is accepted by this actual standard runtime.
    with zipfile.ZipFile(io.BytesIO(raw)) as original:
        assert {info.filename: original.read(info) for info in original.infolist()} == expected
    path = tmp_path / (variant + '.zip')
    path.write_bytes(raw)
    before = sha256_file(path)
    with _open_source_archive(path, expected_sha256=before, expected_size=len(raw)) as archive:
        assert {info.filename: archive.read(info) for info in archive.infolist()} == expected
    assert sha256_file(path) == before


@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_chunked_container_view_preserves_compression_readers(tmp_path, compression):
    payload = bytes(range(256)) * 16_384
    raw = archive_bytes({'large.bin': payload}, compression=compression)
    with _open_source_archive(tmp_path / 'not-written.zip', content=raw) as archive:
        assert archive.read('large.bin') == payload
    assert not (tmp_path / 'not-written.zip').exists()


@pytest.mark.parametrize('corruption', ['multipart', 'truncated', 'count', 'offset'])
def test_malformed_archive_rejected_before_zipinfo_allocation(tmp_path, monkeypatch, corruption):
    raw = bytearray(archive_bytes({'a.txt': b'a'}))
    end = raw.rfind(b'PK\x05\x06')
    if corruption == 'multipart':
        struct.pack_into('<H', raw, end + 4, 1)
    elif corruption == 'truncated':
        raw = raw[:-3]
    elif corruption == 'count':
        struct.pack_into('<HH', raw, end + 8, 2, 2)
    else:
        struct.pack_into('<I', raw, end + 16, len(raw) + 1)
    path = tmp_path / 'invalid.zip'
    path.write_bytes(raw)
    allocated = watch_allocations(monkeypatch)
    assert archive_safety_profile(path)['status'] == 'MISMATCH'
    assert allocated == []


@pytest.mark.parametrize('kwargs', [{'max_members': True}, {'max_members': -1}, {'max_members': 250_001},
    {'max_entries': True}, {'max_entries': 500_001}, {'max_directory_bytes': 0},
    {'max_directory_bytes': 64 * 1024 * 1024 + 1}])
def test_invalid_metadata_budgets_fail_without_opening_a_file(tmp_path, kwargs):
    with pytest.raises(EvidenceLaneError, match='SOURCE_ARCHIVE_BUDGET_INVALID'), _open_source_archive(tmp_path / 'absent.zip', **kwargs):
        raise AssertionError('Invalid budget opened an archive')


def test_capture_remaining_entry_limit_precedes_second_archive_allocation(tmp_path, monkeypatch):
    budget = SourceCaptureBudget(lambda path: None, lambda: None, allow_archives=True, max_archive_members=1)
    raw = archive_bytes({'one.txt': b'retained'})
    first = tmp_path / 'one.zip'
    _archive_members(first, {}, capture=budget, content=raw)
    allocated = watch_allocations(monkeypatch)
    with pytest.raises(LaneError) as failure:
        _archive_members(tmp_path / 'two.zip', {}, capture=budget, content=raw)
    assert getattr(failure.value, 'code', None) == 'SOURCE_CAPTURE_ARCHIVE_BUDGET'
    assert allocated == []


def test_excluded_members_are_not_decompressed(tmp_path, monkeypatch):
    raw = archive_bytes({'visible.txt': b'visible', '.env': b'fixture secret', '../escape': b'unsafe'})
    opened = []
    original = zipfile.ZipFile.open
    def observed(self, name, *args, **kwargs):
        opened.append(name.filename)
        return original(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', observed)
    rows = _archive_members(tmp_path / 'not-written.zip', {}, content=raw)
    assert opened == ['visible.txt']
    assert sum(row['policy_state'] == 'EXCLUDED' for row in rows) == 2


def test_changed_live_metadata_cannot_expand_constructor_allocation(tmp_path, monkeypatch):
    path = tmp_path / 'changed.zip'
    path.write_bytes(archive_bytes({'one.txt': b'original'}))
    replacement = archive_bytes({f'entry-{i}.txt': b'' for i in range(5000)})
    original = zipfile.ZipFile
    observed = []
    def changed(*args, **kwargs):
        path.write_bytes(replacement)
        archive = original(*args, **kwargs)
        observed.append(len(archive.filelist))
        return archive
    monkeypatch.setattr(zipfile, 'ZipFile', changed)
    with pytest.raises(EvidenceLaneError, match='SOURCE_ARCHIVE_CHANGED_DURING_READ'), _open_source_archive(path, max_members=2) as archive:
        assert archive.namelist() == ['one.txt']
    assert observed == [1] and path.read_bytes() == replacement


def registered_sqlite_archive(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    database = source / 'source.sqlite'
    connection = sqlite3.connect(database)
    try:
        connection.execute('CREATE TABLE retained(value INTEGER)')
        connection.commit()
    finally:
        connection.close()
    raw = archive_bytes({'source.sqlite': database.read_bytes(), 'other.txt': b'before'})
    path = source / 'selected.zip'
    path.write_bytes(raw)
    lane = ProjectStore.create(tmp_path / 'state', source).lane('sources')
    batch = register_source_batch(lane, [SourceAuthoritySpec(str(path), 1, 'custom')])['batch_id']
    return lane, batch, path, database.read_bytes()


def test_registered_container_change_rejected_even_when_sqlite_member_matches(tmp_path, monkeypatch):
    lane, batch, path, payload = registered_sqlite_archive(tmp_path)
    path.write_bytes(archive_bytes({'source.sqlite': payload, 'other.txt': b'after!'}))
    allocated = watch_allocations(monkeypatch)
    asset = source_sqlite._registered_assets(lane, batch)[0]
    with pytest.raises(EvidenceLaneError, match='SOURCE_SQLITE_ARCHIVE_CONTAINER_CHANGED'):
        source_sqlite._inspect_asset(asset, max_embedded_member_bytes=8_388_608, exact_count_max_database_bytes=8_388_608)
    assert allocated == []


def test_archive_hash_rechecked_after_actual_sqlite_inspection(tmp_path, monkeypatch):
    lane, batch, path, payload = registered_sqlite_archive(tmp_path)
    replacement = archive_bytes({'source.sqlite': payload, 'other.txt': b'after!'})
    original = source_sqlite._inspect_connection
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        stamp = path.stat()
        path.write_bytes(replacement)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        return result
    monkeypatch.setattr(source_sqlite, '_inspect_connection', changed)
    result = source_sqlite._collect_sqlite_inspections(source_sqlite._registered_assets(lane, batch),
        max_embedded_member_bytes=8_388_608, exact_count_max_database_bytes=8_388_608,
        batch_budget=source_sqlite.SQLiteBatchBudget())
    assert all(row['receipt']['status'] != 'PASS' and row['schema_objects'] == [] for row in result['inspections'].values())

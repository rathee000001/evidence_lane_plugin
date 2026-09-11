"""Bounded ZIP observation preserves the original Sources archive identity."""

import io
import os
import zipfile

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    SourceCaptureBudget,
    _archive_members,
    freeze_source_authority,
)


def archive_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def capture(**kwargs):
    return SourceCaptureBudget(lambda path: None, lambda: None, allow_archives=True, **kwargs)


def test_archive_identity_and_exclusions_match_existing_owner(tmp_path):
    path = tmp_path / 'fixture.zip'
    path.write_bytes(archive_bytes({'Report/report.json': b'{}', '../outside': b'unsafe', '.git/config': b'private'}))
    spec = SourceAuthoritySpec(str(path), 1, 'power_bi')
    original = freeze_source_authority(spec)
    bounded = capture()
    assert freeze_source_authority(spec, capture=bounded) == original
    assert original.kind == 'zip' and original.included_member_count == 1 and original.excluded_member_count == 2
    assert len(bounded.identities) == 1 and bounded.archive_observations[path] == {'entries': 3, 'expanded_bytes': 15}
    assert not (tmp_path / 'Report').exists()


def test_default_capture_still_refuses_archives(tmp_path):
    path = tmp_path / 'fixture.zip'
    path.write_bytes(archive_bytes({'one.txt': b'hello'}))
    with pytest.raises(LaneError) as error:
        freeze_source_authority(SourceAuthoritySpec(str(path), 1, 'power_bi'),
            capture=SourceCaptureBudget(lambda path: None, lambda: None))
    assert error.value.code == 'SOURCE_CAPTURE_LOCAL_SCOPE'


@pytest.mark.parametrize('limit', ['entries', 'member', 'expanded', 'raw'])
def test_proposed_zip_budget_failure_does_not_write(tmp_path, limit):
    path = tmp_path / 'new.zip'
    raw = archive_bytes({'a': b'12345', 'b': b'67890'})
    budget = capture(**{'entries': {'max_archive_members': 1}, 'member': {'max_archive_member_bytes': 4},
        'expanded': {'max_archive_total_bytes': 9}, 'raw': {'max_file_bytes': len(raw) - 1}}[limit])
    with pytest.raises(LaneError) as error:
        _archive_members(path, {}, capture=budget, content=raw)
    assert error.value.code == ('SOURCE_CAPTURE_BYTE_BUDGET' if limit == 'raw' else 'SOURCE_CAPTURE_ARCHIVE_BUDGET')
    assert not path.exists() and list(tmp_path.iterdir()) == []


def test_archive_total_budget_covers_complete_parent_and_deduplicates_overlap(tmp_path):
    one, two = tmp_path / 'one.zip', tmp_path / 'two.zip'
    raw = archive_bytes({'member.txt': b'123456'})
    one.write_bytes(raw)
    two.write_bytes(raw)
    budget = capture(max_archive_total_bytes=10)
    first = SourceAuthoritySpec(str(one), 1, 'power_bi')
    assert freeze_source_authority(first, capture=budget) == freeze_source_authority(first, capture=budget)
    with pytest.raises(LaneError) as error:
        freeze_source_authority(SourceAuthoritySpec(str(two), 2, 'power_bi'), capture=budget)
    assert error.value.code == 'SOURCE_CAPTURE_ARCHIVE_BUDGET'


def test_archive_rehash_detects_same_size_timestamp_byte_change(tmp_path, monkeypatch):
    from evidence_lane_plugin import source_authority
    path = tmp_path / 'fixture.zip'
    first, second = archive_bytes({'a': b'first'}), archive_bytes({'a': b'other'})
    assert len(first) == len(second)
    path.write_bytes(first)
    original = source_authority._archive_members
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        stamp = path.stat()
        path.write_bytes(second)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        return result
    monkeypatch.setattr(source_authority, '_archive_members', changed)
    with pytest.raises(LaneError) as error:
        freeze_source_authority(SourceAuthoritySpec(str(path), 1, 'power_bi'), capture=capture())
    assert error.value.code == 'SOURCE_AUTHORITY_CHANGED_DURING_READ'


def test_archive_chunk_reads_check_operation_cancellation(tmp_path):
    path = tmp_path / 'fixture.zip'
    raw = archive_bytes({'a': b'x' * 180_000})
    calls = []
    def check():
        calls.append(True)
        if len(calls) == 5:
            raise LaneError('FIXTURE_CANCELLED', 'Cancelled during bounded member reading.')
    budget = SourceCaptureBudget(lambda path: None, check, allow_archives=True)
    with pytest.raises(LaneError) as error:
        _archive_members(path, {}, capture=budget, content=raw)
    assert error.value.code == 'FIXTURE_CANCELLED'
    assert len(calls) == 5 and not path.exists()

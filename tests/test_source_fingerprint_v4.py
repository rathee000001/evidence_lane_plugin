"""Real filesystem and Git evidence for the adapted fingerprint owner."""
from __future__ import annotations

import hashlib
import os

import pytest
from evidence_lane_plugin.bounded_io import IOBudget
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.source_fingerprint import (
    measure_worktree_paths,
    staged_index_file_manifest,
    tracked_worktree_file_manifest,
)

from tests.conftest import git


def test_selected_scope_includes_actual_file_deletion_and_untracked_only_if_selected(tmp_path):
    (tmp_path / 'present.bin').write_bytes(b'\x00\xffmeasured')
    (tmp_path / 'untracked.txt').write_bytes(b'explicitly selected')
    (tmp_path / 'outside.txt').write_bytes(b'not selected')
    result = measure_worktree_paths(tmp_path, ['present.bin', 'deleted.bin', 'untracked.txt'])
    rows = {row['path']: row for row in result['entries']}
    assert set(rows) == {'present.bin', 'deleted.bin', 'untracked.txt'}
    assert rows['present.bin']['current_sha256'] == hashlib.sha256(b'\x00\xffmeasured').hexdigest().upper()
    assert rows['deleted.bin']['state'] == 'MISSING_WORKTREE_PATH'
    assert not result['filesystem_snapshot_attested'] and not result['source_bytes_mutated']


def test_assume_unchanged_cannot_hide_changed_worktree_bytes(source_repository):
    path = source_repository / 'README.md'
    before = tracked_worktree_file_manifest(source_repository)
    original_stat = path.stat()
    git(source_repository, 'update-index', '--assume-unchanged', 'README.md')
    path.write_bytes(b'X' * path.stat().st_size)
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert git(source_repository, 'status', '--porcelain') == ''
    after = tracked_worktree_file_manifest(source_repository)
    assert before['path_set_sha256'] == after['path_set_sha256']
    assert before['file_manifest_sha256'] != after['file_manifest_sha256']
    actual = next(row for row in after['entries'] if row['path'] == 'README.md')
    assert actual['current_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest().upper()


@pytest.mark.parametrize('paths', [['../outside'], ['/absolute'], ['dir/../a'], ['a//b'], ['a\\b'], ['a:b'], ['a', 'a'], ['.']])
def test_noncanonical_or_duplicate_paths_are_refused_before_content_read(tmp_path, paths, monkeypatch):
    import evidence_lane_plugin.source_fingerprint as owner
    monkeypatch.setattr(owner.os, 'open', lambda *_a, **_k: pytest.fail('content descriptor must not open'))
    with pytest.raises(LaneError) as error:
        measure_worktree_paths(tmp_path, paths)
    assert error.value.code == 'SOURCE_FINGERPRINT_PATH_INVALID'


@pytest.mark.parametrize('budget,code', [
    ({'max_file_count': 1}, 'SOURCE_FINGERPRINT_PATH_BUDGET'),
    ({'max_file_bytes': 1}, 'BOUNDED_IO_FILE_BYTES_EXCEEDED'),
    ({'max_aggregate_bytes': 3}, 'BOUNDED_IO_AGGREGATE_BUDGET_EXCEEDED'),
])
def test_file_count_individual_and_aggregate_limits_are_enforced(tmp_path, budget, code):
    for name in ('a', 'b'):
        (tmp_path / name).write_bytes(b'ab')
    with pytest.raises(LaneError) as error:
        measure_worktree_paths(tmp_path, ['a', 'b'], budget=IOBudget(**budget))
    assert error.value.code == code


def test_cancel_boundary_during_chunk_hashing_closes_descriptor(tmp_path, monkeypatch):
    import evidence_lane_plugin.source_fingerprint as owner
    (tmp_path / 'large.bin').write_bytes(b'x' * 200_000)
    calls, closed = [], []
    original_close = owner.os.close
    def tick():
        calls.append(1)
        if len(calls) == 3:
            raise LaneError('TEST_SOURCE_CANCELLED', 'Bounded cancellation fixture')
    def close(descriptor):
        closed.append(descriptor)
        return original_close(descriptor)
    monkeypatch.setattr(owner.os, 'close', close)
    with pytest.raises(LaneError) as error:
        measure_worktree_paths(tmp_path, ['large.bin'], tick=tick)
    assert error.value.code == 'TEST_SOURCE_CANCELLED' and len(closed) == 1


def test_opened_descriptor_must_match_preflight_before_any_content_read(tmp_path, monkeypatch):
    import evidence_lane_plugin.source_fingerprint as owner
    (tmp_path / 'selected').write_bytes(b'aaaa')
    (tmp_path / 'different').write_bytes(b'bbbb')
    original_open = owner.os.open
    monkeypatch.setattr(owner.os, 'open', lambda _path, flags: original_open(tmp_path / 'different', flags))
    monkeypatch.setattr(owner.os, 'read', lambda *_a: pytest.fail('unmatched descriptor must not be read'))
    with pytest.raises(LaneError) as error:
        measure_worktree_paths(tmp_path, ['selected'])
    assert error.value.code == 'SOURCE_FINGERPRINT_CHANGED'


def test_authorization_runs_before_read_and_at_every_chunk(tmp_path):
    (tmp_path / 'file').write_bytes(b'x' * 200_000)
    observed = []
    def authorize(path):
        observed.append(path)
        if len(observed) == 3:
            raise LaneError('TEST_SOURCE_GRANT_REVOKED', 'Bounded revocation fixture')
    with pytest.raises(LaneError) as error:
        measure_worktree_paths(tmp_path, ['file'], authorize=authorize)
    assert error.value.code == 'TEST_SOURCE_GRANT_REVOKED'
    assert observed == [tmp_path / 'file'] * 3


def test_final_path_replacement_is_detected(tmp_path, monkeypatch):
    import evidence_lane_plugin.source_fingerprint as owner
    selected = tmp_path / 'file'
    selected.write_bytes(b'aaaa')
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'bbbb')
    original_close = owner.os.close
    def close(descriptor):
        result = original_close(descriptor)
        os.replace(replacement, selected)
        return result
    monkeypatch.setattr(owner.os, 'close', close)
    with pytest.raises(LaneError) as error:
        measure_worktree_paths(tmp_path, ['file'])
    assert error.value.code == 'SOURCE_FINGERPRINT_CHANGED'


def test_elapsed_budget_applies_during_read(tmp_path, monkeypatch):
    import evidence_lane_plugin.source_fingerprint as owner
    (tmp_path / 'file').write_bytes(b'data')
    ticks = iter([0, 0, 61])
    monkeypatch.setattr(owner.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(LaneError) as error:
        measure_worktree_paths(tmp_path, ['file'])
    assert error.value.code == 'SOURCE_FINGERPRINT_TIME_BUDGET'


def test_index_change_during_worktree_observation_is_not_sealed(source_repository, monkeypatch):
    import evidence_lane_plugin.source_fingerprint as owner
    original = owner.measure_worktree_paths
    def measure(*args, **kwargs):
        value = original(*args, **kwargs)
        git(source_repository, 'update-index', '--force-remove', 'README.md')
        return value
    monkeypatch.setattr(owner, 'measure_worktree_paths', measure)
    with pytest.raises(LaneError) as error:
        tracked_worktree_file_manifest(source_repository)
    assert error.value.code == 'SOURCE_FINGERPRINT_INDEX_CHANGED'


def test_staged_blobs_are_independent_of_missing_worktree_parents(source_repository):
    before = staged_index_file_manifest(source_repository)
    for path in (source_repository / 'src').iterdir():
        path.unlink()
    (source_repository / 'src').rmdir()
    assert staged_index_file_manifest(source_repository) == before


def test_large_binary_index_blob_exceeds_old_command_output_default_without_truncation(source_repository):
    raw = bytes(range(256)) * 20_000
    (source_repository / 'large.bin').write_bytes(raw)
    git(source_repository, 'add', 'large.bin')
    value = staged_index_file_manifest(source_repository)
    row = next(item for item in value['entries'] if item['path'] == 'large.bin')
    assert row['staged_bytes'] == len(raw)
    assert row['staged_sha256'] == hashlib.sha256(raw).hexdigest().upper()


def test_conflicted_index_entries_are_explicitly_refused(source_repository):
    import subprocess
    one = git(source_repository, 'hash-object', 'README.md')
    patch = f'100644 {one} 1\tconflicted.txt\n100644 {one} 2\tconflicted.txt\n'
    subprocess.run(['git', '-C', str(source_repository), 'update-index', '--index-info'],
        input=patch.encode(), check=True, capture_output=True)
    with pytest.raises(LaneError) as error:
        tracked_worktree_file_manifest(source_repository)
    assert error.value.code == 'TRACKED_SOURCE_FINGERPRINT_INDEX_INVALID'


def test_case_aliases_follow_the_platform_path_identity(tmp_path):
    (tmp_path / 'a').write_bytes(b'one')
    if os.name == 'nt':
        with pytest.raises(LaneError) as error:
            measure_worktree_paths(tmp_path, ['a', 'A'])
        assert error.value.code == 'SOURCE_FINGERPRINT_PATH_INVALID'
    else:
        (tmp_path / 'A').write_bytes(b'two')
        assert measure_worktree_paths(tmp_path, ['a', 'A'])['path_count'] == 2


def test_native_directory_link_cannot_redirect_worktree_fingerprint(tmp_path, monkeypatch):
    import subprocess

    import evidence_lane_plugin.source_fingerprint as owner
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (target / 'file').write_bytes(b'outside the selected root')
    link = source / 'redirect'
    if os.name == 'nt':
        created = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
            capture_output=True, check=False, timeout=10)
        assert created.returncode == 0, created.stderr
    else:
        os.symlink(target, link, target_is_directory=True)
    try:
        assert link.resolve() == target.resolve() and target.resolve().is_relative_to(tmp_path.resolve())
        monkeypatch.setattr(owner.os, 'open', lambda *_a, **_k: pytest.fail('redirected content must not open'))
        with pytest.raises(LaneError) as error:
            measure_worktree_paths(source, ['redirect/file'])
        assert error.value.code == 'LINKED_STORAGE_PATH'
    finally:
        if os.name == 'nt':
            link.rmdir()
        else:
            link.unlink()

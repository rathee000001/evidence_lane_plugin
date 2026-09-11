"""Directory identity remains complete while parser inputs stay narrowly bounded."""
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.source_routing import SourceRouteGuard

from .test_code_profile_v4 import code_system as code_system  # noqa: PLC0414
from .test_code_profile_v4 import create_plan
from .test_source_routing_v4 import enter, finished, registered
from .test_source_selection_v4 import quiescent, registered_members


def many_members(source, *, count=520):
    other = source / 'other'
    other.mkdir()
    for index in range(count):
        (other / f'module_{index:04}.py').write_bytes(b'pass\n')
    return other


def no_admitted_job(store):
    with store.lane('plan').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='delta_runs'").fetchone() is None


@pytest.mark.parametrize('git', [False, True])
def test_one_file_from_more_than_512_registered_members_avoids_unselected_files(code_system, monkeypatch, git):
    source = code_system[1].source_root
    other = many_members(source)
    if git:
        subprocess.run(['git', '-C', str(source), 'init', '-b', 'main'], check=True, capture_output=True)
    receipt = registered(code_system, ['.'])
    _, members = registered_members(code_system, receipt)
    assert len(members) == 523
    create_plan(code_system)
    actual_stat = Path.stat
    def stat(path, **kwargs):
        assert not path.is_relative_to(other), 'A narrow parser operation inspected an unselected source subtree.'
        return actual_stat(path, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    result = finished(code_system, enter(code_system, receipt['route_id'], arguments={'paths': ['app.py']}))
    quiescent(code_system)
    assert result['source_route']['input_file_count'] == result['result']['result']['files'] == 1
    selection = result['tool_execution']['env_uop']['selection_context']['source_scope']
    assert selection['route_id'] == receipt['route_id']
    assert selection['lane_id'] == 'local_code'
    assert selection['data_locality'] == 'verified_local_source_scope'


def test_full_oversized_parser_scope_is_still_rejected_before_admission(code_system):
    many_members(code_system[1].source_root)
    receipt = registered(code_system, ['.'])
    create_plan(code_system)
    assert enter(code_system, receipt['route_id']).error.code == 'SOURCE_ROUTE_INPUT_BUDGET'
    no_admitted_job(code_system[1])


def test_parent_source_bytes_do_not_consume_a_narrow_parser_input_budget(code_system):
    source = code_system[1].source_root
    with (source / 'large.txt').open('wb') as stream:
        stream.truncate(SourceRouteGuard.MAX_BYTES + 1)
    receipt = registered(code_system, ['.'])
    create_plan(code_system)
    result = finished(code_system, enter(code_system, receipt['route_id'], arguments={'paths': ['app.py']}))
    quiescent(code_system)
    assert result['source_route']['input_bytes'] == (source / 'app.py').stat().st_size


def test_unselected_member_corruption_still_invalidates_the_complete_parent(code_system):
    engine, store, _ = code_system
    many_members(store.source_root)
    receipt = registered(code_system, ['.'])
    source_id, _ = registered_members(code_system, receipt)
    with engine.project_work.mutation(store) as lease, lease.transaction('sources') as connection:
        connection.execute('UPDATE source_member SET sha256=? WHERE object_id=? AND member_path=?',
            ('0' * 64, source_id, 'other/module_0519.py'))
    create_plan(code_system)
    response = enter(code_system, receipt['route_id'], arguments={'paths': ['app.py']})
    assert response.error.code == 'SOURCE_ROUTE_INTEGRITY'
    no_admitted_job(store)


@pytest.mark.parametrize('limit', ['MAX_METADATA_FILES', 'MAX_METADATA_BYTES'])
def test_complete_metadata_has_its_own_preallocation_bound(code_system, monkeypatch, limit):
    receipt = registered(code_system, ['.'])
    create_plan(code_system)
    monkeypatch.setattr(SourceRouteGuard, limit, 1)
    assert enter(code_system, receipt['route_id'], arguments={'paths': ['app.py']}).error.code == 'SOURCE_ROUTE_METADATA_BUDGET'
    no_admitted_job(code_system[1])


def test_metadata_budget_counts_utf8_bytes_and_combines_selected_directories(code_system, monkeypatch):
    store = code_system[1]
    left, right = store.source_root / 'left', store.source_root / 'right'
    left.mkdir()
    right.mkdir()
    (left / '漢.py').write_bytes(b'pass\n')
    (right / '字.py').write_bytes(b'pass\n')
    receipt = registered(code_system, ['left', 'right'], overrides={str(left): 'local_code', str(right): 'local_code'})
    with store.lane('sources').connection(read_only=True) as connection:
        characters = connection.execute('SELECT SUM(length(member_path)+length(sha256)+length(member_kind)'
            '+length(policy_state)+length(policy_reason)) FROM source_member').fetchone()[0]
    create_plan(code_system)
    monkeypatch.setattr(SourceRouteGuard, 'MAX_METADATA_BYTES', characters)
    response = enter(code_system, receipt['route_id'], arguments={'paths': ['left/漢.py', 'right/字.py']}, ordinals=(1, 2))
    assert response.error.code == 'SOURCE_ROUTE_METADATA_BUDGET'
    no_admitted_job(store)

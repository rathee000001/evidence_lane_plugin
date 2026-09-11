"""Source inputs cannot select authority writes; each lane owns separate storage."""
from pathlib import Path, PurePosixPath

import pytest
from evidence_lane_plugin.lanes import (
    AUTHORITY_LANE_IDS,
    CANONICAL_LANE_IDS,
    LANE_REGISTRY,
    RETIRED_LANE_IDS,
    SCHEMA_OWNER_LANES,
    LaneRegistryError,
    catalog,
    get_lane,
    lane_artifact_contract,
    lane_schema_asset,
    resolve_lane_id,
    route_batch,
    route_source,
)


@pytest.mark.parametrize('path,expected', [
    ('plan.md', 'docs'), ('conversation.json', 'data'), ('analysis.py', 'local_code'),
    ('policy.sql', 'local_code'), ('meeting.docx', 'docs'), ('brain.sqlite', 'custom'),
    ('project.zip', 'custom'), ('package.json', 'local_code'), ('tables.hyper', 'tableau'),
    ('report.pbix', 'power_bi'), ('tasks.mpp', 'custom'), ('database.accdb', 'custom'),
    ('slides.pptm', 'ppt'), ('notes.one', 'custom'), ('mail.msg', 'custom'),
    ('drawing.vsdx', 'custom'), ('brochure.pub', 'custom'), ('book.xlsm', 'data_excel'),
    ('report.pdf', 'pdf_ocr'), ('clip.mp4', 'images_ocr'), ('rows.parquet', 'data'),
])
def test_source_routes_are_sector_only(path, expected):
    assert route_source(path, code_mode='local_code') == expected
    assert get_lane(expected).kind == 'sector'


def test_authority_identity_and_explicit_intake_do_not_collapse():
    assert set(AUTHORITY_LANE_IDS) == {'plan', 'chat_lineage', 'canon', 'memory', 'learning', 'sources', 'receipts', 'universe'}
    for lane in AUTHORITY_LANE_IDS:
        assert get_lane(lane).folder == f'authorities/{lane}'
        with pytest.raises(LaneRegistryError, match='owning workflow'):
            route_source('input.md', code_mode='local_code', explicit_lane=lane)
    for lane in RETIRED_LANE_IDS:
        with pytest.raises(LaneRegistryError, match='Unknown or removed'):
            resolve_lane_id(lane)
    assert route_source('paper.pdf', code_mode='local_code', explicit_lane='research') == 'research'
    assert resolve_lane_id('sqlite') == 'custom'
    assert resolve_lane_id('project_memory') == 'memory'


def test_code_selection_preserves_git_mode_without_guessing():
    with pytest.raises(LaneRegistryError, match='explicit'):
        resolve_lane_id('code')
    assert route_batch(['b.py', 'a.ts', 'b.py'], code_mode='github_code') == {'a.ts': 'github_code', 'b.py': 'github_code'}
    assert route_source('unknown.bin', code_mode='local_code', explicit_lane='code') == 'local_code'
    with pytest.raises(LaneRegistryError):
        route_source('a.py', code_mode='inferred')


def test_separate_physical_ownership_and_optional_artifacts():
    paths = set()
    for lane in LANE_REGISTRY.values():
        path = PurePosixPath(lane.database_relative_path)
        assert path.parent == PurePosixPath(lane.folder)
        assert path.name.endswith('.sqlite')
        assert str(path).casefold() not in paths
        paths.add(str(path).casefold())
        assert PurePosixPath(lane.files_relative_path).parent == path.parent
        assert PurePosixPath(lane.schema_history_relative_path).parent == path.parent
        artifact = lane_artifact_contract(lane.canonical_lane_id)
        assert artifact['required_roles'] == [{'role_id': 'sqlite_authority', 'path': lane.sqlite_filename}]
        assert artifact['graph_exports']['both_required'] is False
    assert get_lane('local_code').sqlite_filename == 'local_code_sector_v001.sqlite'
    assert get_lane('plan').sqlite_filename == 'plan_authority_v001.sqlite'
    assert len(paths) == len(CANONICAL_LANE_IDS)
    assert SCHEMA_OWNER_LANES['access'] == 'receipts'
    assert 'msaccess' not in SCHEMA_OWNER_LANES
    assert lane_artifact_contract('plan')['pointer']['meaning'] != lane_artifact_contract('memory')['pointer']['meaning']


def test_catalog_is_detached_and_does_not_claim_runtime_qualification():
    package = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    for lane in catalog():
        assert lane['qualification'] == 'definition_only_until_owning_workflow_verified'
        assert all((package / source).is_file() for source in lane['source_origins'])
    one = lane_schema_asset('docs')
    one['tables'].clear()
    assert lane_schema_asset('docs')['tables']
    assert lane_schema_asset('plan')['database_relative_path'] != lane_schema_asset('chat_lineage')['database_relative_path']

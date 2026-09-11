"""The three selected Office applications own the executable product surface."""
import json
from pathlib import Path

from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.lanes import RETIRED_LANE_IDS, SECTOR_LANE_IDS
from evidence_lane_plugin.sector_support import IMPLEMENTED_SECTORS
from evidence_lane_plugin.tool_catalog import shared_requirements


def test_only_selected_office_apps_have_active_lanes_and_actions(tmp_path):
    excluded = {'onenote', 'access', 'visio', 'outlook', 'project', 'publisher'}
    assert excluded <= RETIRED_LANE_IDS
    assert not excluded.intersection(SECTOR_LANE_IDS)
    assert not excluded.intersection(IMPLEMENTED_SECTORS)
    with Engine(tmp_path / 'runtime') as engine:
        actions = engine.registry.schemas()
        assert not any(row['profile'] in excluded for row in actions)
        assert {'document_index', 'spreadsheet_index', 'presentation_index'} <= {row['name'] for row in actions}
        assert not any(row['view_id'].split('.')[0] in excluded for row in engine.registry.view_schemas())


def test_removed_office_tools_are_not_shared_installation_requirements():
    requirements = shared_requirements()
    ids = {row['tool_id'] for row in requirements['entries']}
    assert not {'OneNote_Parser', 'Jackcess', 'OpenJDK'}.intersection(ids)
    assert {'LibreOffice', 'openpyxl', 'DOCX_OpenXML', 'PPTX_OpenXML'} <= ids
    root = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    native = json.loads((root / 'toolchains/native-tools.v4.json').read_text(encoding='utf-8'))
    assert not {'onenote_parser', 'openjdk', 'jackcess_libraries'}.intersection(row['tool_id'] for row in native['tools'])
    assert not requirements['full_bundle_ready']


def test_automatic_routing_skips_retired_office_lanes():
    from evidence_lane_plugin.lanes import route_source
    for filename, lane in [('source.py', 'local_code'), ('slides.pptx', 'ppt'), ('book.xlsx', 'data_excel'),
                           ('model.hyper', 'tableau'), ('report.pbix', 'power_bi'), ('report.pdf', 'pdf_ocr')]:
        assert route_source(filename, code_mode='local_code') == lane

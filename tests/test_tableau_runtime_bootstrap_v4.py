"""A native Hyper worker must use the engine runtime without ambient import paths."""
import base64

import pytest
from evidence_lane_plugin.tableau_hyper import inspect_hyper, invoke_hyper


@pytest.mark.parametrize('ambient', ['absent', 'poisoned'])
def test_native_hyper_worker_uses_engine_import_roots(tmp_path, monkeypatch, ambient):
    shadow = tmp_path / 'untrusted-pythonpath'
    package = shadow / 'evidence_lane_plugin'
    package.mkdir(parents=True)
    marker = shadow / 'imported.txt'
    (package / '__init__.py').write_text(
        'from pathlib import Path\n'
        f'Path({str(marker)!r}).write_text("wrong package imported")\n'
        'raise RuntimeError("ambient package must not be imported")\n', encoding='utf-8')
    monkeypatch.delenv('PYTHONPATH', raising=False)
    if ambient == 'poisoned':
        monkeypatch.setenv('PYTHONPATH', str(shadow))
    response = invoke_hyper({'operation': 'generate', 'engine_import_roots': [str(shadow)],
        'spec': {'logical_name': 'fixture.hyper', 'tables': [
            {'schema_name': 'Extract', 'name': 'Values', 'columns': [
                {'name': 'value', 'type': 'big_int', 'nullable': True}], 'rows': [[7], [None]]}]}})
    content = base64.b64decode(response['content_base64'], validate=True)
    assert content and response['evidence']['engine'] == 'tableauhyperapi'
    files, evidence = inspect_hyper([('fixture.hyper', content)], max_rows_per_table=1)
    assert evidence == response['evidence']
    assert files[0]['collections']['hyper_table'][0]['row_count'] == 2
    assert len(files[0]['collections']['hyper_row']) == 1
    assert not marker.exists()

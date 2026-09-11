"""The original owning generator must keep packaged lane contracts current."""
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/evidence-lane-plugin'


def _compiler(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN / 'scripts'))
    return importlib.import_module('regenerate_compact_lane_schema_registry')


def test_import_is_read_only_and_root_wrapper_has_the_same_owner(monkeypatch):
    from evidence_lane_plugin.build import package_contents

    before = package_contents(PLUGIN)
    compiler = _compiler(monkeypatch)
    importlib.reload(compiler)
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    wrapper = importlib.import_module('generate_lane_contracts')
    importlib.reload(wrapper)
    assert wrapper.exports is compiler.exports and wrapper.generate is compiler.generate
    assert 'evidence_lane_plugin.lane_engine' not in sys.modules
    assert package_contents(PLUGIN) == before


def test_stale_lane_check_preserves_bytes_and_explicit_refresh_repairs_them(tmp_path, monkeypatch):
    compiler = _compiler(monkeypatch)
    compiler.generate(root=tmp_path)
    path = tmp_path / 'schemas/lane-schema-registry.v4.json'
    current = json.loads(path.read_bytes())
    sources = next(row for row in current['lanes'] if row['lane_id'] == 'sources')
    assert {'customlanes_contracts', 'customlanes_current'} <= set(sources['tables'])
    sources['tables'].remove('customlanes_current')
    path.write_text(json.dumps(current), encoding='utf-8')
    before = {item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in path.parent.iterdir()}
    with pytest.raises(RuntimeError, match='lane-schema-registry.v4.json'):
        compiler.generate(root=tmp_path, check=True)
    assert {item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in path.parent.iterdir()} == before
    assert compiler.generate(root=tmp_path) == ['lane-schema-registry.v4.json']
    assert compiler.generate(root=tmp_path, check=True) == []


def test_complete_package_check_detects_a_stale_outer_lane_contract(tmp_path, monkeypatch):
    compiler = _compiler(monkeypatch)
    compiler.generate(root=tmp_path)
    path = tmp_path / 'schemas/lane-registry.v4.json'
    stale = json.loads(path.read_bytes())
    next(row for row in stale['lanes'] if row['canonical_lane_id'] == 'sources')['schema_contract'].remove('customlanes_contracts')
    path.write_text(json.dumps(stale), encoding='utf-8')
    before = path.read_bytes()
    package = importlib.import_module('generate_package_surface_projections')
    # The existing authority/sector checks still verify their real current
    # owners. Only the selected outer schema destination is deliberately stale.
    monkeypatch.setattr(package, 'PLUGIN_ROOT', tmp_path)
    with pytest.raises(RuntimeError, match='Lane contracts require regeneration: lane-registry.v4.json'):
        package.generate(check=True)
    assert path.read_bytes() == before

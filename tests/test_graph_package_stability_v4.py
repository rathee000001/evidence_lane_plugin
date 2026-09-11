import hashlib
import os
from pathlib import Path

from evidence_lane_plugin.engine import Engine


def test_all_current_lane_packages_ignore_optional_runtime_graph_observations(tmp_path, monkeypatch):
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    monkeypatch.syspath_prepend(str(plugin / 'scripts'))
    import evidence_lane_plugin.graph_pipeline as pipeline
    import regenerate_authority_packages as authorities
    import regenerate_sector_packages as sectors

    def generated(stage):
        policy = plugin / 'authorities/session_authority/installation-layout.v4.json'
        staged_policy = stage / 'authorities/session_authority/installation-layout.v4.json'
        staged_policy.parent.mkdir(parents=True, exist_ok=True)
        staged_policy.write_bytes(policy.read_bytes())
        monkeypatch.setattr(authorities, 'PLUGIN', stage)
        monkeypatch.setattr(sectors, 'PLUGIN', stage)
        with Engine(tmp_path / ('engine-' + stage.name)) as engine:
            authorities.generate(engine.registry)
            sectors.generate(engine.registry)
        return {path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in stage.rglob('*') if path.is_file()}

    monkeypatch.delenv('EVIDENCE_LANE_STUDIO_ROOT', raising=False)
    monkeypatch.delenv('EVIDENCE_LANE_HOST_PROFILE', raising=False)
    first = generated(tmp_path / 'first')

    def forbidden(*args, **kwargs):
        raise AssertionError('Packaged lane projection observed optional runtime state')

    monkeypatch.setattr(pipeline, '_rustworkx_analysis', forbidden)
    monkeypatch.setattr(pipeline, 'configured_runtime_root', forbidden)
    monkeypatch.setattr(pipeline, 'try_resolve_native_tool', forbidden)
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_GRAPH_QUALIFICATION_ASSETS'])
    monkeypatch.setenv('EVIDENCE_LANE_HOST_PROFILE', 'CODEX_DESKTOP')
    second = generated(tmp_path / 'second')
    assert first == second
    assert len(first) >= 625
    assert any(name.endswith('.mmd') for name in first)
    assert any(name.endswith('.dot') for name in first)
    assert any(name.startswith('authorities/plan/') for name in first)
    assert any(name.startswith('authorities/chat_lineage/') for name in first)
    assert not any(name.startswith('authorities/project_sectors/plan/') for name in first)

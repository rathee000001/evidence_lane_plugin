from __future__ import annotations

import json

import pytest
from evidence_lane_plugin.build import package_contents
from evidence_lane_plugin.errors import LaneError


@pytest.fixture
def plugin(tmp_path):
    root = tmp_path / "evidence-lane-plugin"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(
        json.dumps(
            {
                "name": "evidence-lane-plugin",
                "version": "4.0.2",
            }
        )
    )
    (root / "src").mkdir()
    (root / "src/main.py").write_text("print('original')\n")
    return root


def test_identity_is_stable_and_changes_with_packaged_content(plugin):
    first = package_contents(plugin)
    assert package_contents(plugin) == first
    (plugin / "src/main.py").write_text("print('changed')\n")
    assert package_contents(plugin)["content_digest"] != first["content_digest"]


def test_caches_are_not_packaged(plugin):
    first = package_contents(plugin)
    (plugin / "src/__pycache__").mkdir()
    (plugin / "src/__pycache__/main.pyc").write_bytes(b"compiled cache")
    assert package_contents(plugin) == first


@pytest.mark.parametrize('folder', ['authorities', 'authorities/project_authority', 'env', 'uop', 'sdk', 'mcp', 'schemas', 'manifests', 'toolchains', 'studio'])
def test_identity_covers_all_executable_contract_layers(plugin, folder):
    target = plugin / folder / 'contract.json'
    target.parent.mkdir(parents=True)
    target.write_text('{"revision":1}')
    first = package_contents(plugin)
    assert any(row['path'] == folder + '/contract.json' for row in first['files'])
    target.write_text('{"revision":2}')
    assert package_contents(plugin)['content_digest'] != first['content_digest']


def test_runtime_identity_uses_the_whole_package(plugin, monkeypatch):
    from evidence_lane_plugin import build
    module_root = plugin / 'src/evidence_lane_plugin'
    module_root.mkdir()
    monkeypatch.setattr(build, '__file__', str(module_root / 'build.py'))
    first = build.runtime_source_identity()
    (plugin / 'schemas').mkdir()
    (plugin / 'schemas/current.json').write_text('{"contract":"new"}')
    second = build.runtime_source_identity()
    assert first['source_digest'] != second['source_digest']
    assert second['package_root'] == str(plugin)
    assert second['native_installation_verified'] is False


def test_unexpected_runtime_or_secret_files_block_packaging(plugin):
    (plugin / ".env").write_text("secret=value")
    with pytest.raises(LaneError) as error:
        package_contents(plugin)
    assert error.value.code == "UNEXPECTED_PACKAGE_MEMBER"


def test_wrong_plugin_identity_cannot_be_packaged(plugin):
    (plugin / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "other", "version": "4.0.1"})
    )
    with pytest.raises(LaneError) as error:
        package_contents(plugin)
    assert error.value.code == "PACKAGE_IDENTITY_INVALID"


def test_empty_directory_has_no_content_identity_but_retired_root_package_is_rejected(plugin):
    expected = package_contents(plugin)
    (plugin / 'root_pv').mkdir()
    assert package_contents(plugin) == expected
    (plugin / 'root_pv/runtime.py').write_text('raise RuntimeError("retired source route")')
    with pytest.raises(LaneError) as caught:
        package_contents(plugin)
    assert caught.value.code == 'UNEXPECTED_PACKAGE_MEMBER'

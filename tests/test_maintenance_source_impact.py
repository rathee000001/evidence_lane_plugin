from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
SCRIPT = PLUGIN / "scripts" / "generate_maintenance_source_impact.py"


def _module():
    spec = importlib.util.spec_from_file_location("maintenance_source_impact", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_impact_closure_maps_every_changed_path_without_orphans(
    tmp_path: Path,
) -> None:
    result = _module().build_source_impact_closure(
        repository_root=ROOT,
        plugin_root=PLUGIN,
        output_root=tmp_path / "impact",
        changed_paths=(
            "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py",
            "plugins/evidence-lane-plugin/README.md",
        ),
    )
    assert result["status"] == "PASS"
    assert result["changed_path_count"] == 2
    assert result["all_changed_paths_mapped"] is True
    assert result["orphaned_generated_members"] == []
    assert result["all_replacements_directly_purged"] is True
    assert all(row["affected_member_count"] > 0 for row in result["source_mappings"])
    assert (tmp_path / "impact" / "source-impact-closure.v1.json").is_file()
    assert (tmp_path / "impact" / "source-impact-closure.mmd").is_file()
    assert (tmp_path / "impact" / "source-impact-closure.dot").is_file()


def test_live_changed_paths_do_not_collapse_direct_delete_and_add_as_rename(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Evidence Lane Test"],
        cwd=repository,
        check=True,
    )
    old = repository / "old.json"
    old.write_text('{"same":"payload"}\n', encoding="utf-8")
    subprocess.run(["git", "add", "old.json"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repository, check=True)
    old.unlink()
    (repository / "new.json").write_text('{"same":"payload"}\n', encoding="utf-8")
    changed = _module()._live_changed_paths(repository)
    assert changed == ["new.json", "old.json"]

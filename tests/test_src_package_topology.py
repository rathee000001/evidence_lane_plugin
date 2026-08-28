from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.hashing import sha256_file

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
SOURCE_ROOT = PLUGIN / "src"
PACKAGE = SOURCE_ROOT / "evidence_lane_plugin"


def test_valid_python_src_layout_is_single_implementation_owner() -> None:
    assert {path.name for path in SOURCE_ROOT.iterdir() if path.is_dir()} >= {
        "evidence_lane_plugin"
    }
    assert not list(SOURCE_ROOT.glob("*.py"))
    assert not (PACKAGE / "schemas").exists()
    assert not (PACKAGE / "session_flash").exists()
    assert not list(PACKAGE.glob("*poc*.py"))
    assert not list(PACKAGE.glob("*dummy*.py"))
    non_python = {
        path.name for path in PACKAGE.iterdir() if path.is_file() and path.suffix != ".py"
    }
    assert non_python == {
        "module-registry.v1.json",
        "runtime-public-catalog.v1.json",
        "schema.sql",
    }


def test_module_registry_hashes_every_python_owner_once() -> None:
    registry = json.loads(
        (PACKAGE / "module-registry.v1.json").read_text(encoding="utf-8")
    )
    source_files = sorted(PACKAGE.glob("*.py"))
    rows = {row["path"]: row for row in registry["modules"]}
    expected = {path.relative_to(PLUGIN).as_posix() for path in source_files}
    assert registry["status"] == "PASS"
    assert registry["python_source_root"] == "src"
    assert registry["root_surfaces_duplicated_inside_python_namespace"] is False
    assert registry["loose_python_modules_directly_under_src_allowed"] is False
    assert registry["module_count"] == len(rows) == len(expected)
    assert set(rows) == expected
    for relative, row in rows.items():
        path = PLUGIN / relative
        assert path.stat().st_size == row["bytes"]
        assert sha256_file(path) == row["sha256"]
    roles = {row["module"]: row["role"] for row in rows.values()}
    assert roles["evidence_lane_plugin.first_class_workflows"] == (
        "FIRST_CLASS_WORKFLOWS"
    )
    assert roles["evidence_lane_plugin.ai_toolchain"] == "AI_TOOLCHAIN_EXECUTION"
    assert roles["evidence_lane_plugin.live_root_normalization"] == (
        "PROJECT_AUTHORITIES"
    )


def test_pyproject_packages_only_the_namespace_and_required_resources() -> None:
    pyproject = (PLUGIN / "pyproject.toml").read_text(encoding="utf-8")
    assert 'package-dir = {"" = "src"}' in pyproject
    assert 'where = ["src"]' in pyproject
    assert 'evidence_lane_plugin = [' in pyproject
    for name in (
        "module-registry.v1.json",
        "runtime-public-catalog.v1.json",
        "schema.sql",
    ):
        assert f'"{name}"' in pyproject

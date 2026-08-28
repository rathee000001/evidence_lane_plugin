from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def test_installed_test_surface_is_bounded_and_repository_tests_stay_outside() -> None:
    policy = json.loads(
        (PLUGIN / "tests" / "test-surface-policy.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert policy["status"] == "PASS"
    assert policy["local_cache_or_output_included"] is False
    assert policy["poc_builder_in_installed_surface"] is False
    assert policy["repository_only_prefixes"] == ["tests/tools/"]
    assert set(policy["installed_executable_tests"]) == {
        "tests/test_host_panel_governed_counts.py",
        "tests/test_installed_package_surface_smoke.py",
    }
    for relative in policy["installed_executable_tests"]:
        assert (PLUGIN / relative).is_file()
    assert not list((PLUGIN / "tests" / "tools").glob("*.py"))
    assert {path.name for path in (ROOT / "tests" / "tools").glob("*.py")} >= {
        "build_one_shot_dummy_poc.py",
        "build_public_dummy_lane_packages.py",
        "build_real_git_poc.py",
    }


def test_package_builder_excludes_every_declared_cache_class() -> None:
    policy = json.loads(
        (PLUGIN / "tests" / "test-surface-policy.v1.json").read_text(
            encoding="utf-8"
        )
    )
    builder = (
        PLUGIN / "scripts" / "build_release_candidate_rehearsal.py"
    ).read_text(encoding="utf-8")
    for name in policy["excluded_directory_names"]:
        assert f'"{name}"' in builder
    for suffix in policy["excluded_file_suffixes"]:
        assert f'"{suffix}"' in builder
    assert "_installed_test_paths" in builder
    assert 'relative.startswith("tests/")' in builder

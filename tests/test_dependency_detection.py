"""Preserved pnpm detection behavior with a project-independent fixture."""

from __future__ import annotations

from evidence_lane_plugin.dependency_detection import (
    PNPM_LOCK_DETECTOR_ID,
    parse_pnpm_lock_dependencies,
)

LOCK = """lockfileVersion: '9.0'
overrides:
  nanoid@<3.3.18: 3.3.18
importers:
  .:
    dependencies:
      react:
        specifier: ^19.0.0
        version: 19.2.0
      next:
        specifier: ^16.0.0
        version: 16.1.0
    devDependencies:
      typescript:
        specifier: ^5.0.0
        version: 5.9.0
packages:
  react@19.2.0:
    resolution: {integrity: test}
  next@16.1.0:
    resolution: {integrity: test}
  typescript@5.9.0:
    resolution: {integrity: test}
  nanoid@3.3.18:
    resolution: {integrity: test}
"""


def test_pnpm_lock_detector_is_ecosystem_aware_and_deterministic():
    first = parse_pnpm_lock_dependencies(LOCK)
    assert first == parse_pnpm_lock_dependencies(LOCK)
    assert first["status"] == "PASS"
    assert first["detector_id"] == PNPM_LOCK_DETECTOR_ID
    assert first["lockfile_version"] == "9.0"
    assert {row["name"] for row in first["dependencies"]} == {"react", "next", "typescript"}
    assert {row["ecosystem"] for row in first["dependencies"]} == {"pnpm"}
    assert {row["importer"] for row in first["dependencies"]} == {"."}
    assert first["overrides"] == [{"selector": "nanoid@<3.3.18", "replacement": "3.3.18"}]
    assert [row for row in first["resolved_packages"] if row["name"] == "nanoid"] == [
        {"name": "nanoid", "resolved_version": "3.3.18", "selector": "nanoid@3.3.18"},
    ]


def test_pnpm_lock_cannot_be_silently_classified_by_python_or_pip_detector():
    result = parse_pnpm_lock_dependencies("requests==2.0\nnumpy==3.0\n")
    assert result["status"] == "FAIL"
    assert result["reason"] == "PNPM_LOCK_VERSION_MISSING"


def test_pnpm_lock_preserves_unicode_in_double_quoted_yaml_scalars():
    lockfile = '''lockfileVersion: "9.0"
importers:
  ".":
    dependencies:
      "@scope/café":
        specifier: "^1.0.0"
        version: "1.0.1"
'''
    result = parse_pnpm_lock_dependencies(lockfile)
    assert result["status"] == "PASS"
    assert result["dependencies"] == [{
        "ecosystem": "pnpm", "name": "@scope/café", "constraint": "^1.0.0",
        "resolved_version": "1.0.1", "group": "dependencies", "importer": ".",
        "detector_id": PNPM_LOCK_DETECTOR_ID,
    }]

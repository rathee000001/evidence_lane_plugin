from __future__ import annotations

from pathlib import Path

from evidence_lane_plugin.dependency_detection import (
    PNPM_LOCK_DETECTOR_ID,
    parse_pnpm_lock_dependencies,
)
from evidence_lane_plugin.ingest import extract_code_lane_facts
from evidence_lane_plugin.source_graph import _manifest_dependencies, _parse_member

ROOT = Path(__file__).resolve().parents[1]
PNPM_LOCK = (
    ROOT / "apps" / "evidence-lane-app" / "pnpm-lock.yaml"
)


def test_pnpm_lock_detector_is_ecosystem_aware_and_deterministic() -> None:
    text = PNPM_LOCK.read_text(encoding="utf-8")

    first = parse_pnpm_lock_dependencies(text)
    second = parse_pnpm_lock_dependencies(text)

    assert first == second
    assert first["status"] == "PASS"
    assert first["detector_id"] == PNPM_LOCK_DETECTOR_ID
    assert first["lockfile_version"] == "9.0"
    assert len(first["dependencies"]) == 10
    assert {row["name"] for row in first["dependencies"]} >= {
        "next",
        "react",
        "framer-motion",
        "typescript",
    }
    assert {row["ecosystem"] for row in first["dependencies"]} == {"pnpm"}
    assert {row["importer"] for row in first["dependencies"]} == {"."}
    assert all(
        row["detector_id"] == PNPM_LOCK_DETECTOR_ID for row in first["dependencies"]
    )
    nanoid = [
        row for row in first["resolved_packages"] if row["name"] == "nanoid"
    ]
    assert nanoid == [
        {
            "name": "nanoid",
            "resolved_version": "3.3.18",
            "selector": "nanoid@3.3.18",
        }
    ]
    assert not [
        row
        for row in first["resolved_packages"]
        if row["name"] == "nanoid" and row["resolved_version"] == "3.3.16"
    ]
    assert first["overrides"] == [
        {"selector": "nanoid@<3.3.18", "replacement": "3.3.18"}
    ]
    assert len(first["resolved_packages"]) > len(first["dependencies"])


def test_code_lane_and_source_graph_emit_pnpm_not_python_dependency_truth() -> None:
    text = PNPM_LOCK.read_text(encoding="utf-8")

    lane_facts = extract_code_lane_facts("remote_adapter/pnpm-lock.yaml", text)
    lane_dependencies = [
        row["payload"] for row in lane_facts if row["kind"] == "code_dependency"
    ]
    detector = next(
        row["payload"]
        for row in lane_facts
        if row["kind"] == "code_dependency_detector"
    )
    graph_dependencies = _manifest_dependencies("remote_adapter/pnpm-lock.yaml", text)
    parsed = _parse_member("remote_adapter/pnpm-lock.yaml", text, "")

    assert len(lane_dependencies) == len(graph_dependencies) == 10
    assert {row["ecosystem"] for row in lane_dependencies} == {"pnpm"}
    assert {row["ecosystem"] for row in graph_dependencies} == {"pnpm"}
    assert detector == {
        "detector_id": PNPM_LOCK_DETECTOR_ID,
        "status": "PASS",
        "reason": "PNPM_IMPORTER_DEPENDENCIES_PARSED",
        "lockfile_version": "9.0",
        "dependency_count": 10,
        "resolved_package_count": len(
            parse_pnpm_lock_dependencies(text)["resolved_packages"]
        ),
        "override_count": 1,
        "ecosystem": "pnpm",
        "python_or_pip_detector_used": False,
        "zero_dependency_report_valid": False,
    }
    assert parsed["parser_id"] == PNPM_LOCK_DETECTOR_ID
    assert parsed["parse_state"] == "PARSED_MANIFEST"
    assert parsed["parse_reason"] == "PNPM_IMPORTER_DEPENDENCIES_PARSED"
    assert parsed["zero_dependency_report_valid"] is False
    assert any(
        row["name"] == "nanoid" and row["resolved_version"] == "3.3.18"
        for row in parsed["dependency_resolutions"]
    )


def test_pnpm_lock_cannot_be_silently_classified_by_python_or_pip_detector() -> None:
    malformed = "requests==2.0\nnumpy==3.0\n"

    result = parse_pnpm_lock_dependencies(malformed)
    facts = extract_code_lane_facts("pnpm-lock.yaml", malformed)
    parsed = _parse_member("pnpm-lock.yaml", malformed, "")

    assert result["status"] == "FAIL"
    assert result["reason"] == "PNPM_LOCK_VERSION_MISSING"
    assert not [row for row in facts if row["kind"] == "code_dependency"]
    detector = next(
        row["payload"] for row in facts if row["kind"] == "code_dependency_detector"
    )
    assert detector["python_or_pip_detector_used"] is False
    assert detector["status"] == "FAIL"
    assert parsed["parse_state"] == "PARSE_FAILED_MANIFEST"
    assert parsed["dependencies"] == []


def test_pnpm_lock_preserves_unicode_in_double_quoted_yaml_scalars() -> None:
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
    assert result["dependencies"] == [
        {
            "ecosystem": "pnpm",
            "name": "@scope/café",
            "constraint": "^1.0.0",
            "resolved_version": "1.0.1",
            "group": "dependencies",
            "importer": ".",
            "detector_id": PNPM_LOCK_DETECTOR_ID,
        }
    ]

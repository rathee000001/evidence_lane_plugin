from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.custom_source_schema import (
    CUSTOM_SOURCE_SCHEMA,
    compile_and_map_custom_source_schema,
    compile_custom_source_schema,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    register_source_batch,
    snapshot_source_authority_registry,
)


def _definition(schema_id: str = "project.python-file.v1") -> dict[str, object]:
    return {
        "schema": CUSTOM_SOURCE_SCHEMA,
        "schema_id": schema_id,
        "schema_version": 1,
        "title": "Project Python source mapping",
        "description": (
            "Map sealed Python member metadata into the project custom lane."
        ),
        "target_lane": "custom",
        "selectors": [
            {
                "selector_id": "python_members",
                "scope": "MEMBER",
                "source_kinds": ["directory"],
                "lane_ids": ["project_engulf"],
                "member_path_globs": ["**/*.py"],
                "extensions": [".py"],
            }
        ],
        "fields": [
            {
                "name": "object_id",
                "type": "string",
                "required": True,
                "value_from": "source.object_id",
            },
            {
                "name": "member_path",
                "type": "path",
                "required": True,
                "value_from": "member.path",
                "transforms": ["posix_path"],
            },
            {
                "name": "filename",
                "type": "string",
                "required": True,
                "value_from": "member.path",
                "transforms": ["basename"],
            },
            {
                "name": "content_sha256",
                "type": "sha256",
                "required": True,
                "value_from": "member.sha256",
            },
            {
                "name": "target_lane",
                "type": "string",
                "required": True,
                "value_from": "literal",
                "literal": "custom",
            },
        ],
        "dependency_policy": {"requires": [], "on_missing": "BLOCK"},
        "acceptance": {
            "minimum_mappings": 1,
            "maximum_mappings": 20,
            "require_all_selectors": True,
            "on_selector_overlap": "BLOCK",
            "on_unmatched_occurrence": "EXCLUDE",
        },
    }


def _registry(tmp_path: Path) -> tuple[Path, str, Path]:
    source = tmp_path / "project"
    (source / "src").mkdir(parents=True)
    (source / "src" / "main.py").write_text("print('sealed')\n", encoding="utf-8")
    (source / "README.md").write_text("# source\n", encoding="utf-8")
    registry = tmp_path / "source_authority.sqlite"
    batch = register_source_batch(
        registry,
        [
            SourceAuthoritySpec(
                source=str(source),
                ordinal=1,
                lane_id="project_engulf",
            )
        ],
    )
    return registry, str(batch["batch_id"]), source


def test_compiler_is_deterministic_and_non_executable() -> None:
    first = compile_custom_source_schema(_definition())
    second = compile_custom_source_schema(_definition())

    assert first == second
    assert first["schema_sha256"] == second["schema_sha256"]
    assert first["execution_contract"] == {
        "arbitrary_code_allowed": False,
        "imported_sql_allowed": False,
        "source_payload_read": False,
        "source_payload_copy": False,
        "mapping_authority": "SEALED_SOURCE_REGISTRY_METADATA_ONLY",
    }


def test_compiler_rejects_unknown_or_executable_transforms() -> None:
    definition = _definition()
    definition["fields"][0]["transforms"] = ["eval"]  # type: ignore[index]
    with pytest.raises(EvidenceLaneError) as blocked:
        compile_custom_source_schema(definition)
    assert blocked.value.code == "CUSTOM_SOURCE_SCHEMA_TRANSFORM_INVALID"

    definition = _definition()
    definition["python"] = "os.system('unsafe')"
    with pytest.raises(EvidenceLaneError) as unknown:
        compile_custom_source_schema(definition)
    assert unknown.value.code == "CUSTOM_SOURCE_SCHEMA_KEY_UNKNOWN"


def test_schema_maps_sealed_registry_metadata_and_is_idempotent(
    tmp_path: Path,
) -> None:
    registry, batch_id, source = _registry(tmp_path)
    source_before = (source / "src" / "main.py").read_bytes()

    first = compile_and_map_custom_source_schema(registry, batch_id, _definition())
    second = compile_and_map_custom_source_schema(registry, batch_id, _definition())
    snapshot = snapshot_source_authority_registry(registry, batch_id)

    assert first["append_status"] == "APPENDED"
    assert second["append_status"] == "IDEMPOTENT_REUSE"
    assert first["receipt_sha256"] == second["receipt_sha256"]
    assert first["mapping_count"] == 1
    assert first["matched_occurrence_count"] == 1
    assert first["mappings"][0]["mapped_fields"]["member_path"] == "src/main.py"
    assert first["mappings"][0]["mapped_fields"]["filename"] == "main.py"
    assert first["source_payload_read"] is False
    assert first["source_payload_copy"] is False
    assert first["candidate_created"] is False
    assert first["pointer_moved"] is False
    assert (source / "src" / "main.py").read_bytes() == source_before
    assert snapshot["custom_projection"]["schema_count"] == 1
    assert snapshot["custom_projection"]["mapping_count"] == 1
    assert snapshot["custom_projection"]["receipt_count"] == 1
    with sqlite3.connect(registry) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_exact_schema_dependencies_are_required(tmp_path: Path) -> None:
    registry, batch_id, _ = _registry(tmp_path)
    base = compile_and_map_custom_source_schema(
        registry, batch_id, _definition("project.base-python.v1")
    )
    dependent = _definition("project.dependent-python.v1")
    dependent["dependency_policy"] = {
        "on_missing": "BLOCK",
        "requires": [
            {
                "schema_id": "project.base-python.v1",
                "schema_version": 1,
                "schema_sha256": base["schema_sha256"],
            }
        ],
    }
    mapped = compile_and_map_custom_source_schema(registry, batch_id, dependent)
    assert mapped["status"] == "PASS"

    missing = _definition("project.missing-dependency.v1")
    missing["dependency_policy"] = {
        "on_missing": "BLOCK",
        "requires": [
            {
                "schema_id": "project.base-python.v1",
                "schema_version": 1,
                "schema_sha256": "0" * 64,
            }
        ],
    }
    with pytest.raises(EvidenceLaneError) as blocked:
        compile_and_map_custom_source_schema(registry, batch_id, missing)
    assert blocked.value.code == "CUSTOM_SOURCE_SCHEMA_DEPENDENCY_MISSING"

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.lane_engine import (
    LaneArtifactContractError,
    build_lane_artifact_role_contract,
    build_lane_bundle,
    compile_lane_artifact_extension,
    validate_lane_artifact_role_contract,
    validate_lane_bundle,
)
from evidence_lane_plugin.lanes import (
    CANONICAL_LANE_IDS,
    LANE_ARTIFACT_ROLE_REGISTRY_PATH,
    LANE_ARTIFACT_ROLE_REGISTRY_SHA256,
    LANE_REGISTRY,
    lane_artifact_contract,
)


def _seed_required_lane_files(root: Path, lane_id: str) -> None:
    root.mkdir(parents=True)
    contract = lane_artifact_contract(lane_id)
    for role in contract["required_roles"]:
        (root / role["path"]).write_bytes(
            b"{}\n" if role["path"].endswith(".json") else b"lane-artifact\n"
        )


def _docs_extension_spec(*, condition_active: bool = True) -> dict:
    return {
        "extension_id": "schema_export",
        "roles": [
            {
                "role_name": "extension_authority",
                "classification": "REQUIRED",
                "relative_path": "extensions/schema_export/authority.json",
                "condition": None,
            },
            {
                "role_name": "schema_migration_receipt",
                "classification": "CONDITIONAL",
                "relative_path": "extensions/schema_export/migration.json",
                "condition": {
                    "condition_id": "SCHEMA_MIGRATION_APPLIED",
                    "active": condition_active,
                },
            },
            {
                "role_name": "render_preview",
                "classification": "OPTIONAL",
                "relative_path": "extensions/schema_export/preview.svg",
                "condition": None,
            },
        ],
    }


def test_all_lanes_have_concrete_required_conditional_optional_contracts() -> None:
    assert (
        sha256_file(LANE_ARTIFACT_ROLE_REGISTRY_PATH)
        == LANE_ARTIFACT_ROLE_REGISTRY_SHA256
    )
    contracts = [lane_artifact_contract(lane_id) for lane_id in CANONICAL_LANE_IDS]
    assert len({row["contract_sha256"] for row in contracts}) == 18

    for contract, lane_id in zip(contracts, CANONICAL_LANE_IDS, strict=True):
        lane = LANE_REGISTRY[lane_id]
        assert contract["schema"] == "evidence-lane.lane-artifact-role-contract.v1"
        assert contract["registry_sha256"] == LANE_ARTIFACT_ROLE_REGISTRY_SHA256
        assert contract["lane_id"] == lane_id
        assert [row["path"] for row in contract["required_roles"]] == [
            lane.sqlite_filename,
            lane.mmd_filename,
            lane.dot_filename,
            "tools.json",
            "lane_pointer.json",
            "refresh_receipt.json",
            "lane_manifest.json",
        ]
        assert {row["classification"] for row in contract["required_roles"]} == {
            "REQUIRED"
        }
        assert contract["extension_required_roles"] == [
            {
                "role_name": "extension_authority",
                "classification": "REQUIRED",
                "condition": None,
            }
        ]
        assert contract["conditional_roles"]
        assert contract["optional_roles"]
        assert {row["classification"] for row in contract["conditional_roles"]} == {
            "CONDITIONAL"
        }
        assert {row["classification"] for row in contract["optional_roles"]} == {
            "OPTIONAL"
        }
        assert contract["extension_policy"]["unrelated_lane_effect"] == "NONE"


def test_built_bundle_emits_and_revalidates_additive_artifact_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.md").write_text("# lane artifact roles\n", encoding="utf-8")
    output = tmp_path / "bundle"
    build_lane_bundle(
        repository_root=source,
        output_directory=output,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV-ARTIFACT-ROLE",
        pointer_generation=0,
        source_overrides={"guide.md": "docs"},
    )

    manifest = json.loads(
        (output / "docs" / "lane_manifest.json").read_text(encoding="utf-8")
    )
    declared = manifest["artifact_role_contract"]
    assert manifest["schema"] == "evidence-lane.lane-manifest.v3"
    assert declared["status"] == "PASS"
    assert declared["valid"] is True
    assert declared["extensions"] == []
    assert declared["undeclared_extension_files"] == []
    assert len(declared["projection_sha256"]) == 64

    validation = validate_lane_bundle(output)
    report = validation["artifact_role_contracts"]["docs"]
    assert validation["valid"] is True
    assert report["status"] == "PASS"
    assert report["enforced"] is True
    assert report["declared_projection_sha256"] == report[
        "computed_projection_sha256"
    ]


def test_scoped_extension_seals_roles_without_changing_another_lane(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    analysis_root = tmp_path / "analysis"
    _seed_required_lane_files(docs_root, "docs")
    _seed_required_lane_files(analysis_root, "analysis")
    analysis_before = build_lane_artifact_role_contract(
        analysis_root, LANE_REGISTRY["analysis"]
    )

    extension_root = docs_root / "extensions" / "schema_export"
    extension_root.mkdir(parents=True)
    (extension_root / "authority.json").write_text(
        '{"schema":"docs.extension.authority.v1"}\n', encoding="utf-8"
    )
    (extension_root / "migration.json").write_text(
        '{"migration":"docs.extension.annotations.v001"}\n', encoding="utf-8"
    )
    spec = _docs_extension_spec()
    compiled = compile_lane_artifact_extension(
        docs_root, LANE_REGISTRY["docs"], spec
    )
    assert compiled["status"] == "PASS"
    assert compiled["unrelated_lane_effect"] == "NONE"
    assert compiled["roles"][0]["required_now"] is True
    assert compiled["roles"][1]["required_now"] is True
    assert compiled["roles"][2]["exists"] is False

    projection = build_lane_artifact_role_contract(
        docs_root,
        LANE_REGISTRY["docs"],
        extensions=[spec],
    )
    assert projection["status"] == "PASS"
    assert projection["extensions"] == [compiled]
    assert validate_lane_artifact_role_contract(
        docs_root,
        LANE_REGISTRY["docs"],
        projection,
    )["valid"] is True

    analysis_after = build_lane_artifact_role_contract(
        analysis_root, LANE_REGISTRY["analysis"]
    )
    assert analysis_after == analysis_before

    (extension_root / "authority.json").write_text(
        '{"schema":"tampered"}\n', encoding="utf-8"
    )
    tampered = validate_lane_artifact_role_contract(
        docs_root,
        LANE_REGISTRY["docs"],
        projection,
    )
    assert tampered["status"] == "FAIL"
    assert tampered["valid"] is False


def test_inactive_conditional_and_absent_optional_are_valid(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    _seed_required_lane_files(root, "docs")
    extension_root = root / "extensions" / "schema_export"
    extension_root.mkdir(parents=True)
    (extension_root / "authority.json").write_text("{}\n", encoding="utf-8")

    compiled = compile_lane_artifact_extension(
        root,
        LANE_REGISTRY["docs"],
        _docs_extension_spec(condition_active=False),
    )
    assert compiled["roles"][1]["required_now"] is False
    assert compiled["roles"][1]["exists"] is False
    assert compiled["roles"][2]["required_now"] is False
    assert compiled["roles"][2]["exists"] is False


def test_extension_missing_required_undeclared_and_path_escape_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    _seed_required_lane_files(root, "docs")
    extension_root = root / "extensions" / "schema_export"
    extension_root.mkdir(parents=True)
    spec = _docs_extension_spec(condition_active=False)

    with pytest.raises(LaneArtifactContractError) as missing:
        compile_lane_artifact_extension(root, LANE_REGISTRY["docs"], spec)
    assert missing.value.code == "LANE_ARTIFACT_EXTENSION_REQUIRED_FILE_MISSING"

    (extension_root / "authority.json").write_text("{}\n", encoding="utf-8")
    (extension_root / "undeclared.txt").write_text("not declared\n", encoding="utf-8")
    with pytest.raises(LaneArtifactContractError) as undeclared:
        compile_lane_artifact_extension(root, LANE_REGISTRY["docs"], spec)
    assert undeclared.value.code == "LANE_ARTIFACT_EXTENSION_UNDECLARED_FILE"

    (extension_root / "undeclared.txt").unlink()
    escaped = _docs_extension_spec(condition_active=False)
    escaped["roles"][0]["relative_path"] = "extensions/schema_export/../escape.json"
    with pytest.raises(LaneArtifactContractError) as traversal:
        compile_lane_artifact_extension(root, LANE_REGISTRY["docs"], escaped)
    assert traversal.value.code == "LANE_ARTIFACT_EXTENSION_PATH_INVALID"

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from evidence_lane_plugin import flash_identity
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.flash_authority import (
    NESTED_SOURCE_LAYOUT_AUTHORITY_DIGEST,
    NESTED_SOURCE_LAYOUT_FLASH_MANIFEST_SHA256,
    NESTED_SOURCE_LAYOUT_SOURCE_AUTHORITY_MANIFEST_SHA256,
    SessionFlashAuthority,
)
from evidence_lane_plugin.flash_identity import (
    REQUIRED_CODEX_PROJECTION_MEMBER_PATHS,
    SOURCE_AUTHORITY_MEMBER_PATHS,
    build_flash_dual_identity,
)
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes, sha256_file


def _identity_inputs(
    authority: SessionFlashAuthority,
) -> tuple[dict, dict, str]:
    manifest = json.loads(authority.manifest_path.read_text(encoding="utf-8"))
    audit = json.loads(
        (authority.asset_root / "env" / "SOURCE_PACKET_AUDIT.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_sha256 = sha256_bytes(authority.manifest_path.read_bytes())
    return manifest, audit, manifest_sha256


def test_dual_identity_preserves_clean_current_codex_action_plane(
    tmp_path: Path,
) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    report = authority.verify()
    dual = report["dual_identity"]
    source = dual["source_authority"]["manifest"]
    projection = dual["codex_projection"]["manifest"]

    assert source["source_packet_status"] == "PASS"
    assert source["whole_packet_accepted"] is False
    assert source["complete_working_behavior_graph_adapted"] is True
    assert source["usable_boundary"] == (
        "CURRENT_CODEX_ACTION_PLANE_PLUS_ADAPTED_ENV15_3_BEHAVIOR"
    )
    assert source["missing_declared_members"] == []
    source_hashes = {row["path"]: row["sha256"] for row in source["members"]}
    expected_source_hashes = {
        path: sha256_file(authority.asset_root / path)
        for path in SOURCE_AUTHORITY_MEMBER_PATHS
    }
    assert source_hashes == expected_source_hashes
    assert set(source_hashes) == SOURCE_AUTHORITY_MEMBER_PATHS
    projection_paths = {row["path"] for row in projection["members"]}
    assert SOURCE_AUTHORITY_MEMBER_PATHS.isdisjoint(projection_paths)
    assert REQUIRED_CODEX_PROJECTION_MEMBER_PATHS <= projection_paths
    assert projection["derived_projection_is_source_authority"] is False
    assert (
        report["runtime_projection"]["source_authority_manifest_sha256"]
        == dual["source_authority"]["manifest_sha256"]
    )
    assert (
        report["runtime_projection"]["codex_projection_identity_sha256"]
        == dual["codex_projection"]["identity_sha256"]
    )


def test_projection_material_manifest_version_and_generator_change_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    manifest, audit, manifest_sha256 = _identity_inputs(authority)
    baseline = build_flash_dual_identity(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        source_audit=audit,
    )
    baseline_source = baseline["source_authority"]["manifest_sha256"]
    baseline_projection = baseline["codex_projection"]["identity_sha256"]

    for path in (
        "env/env_law.md",
        "env/locked_mmd_hash.txt",
        "uop/uop_law.md",
    ):
        changed_manifest = copy.deepcopy(manifest)
        member = next(
            item for item in changed_manifest["members"] if item["path"] == path
        )
        member["sha256"] = "F" * 64
        changed = build_flash_dual_identity(
            manifest=changed_manifest,
            manifest_sha256=sha256_bytes(canonical_json_bytes(changed_manifest)),
            source_audit=audit,
        )
        assert changed["source_authority"]["manifest_sha256"] == baseline_source
        assert changed["codex_projection"]["identity_sha256"] != baseline_projection

    manifest_changed = build_flash_dual_identity(
        manifest=manifest,
        manifest_sha256="A" * 64,
        source_audit=audit,
    )
    assert (
        manifest_changed["codex_projection"]["identity_sha256"] != baseline_projection
    )

    version_manifest = copy.deepcopy(manifest)
    version_manifest["authority_version"] = "ENV15_UOP15_TEST_NEXT"
    version_changed = build_flash_dual_identity(
        manifest=version_manifest,
        manifest_sha256=manifest_sha256,
        source_audit=audit,
    )
    assert version_changed["codex_projection"]["identity_sha256"] != baseline_projection

    original_generator = flash_identity._generator_identity()
    monkeypatch.setattr(
        flash_identity,
        "_generator_identity",
        lambda: {**original_generator, "identity_version": "TEST_NEXT"},
    )
    generator_changed = build_flash_dual_identity(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        source_audit=audit,
    )
    assert (
        generator_changed["codex_projection"]["identity_sha256"] != baseline_projection
    )


def test_missing_locked_member_fails_closed(tmp_path: Path) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    manifest, audit, manifest_sha256 = _identity_inputs(authority)
    manifest["members"] = [
        item for item in manifest["members"] if item["path"] != "env/env_sqlite.sqlite"
    ]
    with pytest.raises(EvidenceLaneError) as error:
        build_flash_dual_identity(
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            source_audit=audit,
        )
    assert error.value.code == "SESSION_FLASH_SOURCE_AUTHORITY_MEMBER_MISSING"


def test_same_version_projection_receipt_reuse_is_rejected(tmp_path: Path) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    created = authority.ensure_flashed()
    receipt = created["receipt"]
    changed = copy.deepcopy(authority.verify())
    changed["dual_identity"]["codex_projection"]["identity_sha256"] = "B" * 64
    changed["dual_identity"]["build_identity"]["identity_sha256"] = "C" * 64

    with pytest.raises(EvidenceLaneError) as error:
        authority._validated_receipt(changed, receipt)
    assert error.value.code == ("SESSION_FLASH_SAME_VERSION_PROJECTION_REUSE_FORBIDDEN")


def test_new_plugin_build_migrates_flash_receipt_append_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    created = authority.ensure_flashed()
    prior_receipt = copy.deepcopy(created["receipt"])
    prior_receipt_bytes = authority.receipt_path.read_bytes()
    changed = copy.deepcopy(authority.verify())
    changed["dual_identity"]["codex_projection"]["identity_sha256"] = "B" * 64
    changed["authority_digest"] = "8" * 64
    changed["manifest_sha256"] = "9" * 64
    changed["dual_identity"]["source_authority"]["manifest_sha256"] = "A" * 64
    changed["dual_identity"]["build_identity"].update(
        {
            "plugin_version": "3.0.0+codex.20990101000000",
            "projection_cache_identity_sha256": "C" * 64,
            "identity_sha256": "D" * 64,
        }
    )
    monkeypatch.setattr(authority, "verify", lambda: copy.deepcopy(changed))

    migrated = authority.ensure_flashed()

    assert migrated["status"] == "PASS"
    assert migrated["flash_action"] == "BUILD_IDENTITY_MIGRATED"
    assert migrated["receipt"]["plugin_version"] == ("3.0.0+codex.20990101000000")
    migration = migrated["build_migration"]
    assert migration["prior_plugin_version"] == prior_receipt["plugin_version"]
    assert migration["current_plugin_version"] == "3.0.0+codex.20990101000000"
    assert migration["project_state_mutated"] is False
    assert migration["pointer_moved"] is False
    assert migration["candidate_mutated"] is False
    assert migration["migration_scope"] == "NEW_PLUGIN_BUILD_FULL_FLASH_AUTHORITY"
    assert migration["authority_changed"] is True
    assert migration["source_authority_changed"] is True
    assert migration["projection_changed"] is True
    archived = list(
        (authority.data_root / "installation" / "flash_authority_migrations").glob(
            "*/session_flash_receipt.prior.json"
        )
    )
    assert len(archived) == 1
    assert archived[0].read_bytes() == prior_receipt_bytes


def test_nested_layout_flash_receipt_migrates_to_current_build(
    tmp_path: Path,
) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    current = authority.verify()
    prior = authority._receipt_from_report(current)
    prior.update(
        {
            "plugin_version": "3.0.0+codex.20260826024829",
            "manifest_sha256": NESTED_SOURCE_LAYOUT_FLASH_MANIFEST_SHA256,
            "authority_digest": NESTED_SOURCE_LAYOUT_AUTHORITY_DIGEST,
            "source_authority_manifest_sha256": (
                NESTED_SOURCE_LAYOUT_SOURCE_AUTHORITY_MANIFEST_SHA256
            ),
            "codex_projection_identity_sha256": "A" * 64,
            "projection_cache_identity_sha256": "B" * 64,
            "build_identity_sha256": "C" * 64,
        }
    )
    authority.receipt_path.parent.mkdir(parents=True, exist_ok=True)
    authority.receipt_path.write_text(
        json.dumps(prior, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    migrated = authority.ensure_flashed()

    assert migrated["flash_action"] == "BUILD_IDENTITY_MIGRATED"
    assert (
        migrated["build_migration"]["source_layout_migrated_from_nested_package"]
        is True
    )
    assert migrated["receipt"]["manifest_sha256"] == current["manifest_sha256"]
    assert (
        migrated["receipt"]["source_authority_manifest_sha256"]
        == (current["dual_identity"]["source_authority"]["manifest_sha256"])
    )

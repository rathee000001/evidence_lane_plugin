from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from evidence_lane_plugin import flash_identity
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.flash_authority import SessionFlashAuthority
from evidence_lane_plugin.flash_identity import (
    REQUIRED_CODEX_PROJECTION_MEMBER_PATHS,
    SOURCE_AUTHORITY_MEMBER_PATHS,
    build_flash_dual_identity,
)
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

EXPECTED_SOURCE_HASHES = {
    "env/env_mmd.png": (
        "EBA6F1B76BA181100426E59B0D481034296811F35BCB6F255DEBF9CAD9D18FAA"
    ),
    "env/env_sqlite.sqlite": (
        "78EEC5EFF7BA82DF38DF62ED65F2E8A4B8E1F3A593B8387779EAD7EA45E03810"
    ),
    "uop/uop_mmd.png": (
        "09F9035EB1A71443887C37B6B57663D46FA70DB3B8B1C51D789FF5BB3A3264A1"
    ),
    "uop/uop_sqlite.sqlite": (
        "DB2539AAC36BE38D89C74D052C4764ECD28E4CFA29EFBF4EB6B0E4234CB1377F"
    ),
}


def _identity_inputs(
    authority: SessionFlashAuthority,
) -> tuple[dict, dict, str]:
    manifest = json.loads(authority.manifest_path.read_text(encoding="utf-8"))
    audit = json.loads(
        (authority.asset_root / "SOURCE_PACKET_AUDIT.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_sha256 = sha256_bytes(authority.manifest_path.read_bytes())
    return manifest, audit, manifest_sha256


def test_dual_identity_preserves_source_hashes_and_partial_boundary(
    tmp_path: Path,
) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    report = authority.verify()
    dual = report["dual_identity"]
    source = dual["source_authority"]["manifest"]
    projection = dual["codex_projection"]["manifest"]

    assert source["source_packet_status"] == "PARTIAL_INTEGRITY"
    assert source["whole_packet_accepted"] is False
    assert source["usable_boundary"] == (
        "INDEPENDENTLY_VERIFIED_ENV15_UOP15_SUBSET_ONLY"
    )
    assert source["missing_declared_members"] == [
        "codex/CODEX_EXPECTED_FILE_MAP.json",
        "codex/CODEX_PUBLIC_SECTION_PATCH_SCOPE.md",
        "codex/UEPC_ENV15_CODEX_PUBLIC_SECTION_UPDATE_HANDOFF.md",
        "codex/UEPC_ENV15_CODEX_PUBLIC_SECTION_UPDATE_PROMPT.md",
        "codex/codex_update_flow.mmd",
        "codex/codex_update_flow.svg",
    ]
    source_hashes = {row["path"]: row["sha256"] for row in source["members"]}
    assert source_hashes == EXPECTED_SOURCE_HASHES
    assert set(source_hashes) == SOURCE_AUTHORITY_MEMBER_PATHS
    projection_paths = {row["path"] for row in projection["members"]}
    assert SOURCE_AUTHORITY_MEMBER_PATHS.isdisjoint(projection_paths)
    assert REQUIRED_CODEX_PROJECTION_MEMBER_PATHS <= projection_paths
    assert projection["derived_projection_is_source_authority"] is False
    assert report["runtime_projection"][
        "source_authority_manifest_sha256"
    ] == dual["source_authority"]["manifest_sha256"]
    assert report["runtime_projection"][
        "codex_projection_identity_sha256"
    ] == dual["codex_projection"]["identity_sha256"]


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
        "env/env_mmd.mmd",
        "env/env_mmd.svg",
        "env/env_law.md",
        "env/locked_mmd_hash.txt",
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
        assert (
            changed["codex_projection"]["identity_sha256"]
            != baseline_projection
        )

    manifest_changed = build_flash_dual_identity(
        manifest=manifest,
        manifest_sha256="A" * 64,
        source_audit=audit,
    )
    assert (
        manifest_changed["codex_projection"]["identity_sha256"]
        != baseline_projection
    )

    version_manifest = copy.deepcopy(manifest)
    version_manifest["authority_version"] = "ENV15_UOP15_TEST_NEXT"
    version_changed = build_flash_dual_identity(
        manifest=version_manifest,
        manifest_sha256=manifest_sha256,
        source_audit=audit,
    )
    assert (
        version_changed["codex_projection"]["identity_sha256"]
        != baseline_projection
    )

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
        generator_changed["codex_projection"]["identity_sha256"]
        != baseline_projection
    )


def test_missing_locked_member_fails_closed(tmp_path: Path) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    manifest, audit, manifest_sha256 = _identity_inputs(authority)
    manifest["members"] = [
        item
        for item in manifest["members"]
        if item["path"] != "env/env_sqlite.sqlite"
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
    assert error.value.code == (
        "SESSION_FLASH_SAME_VERSION_PROJECTION_REUSE_FORBIDDEN"
    )

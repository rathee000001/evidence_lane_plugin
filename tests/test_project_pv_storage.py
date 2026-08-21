from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.project_pv_storage import (
    accepted_storage_status,
    archive_member_bytes,
    build_project_pv_archive,
    materialized_project_pv_archive,
    validate_project_pv_archive,
    working_overlay_manifest,
)


def _working_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "accepted" / "PV12").mkdir(parents=True)
    (root / "accepted" / "PV12" / "legacy.txt").write_text(
        "preserved legacy PV12\n", encoding="utf-8"
    )
    (root / "sectors" / "plan").mkdir(parents=True)
    (root / "sectors" / "plan" / "plan.sqlite").write_bytes(b"plan-current")
    (root / "sectors" / "local_code").mkdir(parents=True)
    (root / "sectors" / "local_code" / "one.bin").write_bytes(b"same")
    (root / "sectors" / "local_code" / "two.bin").write_bytes(b"same")
    (root / "receipts" / "candidate-overlays").mkdir(parents=True)
    (root / "receipts" / "candidate-overlays" / "ignored.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (root / "sessions").mkdir()
    (root / "sessions" / "runtime.json").write_text("{}\n", encoding="utf-8")
    (root / "active_pointer.json").write_text(
        '{"accepted_pv":"PV12"}\n', encoding="utf-8"
    )
    return root


def test_working_overlay_is_root_authority_without_accepted_or_runtime(
    tmp_path: Path,
) -> None:
    root = _working_root(tmp_path)

    manifest = working_overlay_manifest(root, project_id="project-one")

    paths = {row["path"] for row in manifest["members"]}
    assert paths == {
        "sectors/local_code/one.bin",
        "sectors/local_code/two.bin",
        "sectors/plan/plan.sqlite",
    }
    assert manifest["unique_blob_count"] == 2
    assert manifest["member_count"] == 3
    assert not (root / "candidates").exists()


def test_project_pv_archive_is_deterministic_cas_and_materializes_exactly(
    tmp_path: Path,
) -> None:
    root = _working_root(tmp_path)
    staging = root / ".accepted-staging" / "PV13.zip"
    pointer = {
        "schema": "evidence-lane.active-pointer.v1",
        "project_id": "project-one",
        "accepted_pv": "PV13",
        "generation": 13,
        "prior_generation": 12,
        "accepted_manifest_binding": "THIS_ARCHIVE_MANIFEST_SHA256",
    }

    first = build_project_pv_archive(
        root,
        staging,
        project_id="project-one",
        pv_id="PV13",
        candidate_id="PV13_CANDIDATE__RUN_01TEST",
        parent_accepted_pv="PV12",
        parent_accepted_manifest_sha256="A" * 64,
        pointer_override=pointer,
        candidate_package_metadata={
            "manifest": {
                "universal_lanes": {
                    "canonical_lane_count": 18,
                    "emitted_lane_ids": ["local_code", "plan"],
                }
            },
            "project_identity": {"project_id": "project-one"},
            "exit_slip": {"status": "PASS"},
        },
    )
    second_path = root / ".accepted-staging" / "PV13-second.zip"
    second = build_project_pv_archive(
        root,
        second_path,
        project_id="project-one",
        pv_id="PV13",
        candidate_id="PV13_CANDIDATE__RUN_01TEST",
        parent_accepted_pv="PV12",
        parent_accepted_manifest_sha256="A" * 64,
        pointer_override=pointer,
        candidate_package_metadata={
            "manifest": {
                "universal_lanes": {
                    "canonical_lane_count": 18,
                    "emitted_lane_ids": ["local_code", "plan"],
                }
            },
            "project_identity": {"project_id": "project-one"},
            "exit_slip": {"status": "PASS"},
        },
    )

    assert first["archive_sha256"] == second["archive_sha256"]
    assert first["archive_manifest_sha256"] == second["archive_manifest_sha256"]
    assert first["member_count"] == 3
    assert first["unique_blob_count"] == 2
    assert first["deduplicated_member_count"] == 1
    assert archive_member_bytes(staging, "sectors/plan/plan.sqlite") == b"plan-current"
    with materialized_project_pv_archive(staging) as view:
        assert (view / "sectors" / "local_code" / "one.bin").read_bytes() == b"same"
        assert not (view / "accepted").exists()
        assert not (view / "active_pointer.json").exists()


def test_accepted_status_preserves_unpacked_pv_until_future_hil(tmp_path: Path) -> None:
    root = _working_root(tmp_path)
    before = (root / "accepted" / "PV12" / "legacy.txt").read_bytes()

    status = accepted_storage_status(
        root,
        project_id="project-one",
        accepted_pv="PV12",
        pointer_generation=12,
        accepted_manifest_sha256="A" * 64,
    )

    assert status["status"] == "PASS"
    assert status["state"] == "LEGACY_ACCEPTED_PRESERVED_UNTIL_EXACT_FUTURE_HIL"
    assert status["accepted_artifact_count"] == 1
    assert status["candidate_created"] is False
    assert status["pointer_moved"] is False
    assert status["destructive_retention_action_performed"] is False
    assert (root / "accepted" / "PV12" / "legacy.txt").read_bytes() == before


def test_accepted_status_fails_closed_on_unrecognized_single_artifact(
    tmp_path: Path,
) -> None:
    root = _working_root(tmp_path)
    (root / "accepted" / "PV12" / "legacy.txt").unlink()
    (root / "accepted" / "PV12").rmdir()
    (root / "accepted" / "unexpected.bin").write_bytes(b"not governed storage")

    status = accepted_storage_status(
        root,
        project_id="project-one",
        accepted_pv="PV12",
        pointer_generation=12,
        accepted_manifest_sha256="A" * 64,
    )

    assert status["status"] == "MISMATCH"
    assert status["state"] == "ACCEPTED_STORAGE_REQUIRES_RECONCILIATION"


def test_archive_validation_rejects_tampered_cas_object(tmp_path: Path) -> None:
    root = _working_root(tmp_path)
    archive_path = root / ".accepted-staging" / "PV13.zip"
    build_project_pv_archive(
        root,
        archive_path,
        project_id="project-one",
        pv_id="PV13",
        candidate_id="PV13_CANDIDATE__RUN_01TEST",
        parent_accepted_pv="PV12",
        parent_accepted_manifest_sha256="A" * 64,
        pointer_override={"accepted_pv": "PV13", "generation": 13},
    )
    with zipfile.ZipFile(archive_path) as source:
        rows = {
            name: source.read(name)
            for name in source.namelist()
        }
    object_name = next(name for name in rows if name.startswith("objects/sha256/"))
    rows[object_name] = b"tampered"
    with zipfile.ZipFile(archive_path, "w") as target:
        for name, data in rows.items():
            target.writestr(name, data)

    with pytest.raises(EvidenceLaneError) as raised:
        validate_project_pv_archive(archive_path)

    assert raised.value.code == "PROJECT_PV_ARCHIVE_OBJECT_HASH_MISMATCH"

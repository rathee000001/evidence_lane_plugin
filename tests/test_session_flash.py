from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.flash_authority import (
    ENV_MMD_SHA256,
    FLASH_AUTHORITY_VERSION,
    UOP_MMD_SHA256,
    SessionFlashAuthority,
)
from evidence_lane_plugin.pv_package import validate_pv_package

from .conftest import boot_local


def test_locked_env_uop_flash_is_visible_idempotent_and_outside_pv(service) -> None:
    before = service.session_flash_status()
    assert before["status"] == "PASS"
    assert before["flash_state"] == "NOT_FLASHED"
    assert before["authority_version"] == FLASH_AUTHORITY_VERSION
    assert before["authorities"]["env"]["mmd_sha256"] == ENV_MMD_SHA256
    assert before["authorities"]["uop"]["mmd_sha256"] == UOP_MMD_SHA256
    assert before["authorities"]["env"]["sqlite"]["integrity"] == ["ok"]
    assert before["authorities"]["uop"]["sqlite"]["integrity"] == ["ok"]
    assert before["source_packet"] == {
        "status": "PARTIAL_INTEGRITY",
        "whole_packet_accepted": False,
        "usable_boundary": "INDEPENDENTLY_VERIFIED_ENV15_UOP15_SUBSET_ONLY",
    }

    boot = boot_local(service)
    assert boot["session"]["state"] == "BOOTED"
    assert boot["session_flash"]["flash_action"] == "CREATED"
    assert boot["next_action_contract"]["state"] == "SOURCE_INTAKE_READY"
    assert boot["next_action_contract"]["display_position"] == (
        "AFTER_ATOMIC_BOOT_FLASH"
    )
    assert boot["ordered_source_intake_commands"][0:3] == [
        "/evi-02-git",
        "/evi-03-local",
        "/evi-04-sqlite-pv-candidate-loader",
    ]
    assert len(boot["ordered_source_intake_commands"]) == 17
    assert boot["suggested_next_prompt"].startswith("Choose /evi-02-git")
    assert boot["next_action_contract"]["auto_submit"] is False
    assert boot["session"]["metadata"]["flash_context_stored_in_pv"] is False
    assert (
        boot["session"]["metadata"]["flash_authority_digest"]
        == before["authority_digest"]
    )
    reused = service.flash_authority.ensure_flashed()
    assert reused["flash_action"] == "REUSED"
    assert reused["receipt"]["inside_pv"] is False
    assert reused["receipt"]["hil_approval_inferred"] is False

    lineage_path = (
        service.store.project_root("book-faires")
        / "lineage"
        / f"{boot['session']['session_id']}.jsonl"
    )
    events = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["event_type"] == "session.flash.verified"
    assert events[0]["visible_payload"]["state"] == "SESSION_BOOT_FLASH"
    assert events[0]["visible_payload"]["hil_approval_inferred"] is False


def test_corrupted_locked_flash_member_fails_closed(tmp_path: Path) -> None:
    source = SessionFlashAuthority(data_root=tmp_path / "source-store").asset_root
    copied = tmp_path / "copied-authority"
    shutil.copytree(source, copied)
    with (copied / "env" / "env_mmd.mmd").open("ab") as handle:
        handle.write(b"\ncorruption")
    authority = SessionFlashAuthority(
        data_root=tmp_path / "target-store",
        asset_root=copied,
    )
    with pytest.raises(EvidenceLaneError) as error:
        authority.verify()
    assert error.value.code == "SESSION_FLASH_MEMBER_HASH_MISMATCH"


def test_nested_env_or_uop_content_is_forbidden_in_pv(service) -> None:
    boot = boot_local(service)
    result = service.build_initial("book-faires", boot["session"]["session_id"])
    candidate = Path(result["candidate"]["stored_path"])
    forbidden = candidate / "env" / "authority.txt"
    forbidden.parent.mkdir()
    forbidden.write_text("not allowed in a PV\n", encoding="utf-8")
    with pytest.raises(EvidenceLaneError) as error:
        validate_pv_package(candidate)
    assert error.value.code == "PV_FORBIDDEN_ENVIRONMENT_OPERATOR_MEMBER"

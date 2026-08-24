from __future__ import annotations

import hashlib
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
from evidence_lane_plugin.runtime_activation import RuntimeActivation

from .conftest import boot_local

STABLE_FLASH_MANIFEST_SHA256 = (
    "4585D703515D2DE245F688E3047F192C6BD3D507475B57855918561933C5293A"
)
STABLE_FLASH_PROMPT_SHA256 = (
    "2167BBABE80656C24B18544096E725E874D4FB46066B8F4F8364A3BF14A827DB"
)
STABLE_FLASH_AUTHORITY_DIGEST = (
    "644AEEAE1434B3808E544BA9C634ACE3685F21F73D3F86E0CF5DE31D4A6B48A5"
)


def _sealed_json_sha256(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()


def test_runtime_status_requires_sealed_host_hook_trust(tmp_path: Path) -> None:
    runtime = RuntimeActivation(tmp_path)
    runtime.path.parent.mkdir(parents=True, exist_ok=True)
    runtime.path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.runtime-activation.v1",
                "plugin_id": "evidence-lane-plugin",
                "state": "ACTIVE",
                "generation": 1,
                "active_sessions": [
                    {"project_id": "project-a", "session_id": "session-a"}
                ],
                "flash_context_attached": True,
                "prompt_capture_active": True,
                "visible_response_capture_active": True,
                "immutable_store_preserved": True,
                "plugin_installation_preserved": True,
                "hil_approval_inferred": False,
            }
        ),
        encoding="utf-8",
    )

    unproven = runtime.status_with_host_proof()
    assert unproven["prompt_capture_configured"] is True
    assert unproven["prompt_capture_active"] is True
    assert unproven["visible_response_capture_active"] is True
    assert unproven["prompt_response_capture_decoupled_from_hooks"] is True
    assert unproven["host_hook_status"]["status"] == "UNAVAILABLE"

    selector = "evidence-lane-plugin@evidence-lane-v200-task2-build-test"
    events = [
        "permissionRequest",
        "postCompact",
        "postToolUse",
        "preCompact",
        "preToolUse",
        "sessionEnd",
        "sessionStart",
        "stop",
        "subagentStart",
        "subagentStop",
        "userPromptSubmit",
    ]
    hook_trust: dict[str, object] = {
        "schema": "evidence-lane.codex-hook-trust.v1",
        "status": "PASS",
        "plugin_selector": selector,
        "hook_count": 11,
        "registered_events": events,
        "records": [
            {
                "event_name": event,
                "hook_key": f"{selector}:hooks/hooks.json:{event}:0:0",
                "current_hash": f"sha256:{index:064x}",
                "enabled": True,
                "trust_status": "trusted",
            }
            for index, event in enumerate(events, start=1)
        ],
        "before_trust_statuses": ["untrusted"],
        "after_trust_statuses": ["trusted"],
        "after_enabled_states": [True],
        "supported_codex_api": ["hooks/list", "config/batchWrite"],
        "config_version": f"sha256:{'a' * 64}",
        "workspace_sha256": "B" * 64,
        "raw_workspace_path_included": False,
        "hook_commands_included": False,
        "source_paths_included": False,
        "unrelated_hook_state_mutated": False,
    }
    hook_trust["receipt_sha256"] = _sealed_json_sha256(hook_trust)
    installation: dict[str, object] = {
        "schema": "evidence-lane.codex-stable-installation.v2",
        "status": "PASS",
        "activation": {
            "state": "INSTALLED_RESTART_REQUIRED",
            "plugin_add": {"pluginId": selector},
            "hook_trust": hook_trust,
        },
    }
    installation["receipt_sha256"] = _sealed_json_sha256(installation)
    current_installation = (
        tmp_path
        / "installations"
        / "codex-v200"
        / "CURRENT_INSTALLATION.json"
    )
    current_installation.parent.mkdir(parents=True, exist_ok=True)
    current_installation.write_text(
        json.dumps(
            installation,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    proven = runtime.status_with_host_proof()
    assert proven["host_hook_status"]["status"] == "TRUSTED"
    assert proven["host_hooks_runnable"] is True
    assert proven["prompt_capture_active"] is True
    assert proven["prompt_capture_partially_available"] is True
    assert proven["required_pre_reasoning_capture_complete"] is False
    assert proven["supported_pre_reasoning_capture_complete"] is True
    assert proven["missing_required_pre_reasoning_surfaces"] == [
        "GOAL_CONTINUATION"
    ]
    assert proven["host_capability_unavailable_surfaces"] == [
        "GOAL_CONTINUATION"
    ]
    assert proven["capture_gap_code"] == (
        "HOST_PRE_REASONING_USER_INPUT_HOOK_UNAVAILABLE"
    )
    surfaces = {
        row["surface"]: row
        for row in proven["required_pre_reasoning_capture_surfaces"]
    }
    assert surfaces["USER_PROMPT_CORRECTION_OR_HIL_TOKEN"][
        "pre_reasoning_dispatch_runnable"
    ] is True
    assert surfaces["MID_GOAL_STEER"]["state"] == (
        "RUNNABLE_REQUIRES_PER_INPUT_PREPARE_RECEIPT"
    )
    assert surfaces["GOAL_CONTINUATION"]["state"] == (
        "HOST_CAPABILITY_UNAVAILABLE"
    )
    assert surfaces["GOAL_CONTINUATION"]["native_hook_event"] is None
    assert surfaces["GOAL_CONTINUATION"]["pre_reasoning_dispatch_runnable"] is False
    assert proven["visible_response_capture_active"] is True


def test_runtime_status_does_not_treat_activation_as_prompt_invocation_proof(
    service,
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    status = service.runtime_activation_status()
    bounded = next(
        row
        for row in status["per_session_capture_evidence"]
        if row["project_id"] == "book-faires"
        and row["evidence_session_id"] == session_id
    )
    assert bounded["indexed_visible_input_count"] == 0
    assert bounded["per_input_invocation_proven"] is False
    assert status["runtime_flags_are_invocation_proof"] is False
    assert status["active_session_capture_gap_count"] == 1
    assert status["active_session_capture_gap_code"] == (
        "ACTIVE_RUNTIME_WITHOUT_SEALED_PROMPT_INDEX_RECORD"
    )
    assert status["status"] == "PASS"
    assert status["activation_quality"] == "RUNNABLE_PROMPT_INDEX_EVIDENCE_GAP"
    assert status["explicit_public_actions_runnable"] is True
    assert status["prompt_response_capture_decoupled_from_hooks"] is True
    assert status["capture_unavailable_is_structured_domain_state"] is True
    assert status["adapter_record_is_independent_installed_host_proof"] is False


def test_v2_reuses_the_existing_stable_flash_authority(tmp_path: Path) -> None:
    authority = SessionFlashAuthority(data_root=tmp_path / "store")
    report = authority.verify()
    assert report["manifest_sha256"] == STABLE_FLASH_MANIFEST_SHA256
    assert report["prompt"]["sha256"] == STABLE_FLASH_PROMPT_SHA256
    assert report["authority_digest"] == STABLE_FLASH_AUTHORITY_DIGEST

    authority.receipt_path.parent.mkdir(parents=True, exist_ok=True)
    authority.receipt_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.session-flash-receipt.v1",
                "receipt_id": "flash_644aeeae1434b3808e544ba9",
                "plugin_id": "evidence-lane-plugin",
                "state": "FLASHED_UNTIL_PLUGIN_REMOVED",
                "authority_version": FLASH_AUTHORITY_VERSION,
                "authority_digest": STABLE_FLASH_AUTHORITY_DIGEST,
                "manifest_sha256": STABLE_FLASH_MANIFEST_SHA256,
                "flashed_at": "2026-08-07T21:32:21.754144Z",
                "scope": "PLUGIN_INSTALLATION_OUTSIDE_PV",
                "inside_pv": False,
                "hil_approval_inferred": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    reused = authority.ensure_flashed()
    assert reused["status"] == "PASS"
    assert reused["flash_action"] == "REUSED"
    assert reused["receipt"]["receipt_id"] == "flash_644aeeae1434b3808e544ba9"


def test_locked_env_uop_flash_is_visible_idempotent_and_outside_pv(service) -> None:
    before = service.session_flash_status()
    assert before["status"] == "PASS"
    assert before["flash_state"] == "NOT_FLASHED"
    assert before["authority_version"] == FLASH_AUTHORITY_VERSION
    assert before["authorities"]["env"]["mmd_sha256"] == ENV_MMD_SHA256
    assert before["authorities"]["uop"]["mmd_sha256"] == UOP_MMD_SHA256
    assert before["authorities"]["env"]["sqlite"]["integrity"] == ["ok"]
    assert before["authorities"]["uop"]["sqlite"]["integrity"] == ["ok"]
    assert before["runtime_projection"]["status"] == "PASS"
    assert before["runtime_projection"]["row_count"] == before[
        "runtime_projection"
    ]["fts_count"]
    assert before["source_packet"] == {
        "status": "PARTIAL_INTEGRITY",
        "whole_packet_accepted": False,
        "usable_boundary": "INDEPENDENTLY_VERIFIED_ENV15_UOP15_SUBSET_ONLY",
    }

    boot = boot_local(service)
    assert boot["session"]["state"] == "BOOTED"
    assert boot["session_flash"]["flash_action"] == "CREATED"
    assert boot["runtime_activation"]["state"] == "ACTIVE"
    assert boot["runtime_activation"]["flash_context_attached"] is True
    assert boot["runtime_activation"]["prompt_capture_active"] is True
    assert boot["runtime_activation"]["visible_response_capture_active"] is True
    assert boot["next_action_contract"]["state"] == "SOURCE_INTAKE_READY"
    assert boot["next_action_contract"]["display_position"] == (
        "AFTER_ATOMIC_BOOT_FLASH"
    )
    assert boot["ordered_source_intake_commands"] == ["/evi-source-intake"]
    assert boot["suggested_next_prompt"].startswith("Use /evi-source-intake")
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

from __future__ import annotations

import copy
import json
from pathlib import Path

import evidence_lane_plugin.session as session_module
import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.models import HostKind
from evidence_lane_plugin.persistence import route_persistence
from evidence_lane_plugin.runtime_continuity import validate_runtime_continuity

from .conftest import boot_local, build_and_approve_pv1


def _legacy_runtime_continuity(current: dict) -> dict:
    legacy = copy.deepcopy(current)
    legacy.pop("invocation")
    legacy.pop("continuity_receipt_sha256")
    legacy["continuity_receipt_sha256"] = sha256_bytes(
        canonical_json_bytes(legacy)
    )
    return legacy


def test_legacy_reference_only_runtime_receipt_remains_valid(service) -> None:
    boot = boot_local(service)
    legacy = _legacy_runtime_continuity(boot["runtime_continuity"])

    validated = validate_runtime_continuity(legacy)

    assert validated == legacy
    assert "invocation" not in validated


def test_legacy_runtime_receipt_rejects_unsafe_boundary(service) -> None:
    boot = boot_local(service)
    legacy = _legacy_runtime_continuity(boot["runtime_continuity"])
    legacy["entry_exit_slip"]["pointer_movement"] = True
    legacy.pop("continuity_receipt_sha256")
    legacy["continuity_receipt_sha256"] = sha256_bytes(
        canonical_json_bytes(legacy)
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        validate_runtime_continuity(legacy)

    assert blocked.value.code == "RUNTIME_CONTINUITY_LEGACY_BOUNDARY_INVALID"


def test_resume_upgrades_legacy_receipt_without_rewriting_it(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    legacy = _legacy_runtime_continuity(boot["runtime_continuity"])
    session = service.sessions.load("book-faires", session_id)
    session.metadata["runtime_continuity"] = legacy
    service.sessions._save(session)

    resumed = service.resume_session(
        project_id="book-faires",
        host="CODEX_DESKTOP",
        host_session_id="codex-legacy-continuity-upgrade",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"purpose": "upgrade the sealed runtime receipt"},
    )

    current = validate_runtime_continuity(resumed["runtime_continuity"])
    assert current["continuity_receipt_sha256"] != legacy[
        "continuity_receipt_sha256"
    ]
    assert current["invocation"]["six_way_hil_preserved"] is True
    stored = service.sessions.load("book-faires", session_id)
    archived = stored.metadata["runtime_continuity_receipt_archive"]
    assert archived[-1]["continuity_receipt_sha256"] == legacy[
        "continuity_receipt_sha256"
    ]
    assert archived[-1]["receipt"] == legacy


def test_host_route_distinguishes_local_and_vm_durability() -> None:
    local = route_persistence(
        HostKind.CODEX_DESKTOP,
        ephemeral=False,
        server_has_durable_filesystem=True,
    )
    assert local.mode == "local"
    assert local.host_profile == "CODEX_LOCAL_PC_OR_LAPTOP"
    assert local.primary_runtime_authority == "LOCAL_DURABLE_SQLITE"
    assert local.google_drive_policy == "NOT_SELECTED_FOR_DURABLE_HOST"

    stable_vm = route_persistence(
        HostKind.CODEX_VM,
        ephemeral=False,
    )
    assert stable_vm.mode == "local"
    assert stable_vm.host_profile == "CODEX_STABLE_VM_OR_HOST"
    assert stable_vm.google_drive_policy == "NOT_SELECTED_FOR_DURABLE_HOST"

    ephemeral_vm = route_persistence(
        HostKind.CODEX_VM,
        ephemeral=True,
        server_has_durable_filesystem=False,
    )
    assert ephemeral_vm.mode == "configured_durable_connector"
    assert ephemeral_vm.durable_required is True
    assert ephemeral_vm.google_drive_policy == (
        "CODEX_EPHEMERAL_SEALED_ENTRY_EXIT_CARRIER_ALLOWED_NOT_PRIMARY"
    )


def test_boot_build_and_resume_preserve_reference_only_runtime_continuity(
    service,
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    continuity = validate_runtime_continuity(boot["runtime_continuity"])
    assert continuity["env_uop"]["bytes_in_pv"] is False
    assert continuity["storage"]["primary_runtime_authority"] == (
        "LOCAL_DURABLE_SQLITE"
    )
    assert continuity["mcp_access"]["one_writer_required"] is True
    assert continuity["invocation"]["headless_api"] is False
    assert continuity["invocation"]["flash_verification"] == (
        "VERIFY_LOCKED_ENV_UOP_AT_EVERY_BOOT_OR_RESUME"
    )

    built = service.build_initial("book-faires", session_id)
    candidate_path = service.store.candidate_path(
        "book-faires", built["candidate"]["candidate_id"]
    )
    entry = json.loads((candidate_path / "entry_slip.json").read_text(encoding="utf-8"))
    exit_slip = json.loads(
        (candidate_path / "exit_slip.json").read_text(encoding="utf-8")
    )
    assert entry["runtime_continuity"] == exit_slip["runtime_continuity"]
    assert entry["runtime_continuity"]["continuity_receipt_sha256"] == (
        continuity["continuity_receipt_sha256"]
    )
    assert entry["runtime_continuity"]["entry_exit_slip"][
        "env_uop_bytes_embedded"
    ] is False
    assert exit_slip["pv_exit_prompt"] == {
        "label": "PV_EXIT_SUGGESTED_NEXT_PROMPT",
        "suggested_next_prompt": built["suggested_next_prompt"],
        "choices": [
            "APPROVE",
            "APPROVE_WITH_DELTA",
            "MORE_RESEARCH",
            "ROLLBACK",
            "REJECT",
            "FAIL",
        ],
        "copyable": True,
        "host_owned_composer": True,
        "auto_submit": False,
    }

    resumed = service.resume_session(
        project_id="book-faires",
        host="CODEX_DESKTOP",
        host_session_id="codex-local-mcp-resume",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"purpose": "read and write through durable MCP"},
    )
    resumed_continuity = validate_runtime_continuity(resumed["runtime_continuity"])
    assert resumed["session"]["candidate_id"] == built["candidate"]["candidate_id"]
    assert resumed["session"]["state"] == "PV1_CANDIDATE"
    assert service.store.pointer("book-faires").generation == 0
    assert resumed_continuity["host"]["kind"] == "CODEX_DESKTOP"
    assert resumed_continuity["storage"]["google_drive_policy"] == (
        "NOT_SELECTED_FOR_DURABLE_HOST"
    )
    assert resumed_continuity["pointer_moved"] is False


def test_google_drive_cannot_be_selected_as_primary_runtime(service) -> None:
    with pytest.raises(EvidenceLaneError) as blocked:
        service.storage_connector_select(
            "book-faires",
            mode="CONFIGURED_DURABLE_CONNECTOR",
            connector_id="google-drive",
            selected_by="human-test",
            reason="Attempt to select Drive as runtime.",
            confirmation=(
                "SELECT_STORAGE:CONFIGURED_DURABLE_CONNECTOR:google-drive"
            ),
        )
    assert blocked.value.code == "GOOGLE_DRIVE_PRIMARY_RUNTIME_FORBIDDEN"


def test_resume_preserves_integrity_valid_accepted_authority_across_new_rules(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    real_validate = session_module.validate_pv_package

    def accepted_compatibility_probe(
        directory: str | Path,
        *,
        require_promotable: bool = True,
    ) -> dict:
        if Path(directory).name == "PV1" and require_promotable:
            raise EvidenceLaneError(
                "PV_LANE_BUNDLE_INVALID",
                "An accepted authority must not be retroactively requalified.",
                status="FAIL",
            )
        result = real_validate(
            directory,
            require_promotable=require_promotable,
        )
        if Path(directory).name == "PV1":
            result = copy.deepcopy(result)
            result["promotable"] = False
            result["lanes"]["status"] = "FAIL"
            result["lanes"]["valid"] = False
        return result

    monkeypatch.setattr(
        session_module,
        "validate_pv_package",
        accepted_compatibility_probe,
    )
    resumed = service.resume_session(
        project_id="book-faires",
        host="CODEX_DESKTOP",
        host_session_id="codex-accepted-authority-compatibility",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"purpose": "build stricter successor without mutating entry"},
    )
    entry_pointer = validate_runtime_continuity(resumed["runtime_continuity"])[
        "entry_pointer"
    ]
    assert resumed["session"]["session_id"] == session_id
    assert resumed["session"]["state"] == "PVN1_ENTRY"
    assert entry_pointer["accepted_pv"] == "PV1"
    assert entry_pointer["accepted_authority_integrity_validated"] is True
    assert entry_pointer["promotability_required_for_boot_or_resume"] is False
    assert entry_pointer["promotable_under_current_rules"] is False
    assert entry_pointer["compatibility_state"] == (
        "ACCEPTED_IMMUTABLE_HISTORICAL_SCHEMA"
    )
    assert entry_pointer["successor_candidate_must_pass_current_rules"] is True
    assert service.store.pointer("book-faires").generation == 1


@pytest.mark.parametrize(
    ("host", "ephemeral", "durable", "expected_authority"),
    [
        ("CODEX_DESKTOP", False, True, "LOCAL_DURABLE_SQLITE"),
        ("CODEX_VM", False, True, "LOCAL_DURABLE_SQLITE"),
        ("CODEX_VM", True, True, "DURABLE_MOUNT_SQLITE"),
    ],
)
def test_headless_api_reflashes_every_invocation_without_tunnel(
    host: str,
    ephemeral: bool,
    durable: bool,
    expected_authority: str,
) -> None:
    route = route_persistence(
        host,
        ephemeral=ephemeral,
        server_has_durable_filesystem=durable,
        runtime_context={
            "interaction_profile": "HEADLESS_API",
            "account_tier": "API",
        },
    )

    assert route.primary_runtime_authority == expected_authority
    assert route.tunnel_requirement == "NOT_REQUIRED_FOR_API_LAYER"
    assert route.tunnel_setup_frequency == "NONE"
    assert route.account_tier_affects_routing is False
    assert route.api_billing_affects_routing is False


def test_headless_boot_seals_api_invocation_and_local_pv_continuity(service) -> None:
    boot = service.boot_session(
        project_id="book-faires",
        user_id="user-api",
        workspace_id="workspace-api",
        host="CODEX_DESKTOP",
        agent_id="codex-headless",
        sandbox_id="local-api-profile",
        ephemeral=False,
        runtime_context={
            "interaction_profile": "DIRECT_CLI_API",
            "account_tier": "API",
        },
        host_session_id="headless-invocation-1",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )

    continuity = validate_runtime_continuity(boot["runtime_continuity"])
    invocation = continuity["invocation"]
    assert continuity["storage"]["primary_runtime_authority"] == (
        "LOCAL_DURABLE_SQLITE"
    )
    assert invocation["interaction_profile"] == "DIRECT_CLI_API"
    assert invocation["tunnel_required_for_api_layer"] is False
    assert invocation["flash_verification"] == (
        "VERIFY_LOCKED_ENV_UOP_AT_EVERY_API_INVOCATION_ENTRY"
    )
    assert invocation["prior_state_load"] == (
        "EXACT_PROJECT_DURABLE_RUNTIME_PLUS_ACCEPTED_OR_PENDING_ENTRY_EXIT_SLIP"
    )
    assert invocation["exit_slip_next_prompt_label"] == (
        "PV_EXIT_SUGGESTED_NEXT_PROMPT"
    )
    assert invocation["six_way_hil_preserved"] is True
    assert invocation["durable_runtime_survives_client_process"] is True

from __future__ import annotations

import copy
import json
from pathlib import Path

import evidence_lane_plugin.session as session_module
import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.models import HostKind
from evidence_lane_plugin.persistence import route_persistence
from evidence_lane_plugin.runtime_continuity import validate_runtime_continuity

from .conftest import boot_local, build_and_approve_pv1


def test_host_route_keeps_chatgpt_off_drive_and_distinguishes_vm_durability() -> None:
    chatgpt = route_persistence(
        HostKind.CHATGPT,
        ephemeral=True,
        server_has_durable_filesystem=True,
    )
    assert chatgpt.mode == "local"
    assert chatgpt.host_profile == "CHATGPT_DURABLE_MCP_HOST"
    assert chatgpt.primary_runtime_authority == "MCP_SERVER_MOUNTED_OR_LOCAL_SQLITE"
    assert chatgpt.google_drive_policy == "FORBIDDEN_FOR_CHATGPT_RUNTIME"

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

    resumed = service.resume_session(
        project_id="book-faires",
        host="CHATGPT_WORK",
        host_session_id="chatgpt-local-mcp-resume",
        ephemeral=True,
        client_can_edit_source=False,
        server_has_durable_filesystem=True,
        runtime_context={"purpose": "read and write through durable MCP"},
    )
    resumed_continuity = validate_runtime_continuity(resumed["runtime_continuity"])
    assert resumed["session"]["candidate_id"] == built["candidate"]["candidate_id"]
    assert resumed["session"]["state"] == "PV1_CANDIDATE"
    assert service.store.pointer("book-faires").generation == 0
    assert resumed_continuity["host"]["kind"] == "CHATGPT_WORK"
    assert resumed_continuity["storage"]["google_drive_policy"] == (
        "FORBIDDEN_FOR_CHATGPT_RUNTIME"
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

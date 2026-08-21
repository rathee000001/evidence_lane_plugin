from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from types import ModuleType

from evidence_lane_plugin import hook_skill_runtime
from evidence_lane_plugin.agent_learning import (
    host_memory_boundary_contract,
    inspect_learning_authority,
    record_host_memory_import,
    seal_learning_candidate,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import atomic_write_json, sha256_bytes

SOURCE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID = "row211-host-memory-fixture"
T0 = "2026-08-15T12:00:00+00:00"
T1 = "2026-08-15T12:01:00+00:00"
T2 = "2026-08-16T12:00:00+00:00"


def _hash(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project_root(base: Path) -> Path:
    root = base / PROJECT_ID
    root.mkdir()
    atomic_write_json(
        root / "project.json",
        {"schema": "row211.fixture.project.v1", "project_id": PROJECT_ID},
    )
    atomic_write_json(
        root / "active_pointer.json",
        {
            "schema": "evidence-lane.pointer.v1",
            "project_id": PROJECT_ID,
            "accepted_pv": "PV12",
            "generation": 12,
            "accepted_manifest_sha256": _hash("row211-project-manifest"),
        },
    )
    return root


def _ordinary_evidence(label: str) -> dict[str, str]:
    return {
        "project_id": PROJECT_ID,
        "task_id": "task-row211",
        "delta_id": "EL-CODEX-T6-PARITY-014-HOST-MEMORY-BOUNDARY",
        "pv_ref": "PV12",
        "ref": f"lineage://{label}",
        "sha256": _hash(label),
    }


def _seal(root: Path, evidence: list[dict[str, str]], statement: str) -> dict:
    return seal_learning_candidate(
        root,
        project_id=PROJECT_ID,
        tier="DELTA_OBSERVATION",
        lesson_type="FAILURE_AVOIDANCE",
        statement=statement,
        scope={"kind": "PROJECT", "selectors": [PROJECT_ID]},
        evidence=evidence,
        outcome="SUCCEEDED",
        confidence=0.9,
        counterevidence=[_ordinary_evidence("bounded-counterevidence")],
        contradictions=["truth://known-boundary"],
        temporal={"observed_at": T0, "valid_from": T1, "expires_at": T2},
        privacy="PROJECT_PRIVATE",
        source_lineage_head_sha256=_hash("row211-lineage-head"),
    )


def _verify_behavior_handoff() -> str:
    hooks = SOURCE_ROOT / "plugins" / "evidence-lane-plugin" / "hooks"
    module = _load_module(hooks / "behavior_handoff.py", "row211_behavior_handoff")
    transport: dict[str, object] = {
        "schema": "evidence-lane.codex-hook-transport.v1",
        "event_name": "UserPromptSubmit",
        "transport_receipt_sha256": "A" * 64,
        "hook_behavior_executed": False,
    }
    skill_receipt: dict[str, object] = {
        "state": "BOUNDED_SKILL_RESULT",
        "hook_transport_envelope": dict(transport),
        "hook_runtime_role": module.HOOK_RUNTIME_ROLE,
        "skill_action_owner": module.SKILL_RUNTIME_OWNER,
        "skill_action": module.EVENT_SKILL_ACTIONS["UserPromptSubmit"],
        "skill_action_executed": True,
        "hook_behavior_executed": False,
    }
    consumer = hook_skill_runtime.consume_prompt_transport
    issued = module.issue_behavior_handoff_receipt(
        "UserPromptSubmit",
        transport,
        skill_receipt,
        skill_consumer=consumer,
    )
    consumed = module.consume_behavior_handoff_receipt(
        issued,
        event_name="UserPromptSubmit",
        transport=transport,
        skill_receipt=skill_receipt,
        skill_consumer=consumer,
    )
    for receipt in (issued, consumed):
        assert receipt["host_memory_imported"] is False
        assert receipt["learning_candidate_created"] is False
        assert receipt["learning_hil_invoked"] is False
    return str(consumed["consumed_receipt_sha256"])


def main() -> None:
    boundary = host_memory_boundary_contract()
    assert boundary["host_memory_authority"] == (
        "NONAUTHORITATIVE_HELPFUL_RECALL_ONLY"
    )
    assert boundary["automatic_import_allowed"] is False
    assert boundary["direct_evidence_reference_allowed"] is False
    assert boundary["explicit_provenance_receipt_required"] is True
    assert boundary["learning_candidate_creation"] == (
        "SEPARATE_EXPLICIT_ACTION_REQUIRED"
    )
    assert boundary["learning_hil_invocation"] == (
        "SEPARATE_EXPLICIT_ACTION_REQUIRED"
    )
    assert boundary["project_hil_invocation"] == "FORBIDDEN"

    policy = json.loads(
        (
            SOURCE_ROOT
            / "plugins"
            / "evidence-lane-plugin"
            / "hooks"
            / "event_isolation_policy.json"
        ).read_text(encoding="utf-8")
    )["host_memory_boundary"]
    assert policy["authority"] == boundary["host_memory_authority"]
    assert policy["automatic_import"] is False
    assert policy["hook_import"] is False
    assert policy["explicit_provenance_receipt_required"] is True
    assert policy["raw_host_memory_stored"] is False
    assert policy["learning_candidate_creation"] == (
        "SEPARATE_EXPLICIT_ACTION_REQUIRED"
    )
    assert policy["learning_hil_invocation"] == "SEPARATE_EXPLICIT_ACTION_REQUIRED"
    assert policy["project_hil_invocation"] == "FORBIDDEN"

    with tempfile.TemporaryDirectory(prefix="evidence-lane-row211-") as raw:
        root = _project_root(Path(raw))
        project_pointer_before = (root / "active_pointer.json").read_bytes()
        direct = _ordinary_evidence("direct-memory")
        direct["ref"] = "codex-local-memory://memory-summary/entry-001"
        try:
            _seal(root, [direct], "Direct host memory must fail closed.")
        except EvidenceLaneError as exc:
            assert exc.code == "LEARNING_HOST_MEMORY_PROVENANCE_REQUIRED"
            direct_block_code = exc.code
        else:
            raise AssertionError("Direct host memory unexpectedly sealed a candidate.")

        import_args = {
            "project_id": PROJECT_ID,
            "source_kind": "CODEX_LOCAL_MEMORY",
            "source_locator": "codex-local-memory://memory-summary/entry-001",
            "source_record_sha256": _hash("exact generated memory record"),
            "source_context_id": "host-context-row211",
            "observed_at": T0,
            "imported_at": T1,
            "imported_by": "row211-verifier",
            "purpose": "Bound one recall record as nonauthoritative provenance only.",
            "task_id": "task-row211",
            "delta_id": "EL-CODEX-T6-PARITY-014-HOST-MEMORY-BOUNDARY",
            "pv_ref": "PV12",
        }
        imported = record_host_memory_import(root, **import_args)
        repeated = record_host_memory_import(root, **import_args)
        assert repeated["receipt"] == imported["receipt"]
        assert imported["candidate_created"] is False
        assert imported["accepted_learning"] is False
        assert imported["learning_hil_invoked"] is False
        assert imported["learning_pointer_moved"] is False
        assert imported["project_hil_invoked"] is False
        assert imported["project_truth_pointer_moved"] is False
        assert imported["raw_host_memory_stored"] is False
        assert imported["private_reasoning_stored"] is False
        receipt_path = Path(imported["receipt_path"])
        assert "host-context-row211" not in receipt_path.read_text(encoding="utf-8")
        assert (root / "active_pointer.json").read_bytes() == project_pointer_before
        assert not (root / "learning" / "active_pointer.json").exists()
        before_candidate = inspect_learning_authority(root, project_id=PROJECT_ID)
        assert before_candidate["candidate_count"] == 0
        assert before_candidate["host_memory_import_count"] == 1

        sealed = _seal(
            root,
            [imported["learning_evidence_reference"]],
            "Imported host memory remains evidence pending separate Learning HIL.",
        )
        assert sealed["state"] == "PENDING_LEARNING_HIL"
        assert sealed["host_memory_import_receipt_sha256s"] == [
            imported["receipt"]["receipt_sha256"]
        ]
        assert sealed["project_hil_invoked"] is False
        assert sealed["project_truth_pointer_moved"] is False
        assert not (root / "learning" / "active_pointer.json").exists()

        tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
        tampered["authority_boundary"]["accepted_learning"] = True
        atomic_write_json(receipt_path, tampered)
        try:
            _seal(
                root,
                [imported["learning_evidence_reference"]],
                "A tampered provenance receipt must fail closed.",
            )
        except EvidenceLaneError as exc:
            assert exc.code in {
                "LEARNING_HOST_MEMORY_IMPORT_RECEIPT_HASH_MISMATCH",
                "LEARNING_HOST_MEMORY_IMPORT_RECEIPT_BOUNDARY_INVALID",
            }
            tamper_block_code = exc.code
        else:
            raise AssertionError("Tampered host-memory provenance was accepted.")

    handoff_sha256 = _verify_behavior_handoff()
    print(
        json.dumps(
            {
                "schema": "evidence-lane.row211-host-memory-verification.v1",
                "status": "PASS",
                "direct_reference_block_code": direct_block_code,
                "tamper_block_code": tamper_block_code,
                "import_idempotent": True,
                "candidate_after_import": "NONE",
                "candidate_after_explicit_seal": "PENDING_LEARNING_HIL",
                "host_memory_authority": boundary["host_memory_authority"],
                "hook_memory_imported": False,
                "behavior_handoff_receipt_sha256": handoff_sha256,
                "live_project_or_host_state_touched": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

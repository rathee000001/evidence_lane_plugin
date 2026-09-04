from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from evidence_lane_plugin import adaptive_delta_entry as entry
from evidence_lane_plugin.mode_governance import (
    compile_env_uop_formula,
    route_env_uop_operator,
)
from evidence_lane_plugin.operating_modes import classify_operating_modes


class _Pointer:
    def as_dict(self) -> dict[str, Any]:
        return {"accepted_pv": "PV12", "generation": 12}


class _Store:
    def __init__(self, root: Path, *, closed: bool = False) -> None:
        self.root = root
        self.formula_events: list[dict[str, Any]] = (
            [
                {
                    "event_id": "entry-existing",
                    "event_kind": "ENTRY_FORMULA",
                    "formula_sha256": "E" * 64,
                }
            ]
            if closed
            else []
        )
        if closed:
            self.formula_events.append(
                {
                    "event_id": "exit-existing",
                    "event_kind": "EXIT_FORMULA",
                    "formula_sha256": "X" * 64,
                }
            )
        self.record_calls = 0

    def backlog_status(self, _project_id: str) -> dict[str, Any]:
        return {
            "goal_projection": {
                "canonical_plan_sha256": "C" * 64,
                "rows": [
                    {
                        "task_id": "R265",
                        "status": "in_progress",
                        "lifecycle_status": "ACTIVE",
                        "title": "Current HIL Delta",
                        "requested_outcome": "Finish R265",
                        "stage": "IMPLEMENT",
                    }
                ],
            }
        }

    def plan_runtime_query(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "PASS",
            "query_mode": "EXACT_TASK_ID",
            "steers": [
                {
                    "delta_id": "steer-1",
                    "text": "Use the current local route and all live authorities.",
                }
            ],
            "formula_events": list(self.formula_events),
        }

    def pointer(self, _project_id: str) -> _Pointer:
        return _Pointer()

    def config(self, _project_id: str) -> SimpleNamespace:
        return SimpleNamespace(repository_path=self.root / "repo")

    def project_root(self, _project_id: str) -> Path:
        return self.root / "project"

    def reconcile_verified_predecessor_sub_pv(
        self, *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        return {
            "status": "PASS",
            "state": "EXISTING_SUB_PV_REUSED",
            "sub_pv_acceptance": {"sub_pv_id": "PV13.1.1"},
            "plan_task_advanced": False,
            "project_pointer_moved": False,
        }

    def record_task_formula(self, *_args: Any, **kwargs: Any) -> dict[str, Any]:
        self.record_calls += 1
        event = {
            "event_id": kwargs["event_id"],
            "event_kind": kwargs["event_kind"],
            "formula_sha256": "F" * 64,
        }
        self.formula_events.append(event)
        return {"status": "PASS", "event": event}


class _Sessions:
    def __init__(self) -> None:
        self.session = SimpleNamespace(
            metadata={"active_backlog_task_id": "R265"},
            candidate_id="PV13_CANDIDATE",
        )

    def load(self, *_args: Any) -> SimpleNamespace:
        return self.session

    def record_activity(self, *_args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "PASS", "event": {"event_id": kwargs["event_id"]}}


class _SDK:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        operation = str(kwargs.get("operation") or "")
        payload = dict(kwargs.get("payload") or {})
        if operation == "classify_mode":
            data = classify_operating_modes(
                str(payload["request"]),
                explicit_modes=list(payload["explicit_modes"]),
                code_lane="local_code",
            )
            return {
                "status": "PASS",
                "receipt_sha256": "M" * 64,
                "binding_sha256": "A" * 64,
                "data": data,
            }
        if operation == "compile_formula":
            data = compile_env_uop_formula(
                payload["mode_governance"],
                payload["execution_budget"],
                sdk_binding_sha256="A" * 64,
            )
            return {
                "status": "PASS",
                "receipt_sha256": "C" * 64,
                "binding_sha256": "A" * 64,
                "data": data,
            }
        if operation == "route_operator":
            data = route_env_uop_operator(
                payload["compiled_formula"],
                sdk_binding_sha256="A" * 64,
                mode_id=payload["mode_id"],
                operator_id=payload["operator_id"],
                requested_effect=payload["requested_effect"],
                lane_id=payload["lane_id"],
                tool_id=payload["tool_id"],
                lane_units=payload["lane_units"],
                tool_invocations=payload["tool_invocations"],
            )
            return {
                "status": "PASS",
                "receipt_sha256": "R" * 64,
                "binding_sha256": "A" * 64,
                "data": data,
            }
        return {
            "status": "PASS",
            "receipt_sha256": "L" * 64,
            "data": {
                "status": "PASS",
                "candidate_count": 152,
                "auto_accepted_delta_count": 151,
                "pending_weave_count": 1,
                "learning_weaves": [
                    {
                        "member_count": 151,
                        "weave_candidate_id": "PV13_LEARNING_WEAVE",
                    }
                ],
                "current_pointer": {"generation": 0},
                "integrity": ["ok"],
                "foreign_key_errors": 0,
            },
        }


def _live_result() -> dict[str, Any]:
    authorities = {
        name: {"status": "PASS", "authority": name.upper()}
        for name in (
            "sector_lanes",
            "agent_learning",
            "canon_graph",
            "project_memory",
            "project_universe",
            "connector_brain",
            "agent_configuration",
            "conversation_memory",
        )
    }
    return {
        "status": "PASS",
        "result_state": "HITS",
        "env_uop": {
            "env_authority_sha256": "1" * 64,
            "uop_authority_sha256": "2" * 64,
            "derived_projection_sha256": "3" * 64,
            "flash_receipt_sha256": "4" * 64,
        },
        "authorities": authorities,
        "refresh_required": True,
        "refresh_performed": True,
        "bounded_retry_performed": True,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
    }


@pytest.mark.parametrize(
    ("closed", "expected_disposition", "expected_record_calls"),
    [
        (False, "NEW_EXECUTABLE_ENTRY_FORMULA_APPENDED", 1),
        (True, "CLOSED_ROW_ENTRY_AUDITED_NO_APPEND", 0),
    ],
)
def test_adaptive_delta_entry_consumes_all_authorities_and_preserves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    closed: bool,
    expected_disposition: str,
    expected_record_calls: int,
) -> None:
    store = _Store(tmp_path, closed=closed)
    service = SimpleNamespace(store=store, sessions=_Sessions())
    monkeypatch.setattr(
        entry,
        "inspect_repository",
        lambda _path: SimpleNamespace(
            as_dict=lambda: {
                "branch": "agent/test",
                "commit_sha": "A" * 40,
                "tree_sha": "B" * 40,
                "worktree_sha256": "D" * 64,
            }
        ),
    )
    monkeypatch.setattr(
        entry, "query_live_authorities", lambda *_a, **_k: _live_result()
    )
    sdk = _SDK()
    monkeypatch.setattr(
        entry,
        "build_live_local_sdk_context",
        lambda *_a, **_k: (sdk, SimpleNamespace()),
    )

    result = entry.run_adaptive_delta_entry(
        service,
        "project",
        "session",
        classification_result={
            "classification_binding": {"receipt_sha256": "B" * 64},
            "task": {
                "task_class": "modify_code",
                "permitted_paths": ["plugins/evidence-lane-plugin"],
                "permitted_tools": ["apply_patch", "pytest"],
            },
        },
    )

    receipt = result["receipt"]
    assert result["status"] == "PASS"
    assert receipt["formula_disposition"] == expected_disposition
    assert store.record_calls == expected_record_calls
    assert set(receipt["authorities_consumed"]) == set(_live_result()["authorities"])
    assert receipt["learning"]["auto_accepted_delta_count"] == 151
    assert receipt["learning"]["pending_weave_count"] == 1
    learning_call = next(
        call for call in sdk.calls if call.get("operation") == "inspect"
    )
    assert learning_call["payload"] == {"summary_only": True}
    assert receipt["normalized_steer_contract"]["steer_count"] == 1
    assert receipt["normalized_steer_contract"]["verification_scope"] == (
        "ONCE_PER_NORMALIZED_DELTA_ROW"
    )
    assert receipt["normalized_steer_contract"]["regression_repeated_per_steer"] is False
    assert receipt["authority_time_boundary"] == {
        "full_pv_pointer_baseline": "PV12",
        "full_pv_history_role": "IMMUTABLE_PV_N_MINUS_1_BASELINE",
        "sector_lane_snapshot_role": (
            "PROGRESSIVE_LIVE_ROOT_THROUGH_PREDECESSOR_DELTA_EXIT"
        ),
        "predecessor_sub_pv_id": "PV13.1.1",
        "predecessor_sub_pv_role": (
            "LATEST_AUTO_ACCEPTED_DELTA_CHECKPOINT_AND_LANE_WATERMARK"
        ),
        "delta_learning_role": "LATEST_AUTO_ACCEPTED_PROCEDURAL_EVIDENCE",
        "dirty_live_repository_role": (
            "CURRENT_ACTIVE_DELTA_IMPLEMENTATION_UNDER_CONSTRUCTION"
        ),
        "current_delta_present_in_sector_lanes": False,
        "completed_predecessor_deltas_present_in_sector_lanes": True,
        "current_delta_refresh_owner": "ADAPTIVE_DELTA_EXIT",
        "source_test_claim_scope": "SOURCE_IMPLEMENTATION_ONLY",
        "installed_public_behavior_claimed": False,
    }
    assert receipt["accepted_archive_queried"] is False
    assert receipt["project_candidate_before"] == receipt["project_candidate_after"]
    assert receipt["project_pointer_before"] == receipt["project_pointer_after"]
    assert receipt["source_work_authorized"] is True
    assert receipt["env_uop_ai_action_plane"]["status"] == "PASS"
    assert receipt["env_uop_ai_action_plane"]["counted_as_public_action"] is False
    assert receipt["env_uop_ai_action_plane"]["operator_route_count"] == 14
    assert receipt["mathematical_execution"]["evaluated_result"] is True
    assert receipt["public_action_sdk_separate"] is True
    assert receipt["env_uop_ai_action_plane_separate"] is True
    assert Path(result["receipt_path"]).is_file()

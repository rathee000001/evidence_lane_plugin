from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from evidence_lane_plugin import adaptive_delta_exit as adaptive
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hook_contract import HOOK_EVENT_NAMES
from evidence_lane_plugin.models import RepositoryIdentity
from evidence_lane_plugin.plan_runtime import append_task_formula_event

ACTIVE_TASK = "DELTA-246"
INSTALL_TASK = "DELTA-248"


def _formula_backlog() -> dict:
    return {
        "schema": "evidence-lane.linear-task-backlog.v1",
        "project_id": "adaptive-project",
        "plans": [{"plan_id": "plan-1", "planned_by": "human-test"}],
        "tasks": [
            {
                "task_id": ACTIVE_TASK,
                "sequence": 246,
                "plan_id": "plan-1",
                "task_class": "add_bounded_feature",
                "requested_outcome": "Close one adaptive Delta.",
                "permitted_paths": ["src"],
                "permitted_tools": ["test"],
                "acceptance_checks": ["Adaptive exit passes."],
                "stop_condition": "Stop on mismatch.",
                "status": "ACTIVE",
                "planned_at": "2026-08-21T00:00:00Z",
                "panel_role": "STANDARD",
                "steer_deltas": [],
            }
        ],
    }


def _entry_formula() -> dict:
    return {
        "fired_modes": ["validation"],
        "operators": ["INTERSECTION", "VALIDATE"],
        "bounded_source_locators": ["workspace:src"],
        "sector_locators": ["local_code"],
        "env_uop_terms": ["ENV15", "UOP15"],
        "assumptions": ["exact task binding"],
        "intended_validator": "targeted pytest",
        "expected_result": "PASS",
        "formula_expression": "V_R246(TESTS intersect AUTHORITY) = PASS",
    }


def _exit_formula() -> dict:
    return {
        "formula_expression": "V_R246(TESTS intersect RECEIPTS) = PASS",
        "bounded_input_locators": ["workspace:src", "plan:R246"],
        "modes_fired": ["validation", "source_intake"],
        "operators_fired": ["INTERSECTION", "VALIDATE"],
        "target_outcome": "close one bounded Delta",
        "achieved_outcome": "targeted checks and adaptive refresh passed",
        "delta_ledger": {"PASS": ["tests", "refresh"], "FAIL": [], "OPEN": []},
        "source_freshness": {"query_status": "PASS", "state": "STALE"},
    }


def _goal_projection() -> dict:
    return {
        "goal_projection": {
            "canonical_plan_sha256": "C" * 64,
            "projection_sha256": "D" * 64,
            "rows": [
                {
                    "task_id": ACTIVE_TASK,
                    "number": 246,
                    "status": "in_progress",
                },
                {
                    "task_id": INSTALL_TASK,
                    "number": 248,
                    "status": "pending",
                },
            ],
        },
    }


class _FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._formula_backlog = _formula_backlog()
        self._goal = _goal_projection()
        self._pointer = {"pv_number": 12, "generation": 12}

    def pointer(self, project_id: str) -> SimpleNamespace:
        del project_id
        return SimpleNamespace(as_dict=lambda: deepcopy(self._pointer))

    def backlog_status(self, project_id: str) -> dict:
        del project_id
        return deepcopy(self._goal)

    def config(self, project_id: str) -> SimpleNamespace:
        del project_id
        return SimpleNamespace(repository_path=str(self.root / "repository"))

    def project_root(self, project_id: str) -> Path:
        return self.root / project_id

    def record_task_formula(self, project_id: str, **kwargs: object) -> dict:
        del project_id
        event = append_task_formula_event(self._formula_backlog, **kwargs)
        return {"status": "PASS", "event": event}


class _FakeSessions:
    def load(self, project_id: str, session_id: str) -> SimpleNamespace:
        del project_id, session_id
        return SimpleNamespace(
            candidate_id=None,
            state=SimpleNamespace(value="ACTIVE_EXECUTION"),
            metadata={"current_host_session_id": "host-task-8"},
        )


class _FakeSdk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def invoke(self, *, module_id: str, operation: str, **kwargs: object) -> dict:
        del kwargs
        self.calls.append((module_id, operation))
        return {
            "status": "PASS",
            "module_id": module_id,
            "operation": operation,
            "receipt_sha256": "E" * 64,
            "authority_effects": {"pointer_moved": False},
        }


def _service(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(store=_FakeStore(tmp_path), sessions=_FakeSessions())


def _repository_identity() -> RepositoryIdentity:
    return RepositoryIdentity(
        provider="git",
        repository_url="local://repository",
        owner="local",
        name="repository",
        branch="branch",
        commit_sha="1" * 40,
        tree_sha="2" * 40,
        is_clean=False,
    )


def test_adaptive_delta_exit_closes_formula_with_all_current_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    entry = append_task_formula_event(
        service.store._formula_backlog,
        task_id=ACTIVE_TASK,
        event_kind="ENTRY_FORMULA",
        source_event_id="entry-1",
        session_id="session-1",
        formula=_entry_formula(),
        actor="human-test",
    )
    sdk = _FakeSdk()
    monkeypatch.setattr(
        adaptive,
        "build_live_local_sdk_context",
        lambda *args, **kwargs: (sdk, object()),
    )
    monkeypatch.setattr(
        adaptive,
        "inspect_repository",
        lambda *args, **kwargs: _repository_identity(),
    )
    monkeypatch.setattr(
        adaptive,
        "prepare_host_plan_rehydration",
        lambda *args, **kwargs: {
            "state": "REHYDRATION_RECEIPT_SEALED",
            "receipt": {
                "status": "PASS",
                "action": "NO_HOST_PLAN_ACTION_REUSE_CURRENT_WINDOW",
                "host_update_plan_required": False,
                "projection": {
                    "window_task_ids": [ACTIVE_TASK],
                    "window_ui_fingerprint_sha256": "F" * 64,
                },
            },
        },
    )

    result = adaptive.run_adaptive_delta_exit(
        service,
        "adaptive-project",
        "session-1",
        task_id=ACTIVE_TASK,
        source_event_id="exit-1",
        prior_formula_sha256=entry["formula_sha256"],
        formula=_exit_formula(),
        validator_results=[
            {
                "name": "pytest",
                "status": "PASS",
                "evidence_locator": "test:test_adaptive_delta_exit",
                "receipt_sha256": "A" * 64,
            }
        ],
        install_disposition={
            "status": "DEFERRED_TO_VERIFIED_BATCH",
            "source_scope_sha256": "B" * 64,
            "deferred_to_task_id": INSTALL_TASK,
            "covered_task_ids": [ACTIVE_TASK],
            "reason": "Grouped local-testing installation closes at R248.",
        },
        fixed_window_task_ids=[ACTIVE_TASK],
        event_id="exit-formula-1",
    )

    receipt = result["receipt"]
    assert result["status"] == "PASS"
    assert receipt["hook_registry_count"] == len(HOOK_EVENT_NAMES) == 8
    assert [row["hook_name"] for row in receipt["hook_progression"]] == list(
        HOOK_EVENT_NAMES
    )
    assert len({row["hook_name"] for row in receipt["hook_progression"]}) == 8
    assert all(
        row["state"] == "UNCHANGED_INACTIVE" for row in receipt["hook_progression"]
    )
    assert sdk.calls == list(adaptive._SDK_OPERATIONS)
    assert receipt["install_disposition"]["install_performed"] is False
    assert receipt["plan_task_advanced"] is False
    assert receipt["git_mutated"] is False
    assert Path(result["receipt_path"]).is_file()
    assert (
        service.store._formula_backlog["task_formula_events"][-1]["event_kind"]
        == "EXIT_FORMULA"
    )


def test_hook_progression_rejects_missing_or_collapsed_registry() -> None:
    collapsed = [
        {
            "hook_name": "Hook 1",
            "state": "UNCHANGED_INACTIVE",
            "verification_status": "UNVERIFIED",
        }
        for _ in HOOK_EVENT_NAMES
    ]
    with pytest.raises(EvidenceLaneError) as raised:
        adaptive._hook_progression(collapsed)
    assert raised.value.code == "ADAPTIVE_DELTA_EXIT_HOOK_REGISTRY_MISMATCH"


def test_install_deferral_requires_later_queued_target() -> None:
    with pytest.raises(EvidenceLaneError) as raised:
        adaptive._install_disposition(
            {
                "status": "DEFERRED_TO_VERIFIED_BATCH",
                "source_scope_sha256": "B" * 64,
                "deferred_to_task_id": ACTIVE_TASK,
                "covered_task_ids": [ACTIVE_TASK],
                "reason": "Invalid self-deferral.",
            },
            active={"task_id": ACTIVE_TASK, "number": 246},
            goal_rows=_goal_projection()["goal_projection"]["rows"],
        )
    assert raised.value.code == "ADAPTIVE_DELTA_EXIT_INSTALL_DEFERRAL_INVALID"

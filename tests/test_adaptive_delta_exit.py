from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from evidence_lane_plugin import adaptive_delta_exit as adaptive
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hook_contract import HOOK_EVENT_NAMES
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS
from evidence_lane_plugin.models import RepositoryIdentity
from evidence_lane_plugin.plan_runtime import append_task_formula_event
from evidence_lane_plugin.service import EvidenceLaneService

ACTIVE_TASK = "DELTA-246"
INSTALL_TASK = "DELTA-248"


def test_delta_exit_enforces_row_scoped_regression_and_install_caps() -> None:
    active = {"task_id": ACTIVE_TASK, "number": 246, "git_commit_stage": "NO_COMMIT"}
    goal_rows = [active]
    verified = {
        "status": "VERIFIED_LOCAL_TESTING_INSTALL",
        "source_scope_sha256": "A" * 64,
        "package_sha256": "B" * 64,
        "catalog_sha256": "C" * 64,
        "runtime_sha256": "D" * 64,
        "task_binding_sha256": "E" * 64,
        "model_visible_schema_sha256": "F" * 64,
        "public_action_matrix_sha256": "1" * 64,
        "installed_host_status": "PASS",
        "exact_task_reattachment_status": "PASS",
        "local_install_attempt_count": 2,
        "full_regression_required": True,
        "full_regression_run_count": 1,
    }
    receipt = adaptive._install_disposition(
        verified,
        active=active,
        goal_rows=goal_rows,
    )
    assert receipt["local_install_max"] == 2
    assert receipt["regression_repeated_per_linked_steer"] is False
    assert receipt["installed_public_behavior_claim_status"] == "PASS"

    with pytest.raises(EvidenceLaneError) as source_only_install:
        adaptive._install_disposition(
            {
                key: value
                for key, value in verified.items()
                if key != "public_action_matrix_sha256"
            },
            active=active,
            goal_rows=goal_rows,
        )
    assert source_only_install.value.code == "ADAPTIVE_DELTA_EXIT_HASH_INVALID"

    with pytest.raises(EvidenceLaneError) as install_loop:
        adaptive._install_disposition(
            {**verified, "local_install_attempt_count": 3},
            active=active,
            goal_rows=goal_rows,
        )
    assert install_loop.value.code == (
        "ADAPTIVE_DELTA_EXIT_LOCAL_INSTALL_CADENCE_EXCEEDED"
    )

    with pytest.raises(EvidenceLaneError) as regression_loop:
        adaptive._install_disposition(
            {**verified, "full_regression_run_count": 3},
            active=active,
            goal_rows=goal_rows,
        )
    assert regression_loop.value.code == (
        "ADAPTIVE_DELTA_EXIT_FULL_REGRESSION_CADENCE_EXCEEDED"
    )

    git_receipt = adaptive._install_disposition(
        {**verified, "local_install_attempt_count": 3},
        active={**active, "git_commit_stage": "FEATURE_BRANCH_CI_MAIN_UPGRADE"},
        goal_rows=goal_rows,
    )
    assert git_receipt["local_install_max"] == 3


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
        "schema": "evidence-lane.executable-delta-entry-formula.v2",
        "fired_modes": ["VAL"],
        "modes_fired": ["VAL"],
        "operators": [],
        "operators_fired": [],
        "bounded_source_locators": ["workspace:src"],
        "sector_locators": ["local_code"],
        "env_uop_terms": ["ENV15", "UOP15"],
        "assumptions": ["exact task binding"],
        "intended_validator": "targeted pytest",
        "expected_result": "PASS",
        "formula_expression": "V_R246(TESTS intersect AUTHORITY) = PASS",
        "env_uop_runtime_execution": {
            "status": "PASS",
            "plane_role": "INTERNAL_AI_ACTION_PLANE_BETWEEN_SQLITE_AND_WORK",
            "counted_as_public_action": False,
            "receipt_sha256": "1" * 64,
            "compiled_formula": {"compiled_formula_sha256": "2" * 64},
            "operator_route_receipts": [],
            "operator_route_count": 0,
        },
        "mathematical_execution": {
            "status": "PASS",
            "evaluated_result": True,
            "null_execution": False,
            "receipt_sha256": "3" * 64,
        },
        "public_action_sdk_separate": True,
        "env_uop_ai_action_plane_separate": True,
        "source_work_authorized": True,
        "accepted_archive_queried": False,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
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
        return SimpleNamespace(
            accepted_pv="PV12",
            generation=12,
            as_dict=lambda: deepcopy(self._pointer),
        )

    def backlog_status(self, project_id: str) -> dict:
        del project_id
        return deepcopy(self._goal)

    def config(self, project_id: str) -> SimpleNamespace:
        del project_id
        return SimpleNamespace(repository_path=str(self.root / "repository"))

    def project_root(self, project_id: str) -> Path:
        return self.root / project_id

    def _candidate_overlay_receipt_path(
        self, project_id: str, candidate_id: str
    ) -> Path:
        return (
            self.project_root(project_id)
            / "receipts"
            / "project-overlay"
            / f"{candidate_id}.json"
        )

    def plan_runtime_query(self, project_id: str, **kwargs: object) -> dict:
        del project_id, kwargs
        return {
            "formula_events": deepcopy(
                self._formula_backlog["task_formula_events"]
            )
        }

    def record_task_formula(self, project_id: str, **kwargs: object) -> dict:
        del project_id
        event = append_task_formula_event(self._formula_backlog, **kwargs)
        return {"status": "PASS", "event": event}


class _FakeSessions:
    def __init__(
        self,
        *,
        candidate_id: str | None = None,
        state: str = "ACTIVE_EXECUTION",
    ) -> None:
        self.candidate_id = candidate_id
        self.state = state

    def load(self, project_id: str, session_id: str) -> SimpleNamespace:
        del project_id, session_id
        return SimpleNamespace(
            candidate_id=self.candidate_id,
            state=SimpleNamespace(value=self.state),
            metadata={"current_host_session_id": "host-task-8"},
        )


class _FakeSdk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def invoke(self, *, module_id: str, operation: str, **kwargs: object) -> dict:
        del kwargs
        self.calls.append((module_id, operation))
        data: dict[str, object] = {}
        if (module_id, operation) in {
            ("agent_learning", "retrieve"),
            ("project_memory", "query"),
        }:
            data = {
                "status": "PASS",
                "result": "NO_HIT",
                "hits": [],
                "suppressed": [],
                "full_ledger_loaded_into_model_context": False,
            }
        elif (module_id, operation) == ("canon_input", "graph"):
            data = {
                "status": "PASS",
                "contract_count": 0,
                "packet_count": 0,
                "edge_count": 0,
                "consequence_graph": {"status": "PASS", "state": "CURRENT"},
            }
        return {
            "status": "PASS",
            "module_id": module_id,
            "operation": operation,
            "receipt_sha256": "E" * 64,
            "authority_effects": {"pointer_moved": False},
            "data": data,
        }


def _service(
    tmp_path: Path,
    *,
    candidate_id: str | None = None,
) -> SimpleNamespace:
    project_root = tmp_path / "adaptive-project"
    project_root.mkdir(parents=True, exist_ok=True)
    connector_root = project_root / "connector_brain"
    connector_root.mkdir(parents=True, exist_ok=True)
    (connector_root / "connector-brain.sqlite").write_bytes(b"connector-brain")
    store = _FakeStore(tmp_path)
    if candidate_id is not None:
        candidate_receipt = store._candidate_overlay_receipt_path(
            "adaptive-project", candidate_id
        )
        candidate_receipt.parent.mkdir(parents=True, exist_ok=True)
        candidate_receipt.write_text(
            '{"candidate_id":"' + candidate_id + '","status":"PENDING"}',
            encoding="utf-8",
        )
    return SimpleNamespace(
        store=store,
        sessions=_FakeSessions(
            candidate_id=candidate_id,
            state=("PVN1_CANDIDATE" if candidate_id else "ACTIVE_EXECUTION"),
        ),
        connector_plugin_catalog=lambda project_id: {
            "status": "PASS",
            "project_id": project_id,
            "active_count": 0,
            "routable_count": 0,
            "registrations": [],
            "integrity": ["ok"],
            "foreign_key_errors": [],
            "secret_values_persisted": False,
        },
        _refresh_delta_source_authority=lambda project_id, session_id, task_id: {
            "schema": "evidence-lane.delta-source-authority-refresh.v1",
            "status": "PASS",
            "project_id": project_id,
            "session_id": session_id,
            "task_id": task_id,
            "source_planes": [
                {
                    "lane_id": "local_code",
                    "status": "PASS",
                    "receipt_sha256": "8" * 64,
                },
                {
                    "lane_id": "github_code",
                    "status": "PASS",
                    "receipt_sha256": "9" * 64,
                },
            ],
            "canonical_lane_refresh": {
                "authority_scope": "ALL_18_CANONICAL_LANES",
                "refresh_action": "WORKING_TO_WORKING_INCREMENTAL_REFRESH",
                "canonical_lane_count": len(CANONICAL_LANE_IDS),
                "emitted_lane_ids": list(CANONICAL_LANE_IDS),
                "lane_report_count": len(CANONICAL_LANE_IDS),
                "lane_reports": [
                    {
                        "lane_id": lane_id,
                        "build_mode": "UNCHANGED_REUSE",
                        "full_validation_fallback_reason": None,
                        "byte_reused": True,
                    }
                    for lane_id in CANONICAL_LANE_IDS
                ],
                "full_validation_fallbacks": [],
                "build_parent_kind": "CURRENT_VALIDATED_WORKING_SECTORS",
            },
            "receipt_sha256": "7" * 64,
        },
    )


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
    monkeypatch.setattr(
        adaptive,
        "query_live_authorities",
        lambda *args, **kwargs: {
            "status": "PASS",
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "initial_reads": [
                {"module_id": "agent_learning", "status": "PASS"},
                {"module_id": "project_memory", "status": "PASS"},
                {"module_id": "canon_input", "status": "PASS"},
            ],
            "authorities": {
                "sector_lanes": {
                    "result": {
                        "status": "PASS",
                        "queried_lane_ids": list(CANONICAL_LANE_IDS),
                        "hits": [],
                        "query_mutated_project_authority": False,
                        "query_rehashed_dirty_content": False,
                    }
                }
            },
        },
    )
    monkeypatch.setattr(
        adaptive,
        "refresh_delta_exit_authority_supports",
        lambda *args, **kwargs: {
            "schema": "evidence-lane.delta-exit-authority-support-refresh.v1",
            "status": "PASS",
            "authority_ids": [
                "agent_learning",
                "canon_input",
                "project_memory",
                "source_authority",
                "project_universe",
                "connector_brain",
            ],
            "receipts": [],
            "project_overlay_refreshed": False,
            "receipt_sha256": "6" * 64,
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
    assert receipt["hook_registry_count"] == len(HOOK_EVENT_NAMES) == 11
    assert [row["hook_name"] for row in receipt["hook_progression"]] == list(
        HOOK_EVENT_NAMES
    )
    assert len({row["hook_name"] for row in receipt["hook_progression"]}) == 11
    assert all(
        row["state"] == "UNCHANGED_INACTIVE" for row in receipt["hook_progression"]
    )
    assert sdk.calls == list(adaptive._SDK_OPERATIONS)
    assert [
        row["lane_id"]
        for row in receipt["source_authority_refresh"]["source_planes"]
    ] == ["local_code", "github_code"]
    assert receipt["source_authority_refresh"]["canonical_lane_refresh"][
        "emitted_lane_ids"
    ] == list(CANONICAL_LANE_IDS)
    assert receipt["source_authority_refresh"]["canonical_lane_refresh"][
        "lane_report_count"
    ] == 18
    assert receipt["plan_runtime_authority_state"] == (
        "LIVE_CURRENT_EXECUTION_AUTHORITY"
    )
    assert [
        row["module_id"] for row in receipt["decision_support_receipts"]
    ] == ["agent_learning", "project_memory", "canon_input"]
    assert receipt["working_sector_fallback_receipt"]["queried_lane_ids"] == list(
        CANONICAL_LANE_IDS
    )
    assert receipt["install_disposition"]["install_performed"] is False
    assert receipt["verification_layers"] == {
        "source_validator_status": "PASS",
        "source_validator_scope": "IMPLEMENTATION_SOURCE_ONLY",
        "sector_refresh_status": "PASS",
        "sector_refresh_represents_current_delta": True,
        "installed_public_behavior_status": "NOT_CLAIMED",
        "installed_public_behavior_requires_local_package": True,
        "source_tests_substitute_for_installed_host": False,
    }
    assert receipt["connector_brain_refresh"]["status"] == "PASS"
    assert receipt["authority_support_refresh"]["status"] == "PASS"
    assert receipt["project_overlay_disposition"] == {
        "status": "NOT_RUN_NON_HIL_DELTA",
        "hil_delta": False,
        "refresh_owner": None,
        "project_overlay_refreshed_during_ordinary_delta_exit": False,
    }
    assert receipt["plan_task_advanced"] is False
    assert receipt["git_mutated"] is False
    assert Path(result["receipt_path"]).is_file()
    assert (
        service.store._formula_backlog["task_formula_events"][-1]["event_kind"]
        == "EXIT_FORMULA"
    )


def test_adaptive_delta_exit_repairs_legacy_formula_without_clearing_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_id = "PV13_HIL_PROPOSAL__PRESERVE_IN_PLACE"
    service = _service(tmp_path, candidate_id=candidate_id)
    legacy_formula = {
        "fired_modes": ["analysis", "code"],
        "operators": ["INTERSECTION", "VALIDATE"],
        "bounded_source_locators": ["workspace:src"],
        "sector_locators": ["local_code"],
        "env_uop_terms": ["ENV15", "UOP15"],
        "assumptions": ["candidate remains pending"],
        "intended_validator": "legacy R265 validation",
        "expected_result": "PASS",
        "formula_expression": "V_R265(LEGACY_ENTRY) = PASS",
    }
    entry = append_task_formula_event(
        service.store._formula_backlog,
        task_id=ACTIVE_TASK,
        event_kind="ENTRY_FORMULA",
        source_event_id="legacy-entry-1",
        session_id="session-1",
        formula=legacy_formula,
        actor="human-test",
    )
    candidate_path = service.store._candidate_overlay_receipt_path(
        "adaptive-project", candidate_id
    )
    candidate_bytes_before = candidate_path.read_bytes()

    def repair_entry(*args: object, **kwargs: object) -> dict:
        del args, kwargs
        persisted = service.store.record_task_formula(
            "adaptive-project",
            task_id=ACTIVE_TASK,
            event_kind="MUTATION",
            source_event_id="candidate-preserving-entry-repair",
            session_id="session-1",
            formula=_entry_formula(),
            actor="candidate-preserving-repair",
            prior_formula_sha256=entry["formula_sha256"],
            changed_terms={"env_uop_runtime": "EXECUTABLE"},
            cause_evidence_locator="delta-entry://candidate-preserving-repair",
            event_id="candidate-preserving-entry-repair",
        )
        event = persisted["event"]
        return {
            "status": "PASS",
            "receipt": {
                "status": "PASS",
                "formula_disposition": (
                    "LEGACY_OPEN_ENTRY_SUPERSEDED_BY_EXECUTABLE_MUTATION"
                ),
                "formula_sha256": event["formula_sha256"],
                "receipt_sha256": "4" * 64,
            },
        }

    sdk = _FakeSdk()
    monkeypatch.setattr(adaptive, "run_adaptive_delta_entry", repair_entry)
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
    monkeypatch.setattr(
        adaptive,
        "query_live_authorities",
        lambda *args, **kwargs: {
            "status": "PASS",
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "initial_reads": [
                {"module_id": "agent_learning", "status": "PASS"},
                {"module_id": "project_memory", "status": "PASS"},
                {"module_id": "canon_input", "status": "PASS"},
            ],
            "authorities": {
                "sector_lanes": {
                    "result": {
                        "status": "PASS",
                        "queried_lane_ids": list(CANONICAL_LANE_IDS),
                        "hits": [],
                        "query_mutated_project_authority": False,
                        "query_rehashed_dirty_content": False,
                    }
                }
            },
        },
    )
    monkeypatch.setattr(
        adaptive,
        "refresh_delta_exit_authority_supports",
        lambda *args, **kwargs: {
            "schema": "evidence-lane.delta-exit-authority-support-refresh.v1",
            "status": "PASS",
            "authority_ids": [
                "agent_learning",
                "canon_input",
                "project_memory",
                "source_authority",
                "project_universe",
                "connector_brain",
            ],
            "receipts": [],
            "project_overlay_refreshed": False,
            "receipt_sha256": "6" * 64,
        },
    )

    formula = {
        **_exit_formula(),
        "hil_delta": True,
        "preexisting_candidate_preservation": {
            "schema": (
                "evidence-lane.pending-candidate-delta-exit-preservation.v1"
            ),
            "confirmation": "PRESERVE_PENDING_CANDIDATE_DURING_DELTA_EXIT",
            "candidate_id": candidate_id,
            "reason": "Append R265 exit evidence without clearing its proposal.",
        },
        "entry_formula_repair": {
            "schema": "evidence-lane.legacy-entry-formula-delta-exit-repair.v1",
            "confirmation": "REPAIR_LEGACY_ENTRY_FORMULA_IN_PLACE",
            "prior_formula_sha256": entry["formula_sha256"],
            "candidate_id": candidate_id,
            "reason": "Upgrade the open legacy R265 formula through adaptive entry.",
        },
    }
    result = adaptive.run_adaptive_delta_exit(
        service,
        "adaptive-project",
        "session-1",
        task_id=ACTIVE_TASK,
        source_event_id="candidate-preserving-exit-1",
        prior_formula_sha256=entry["formula_sha256"],
        formula=formula,
        validator_results=[
            {
                "name": "pytest",
                "status": "PASS",
                "evidence_locator": "test:candidate-preserving-exit",
                "receipt_sha256": "A" * 64,
            }
        ],
        install_disposition={
            "status": "DEFERRED_TO_VERIFIED_BATCH",
            "source_scope_sha256": "B" * 64,
            "deferred_to_task_id": INSTALL_TASK,
            "covered_task_ids": [ACTIVE_TASK],
            "reason": "Grouped install remains at the existing verified boundary.",
        },
        fixed_window_task_ids=[ACTIVE_TASK],
        event_id="candidate-preserving-exit-formula-1",
    )

    receipt = result["receipt"]
    assert result["status"] == "PASS"
    assert service.sessions.candidate_id == candidate_id
    assert service.sessions.state == "PVN1_CANDIDATE"
    assert candidate_path.read_bytes() == candidate_bytes_before
    assert receipt["candidate_preserved"] is True
    assert receipt["candidate_cleared"] is False
    assert receipt["candidate_rebuilt"] is False
    assert receipt["candidate_renamed"] is False
    assert receipt["pending_hil_mutated"] is False
    assert receipt["preexisting_candidate_preservation"]["candidate_id"] == (
        candidate_id
    )
    assert receipt["entry_formula_repair"]["status"] == "PASS"
    assert receipt["requested_prior_formula_sha256"] == entry["formula_sha256"]
    assert receipt["effective_prior_formula_sha256"] != entry["formula_sha256"]
    assert receipt["project_overlay_disposition"]["status"] == (
        "HIL_CANDIDATE_BUILD_REQUIRED"
    )
    assert [
        row["event_kind"]
        for row in service.store._formula_backlog["task_formula_events"]
    ] == ["ENTRY_FORMULA", "MUTATION", "EXIT_FORMULA"]


def test_formula_identity_mismatch_fails_before_any_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    entry = append_task_formula_event(
        service.store._formula_backlog,
        task_id=ACTIVE_TASK,
        event_kind="ENTRY_FORMULA",
        source_event_id="entry-missing-task-id",
        session_id="session-1",
        formula=_entry_formula(),
        actor="human-test",
    )
    service.store._formula_backlog["task_formula_events"][0].pop("task_id")
    refresh_calls: list[str] = []
    service._refresh_delta_source_authority = (
        lambda *args, **kwargs: refresh_calls.append("source")
    )
    monkeypatch.setattr(
        adaptive,
        "build_live_local_sdk_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("SDK refresh must not start before formula validation")
        ),
    )
    monkeypatch.setattr(
        adaptive,
        "inspect_repository",
        lambda *args, **kwargs: _repository_identity(),
    )

    with pytest.raises(EvidenceLaneError) as raised:
        adaptive.run_adaptive_delta_exit(
            service,
            "adaptive-project",
            "session-1",
            task_id=ACTIVE_TASK,
            source_event_id="exit-missing-task-id",
            prior_formula_sha256=entry["formula_sha256"],
            formula=_exit_formula(),
            validator_results=[
                {
                    "name": "pytest",
                    "status": "PASS",
                    "evidence_locator": "test:formula-preflight",
                    "receipt_sha256": "A" * 64,
                }
            ],
            install_disposition={
                "status": "DEFERRED_TO_VERIFIED_BATCH",
                "source_scope_sha256": "B" * 64,
                "deferred_to_task_id": INSTALL_TASK,
                "covered_task_ids": [ACTIVE_TASK],
                "reason": "Grouped install remains pending.",
            },
        )

    assert raised.value.code == "ADAPTIVE_DELTA_EXIT_ENTRY_FORMULA_TIMESTAMP_REQUIRED"
    assert refresh_calls == []
    assert len(service.store._formula_backlog["task_formula_events"]) == 1


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


def test_delta_source_refresh_rejects_wrong_active_task_before_repository_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_work_entered = False

    def forbidden_repository_work(*args: object, **kwargs: object) -> object:
        nonlocal repository_work_entered
        del args, kwargs
        repository_work_entered = True
        raise AssertionError("repository work entered before exact-task rejection")

    service = SimpleNamespace(
        sessions=SimpleNamespace(
            load=lambda project_id, session_id: SimpleNamespace(
                metadata={"active_backlog_task_id": "OTHER-TASK"}
            )
        ),
        store=SimpleNamespace(
            backlog_status=lambda project_id: {
                "goal_projection": {
                    "rows": [
                        {
                            "task_id": ACTIVE_TASK,
                            "status": "in_progress",
                        }
                    ]
                }
            }
        ),
    )
    monkeypatch.setattr(
        "evidence_lane_plugin.service.inspect_repository",
        forbidden_repository_work,
    )
    monkeypatch.setattr(
        "evidence_lane_plugin.service.migrate_working_project_sectors",
        forbidden_repository_work,
    )

    with pytest.raises(EvidenceLaneError) as raised:
        EvidenceLaneService._refresh_delta_source_authority(
            service,
            "adaptive-project",
            "session-1",
            task_id=ACTIVE_TASK,
        )

    assert raised.value.code == "DELTA_SOURCE_REFRESH_TASK_BINDING_MISMATCH"
    assert repository_work_entered is False

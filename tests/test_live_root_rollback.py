from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.plan_runtime import (
    append_delta_event,
    append_sub_pv_acceptance,
)
from evidence_lane_plugin.project_pv_storage import build_project_pv_archive

from .conftest import build_and_approve_pv1
from .test_lifecycle import (
    _materialize_live_root_sectors,
    _relocate_live_project_root,
)


def _task(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_class": "fix_bug",
        "requested_outcome": f"Verify {task_id}",
        "permitted_paths": ["src"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["bounded PASS"],
        "stop_condition": "stop on mismatch",
    }


def test_live_root_logical_rollback_uses_plan_subpv_without_accepted_folder(
    service,
    source_repository: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    live_root = tmp_path / "live-projects" / "book-faires"
    _relocate_live_project_root(service, source_repository, live_root)
    _materialize_live_root_sectors(service, source_repository)
    service.plan_tasks(
        "book-faires",
        tasks=[_task("delta-one"), _task("delta-two")],
        planned_by="human-test",
        plan_id="rollback-subpv-plan",
    )
    backlog = service.store._load_backlog("book-faires")
    append_delta_event(
        backlog,
        task_id="delta-one",
        event_type="TASK_ACTIVE",
        to_status="ACTIVE",
        actor=session_id,
        event_id="delta-one-active",
    )
    completion = append_delta_event(
        backlog,
        task_id="delta-one",
        event_type="VERIFIED_TASK_CHECKPOINT_COMPLETED",
        to_status="DONE",
        actor=session_id,
        event_id="delta-one-done",
        details={"completion_receipt_sha256": "A" * 64},
    )
    task = next(row for row in backlog["tasks"] if row["task_id"] == "delta-one")
    task["task_checkpoint_completion_receipt_sha256"] = "A" * 64
    record = append_sub_pv_acceptance(
        backlog,
        task_id="delta-one",
        successor_task_id="delta-two",
        session_id=session_id,
        baseline_pv="PV1",
        pointer_generation=1,
        task_checkpoint_completion_receipt_sha256="A" * 64,
        verification_proof_sha256="B" * 64,
        delta_completion_event_sha256=completion["event_sha256"],
        accepted_at="2026-08-26T00:00:00Z",
    )
    service.store._persist_backlog("book-faires", backlog)

    def forbidden(*args, **kwargs):
        raise AssertionError("logical rollback queried accepted storage")

    monkeypatch.setattr(service.store, "accepted_ids", forbidden)
    monkeypatch.setattr(service.store, "accepted_path", forbidden)
    monkeypatch.setattr(service.store, "validate_accepted", forbidden)

    catalog = service.store.rollback_state_catalog("book-faires")
    assert catalog["status"] == "PASS"
    assert catalog["accepted_folder_queried"] is False
    assert catalog["accepted_zip_opened"] is False
    assert catalog["full_pv_state_count"] == 1
    assert catalog["sub_pv_state_count"] == 1
    assert record["sub_pv_id"] in {row["state_ref"] for row in catalog["states"]}

    before = service.store.pointer("book-faires").as_dict()
    rolled = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to=record["sub_pv_id"],
        decision_id="logical-subpv-rollback",
    )
    after = service.store.pointer("book-faires").as_dict()

    assert rolled["status"] == "PASS"
    assert rolled["state"] == "LOGICAL_ROLLBACK_CURSOR_SELECTED"
    assert rolled["cursor"]["state_ref"] == record["sub_pv_id"]
    assert rolled["cursor"]["state_kind"] == "SUB_PV_DELTA"
    assert rolled["pointer_moved"] is False
    assert rolled["receipt"]["project_overlay_applicable"] is False
    assert rolled["receipt"]["hard_restore_requires"] == "UNAVAILABLE_FOR_SUB_PV"
    assert rolled["receipt"]["accepted_folder_queried"] is False
    assert before == after


def test_live_root_full_pv_logical_rollback_links_overlay_not_zip(
    service,
    source_repository: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    live_root = tmp_path / "live-projects" / "book-faires"
    _relocate_live_project_root(service, source_repository, live_root)
    _materialize_live_root_sectors(service, source_repository)

    monkeypatch.setattr(
        service.store,
        "accepted_path",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("logical rollback opened accepted ZIP")
        ),
    )
    rolled = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to="PV1",
        decision_id="logical-full-pv-rollback",
    )

    assert rolled["status"] == "PASS"
    assert rolled["cursor"]["state_ref"] == "PV1"
    assert rolled["receipt"]["project_overlay_applicable"] is True
    assert rolled["receipt"]["hard_restore_performed"] is False
    assert rolled["receipt"]["hard_restore_requires"] == (
        "EXPLICIT_USER_SELECTED_MATCHING_FULL_PV_ZIP"
    )


def test_hard_rollback_mode_requires_complete_explicit_contract(
    service,
    source_repository: Path,
    tmp_path: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    live_root = tmp_path / "live-projects" / "book-faires"
    _relocate_live_project_root(service, source_repository, live_root)
    with pytest.raises(EvidenceLaneError) as error:
        service.rollback(
            "book-faires",
            session_id,
            decided_by="human-test",
            rollback_mode="HARD_ACCEPTED_ZIP_RESTORE",
        )
    assert error.value.code == "HARD_ROLLBACK_ARGUMENTS_REQUIRED"


def test_hard_rollback_restores_fresh_root_and_requires_new_plan(
    service,
    source_repository: Path,
    tmp_path: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    live_root = tmp_path / "live-projects" / "book-faires"
    _relocate_live_project_root(service, source_repository, live_root)
    _materialize_live_root_sectors(service, source_repository)
    service.plan_tasks(
        "book-faires",
        tasks=[
            _task("pv1-stamped-target")
            | {"requested_outcome": "Accepted Project PV1 checkpoint"},
            _task("later-direction"),
        ],
        planned_by="human-test",
        plan_id="hard-rollback-plan",
    )
    archive = tmp_path / "selected-PV1.zip"
    validation = build_project_pv_archive(
        live_root,
        archive,
        project_id="book-faires",
        pv_id="PV1",
        candidate_id="PV1_ACCEPTED_BASELINE",
        parent_accepted_pv=None,
        parent_accepted_manifest_sha256=None,
        pointer_override={
            "project_id": "book-faires",
            "accepted_pv": "PV1",
            "accepted_manifest_sha256": service.store.pointer(
                "book-faires"
            ).accepted_manifest_sha256,
            "generation": 1,
            "prior_generation": 0,
        },
    )
    restored_root = tmp_path / "restored-projects" / "book-faires"
    result = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_mode="HARD_ACCEPTED_ZIP_RESTORE",
        archive_path=str(archive),
        expected_archive_sha256=sha256_file(archive),
        restore_root=str(restored_root),
        target_plan_task_id="pv1-stamped-target",
        confirmation="RESTORE_EXACT_ACCEPTED_ZIP_AND_REQUIRE_NEW_PLAN",
        decision_id="hard-pv1-restore",
    )
    assert validation["status"] == "PASS"
    assert result["status"] == "PASS"
    assert result["state"] == "HARD_RESTORE_COMPLETE_REPLAN_REQUIRED"
    assert result["replan_required"] is True
    assert service.store.project_root("book-faires") == restored_root.resolve()
    assert (restored_root / "accepted").is_dir()
    assert len(list((restored_root / "accepted").glob("PV1__*.zip"))) == 1
    backlog = service.store._load_backlog("book-faires")
    later = next(row for row in backlog["tasks"] if row["task_id"] == "later-direction")
    assert later["status"] == "SUPERSEDED"
    assert backlog["rollback_replan_gates"][-1]["fresh_evi_plan_required"] is True


def test_git_rollback_restores_fresh_workspace_rebuilds_local_code_and_replans(
    service,
    source_repository: Path,
    tmp_path: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    live_root = tmp_path / "live-projects" / "book-faires"
    _relocate_live_project_root(service, source_repository, live_root)
    _materialize_live_root_sectors(service, source_repository)
    commit = subprocess.run(
        ["git", "-C", str(source_repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", str(source_repository), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    service.plan_tasks(
        "book-faires",
        tasks=[
            _task("git-stamped-target")
            | {"requested_outcome": f"Verified Git commit {commit}"},
            _task("rejected-later-direction"),
        ],
        planned_by="human-test",
        plan_id="git-rollback-plan",
    )
    workspace = tmp_path / "fresh-workspaces" / "book-faires-restored"
    result = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_mode="GIT_BRANCH_COMMIT_RESTORE",
        repository_path=str(source_repository),
        branch=branch,
        commit_sha=commit,
        restore_workspace=str(workspace),
        target_plan_task_id="git-stamped-target",
        confirmation=(
            "RESTORE_EXACT_GIT_COMMIT_TO_FRESH_WORKSPACE_AND_REQUIRE_NEW_PLAN"
        ),
        decision_id="git-commit-restore",
    )
    assert result["status"] == "PASS"
    assert result["state"] == "GIT_RESTORE_COMPLETE_REPLAN_REQUIRED"
    assert result["pointer_moved"] is False
    assert workspace.is_dir()
    assert subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip() == commit
    config = json.loads((live_root / "project.json").read_text(encoding="utf-8"))
    assert Path(config["repository_path"]).resolve() == workspace.resolve()
    backlog = service.store._load_backlog("book-faires")
    later = next(
        row
        for row in backlog["tasks"]
        if row["task_id"] == "rejected-later-direction"
    )
    assert later["status"] == "SUPERSEDED"
    sectors = live_root / "sectors"
    assert (sectors / "local_code").is_dir()
    assert (sectors / "local_code" / "local_code_sector_v001.sqlite").is_file()

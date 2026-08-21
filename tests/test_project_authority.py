from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS
from evidence_lane_plugin.project_authority import (
    PROJECT_AUTHORITY_CONFIRMATION,
    migrate_working_project_sectors,
    query_working_project_sectors,
)
from evidence_lane_plugin.project_pv_storage import (
    validate_project_pv_archive,
    working_overlay_manifest,
)

from .conftest import build_and_approve_pv1, git


def _relocate(service, source_repository: Path, target: Path) -> dict:
    return service.register_project(
        project_id="book-faires",
        display_name="Book Faires",
        repository_path=str(source_repository),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
        project_authority_root=str(target),
        project_authority_migration_confirmation=PROJECT_AUTHORITY_CONFIRMATION,
        expected_accepted_pv="PV1",
        expected_pointer_generation=1,
        selected_by="human-test",
    )


def test_project_register_relocates_active_authority_without_shadow_payload(
    service, source_repository: Path, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    legacy = service.store.project_root("book-faires")
    pointer_before = (legacy / "active_pointer.json").read_bytes()
    (legacy / "accepted" / "PV0").mkdir(parents=True)
    (legacy / "accepted" / "PV0" / "history.txt").write_text(
        "historical only\n", encoding="utf-8"
    )
    target = tmp_path / "user-projects" / "book-faires"

    result = _relocate(service, source_repository, target)

    assert result["status"] == "PASS"
    assert result["state"] == "REGISTERED_PROJECT_AUTHORITY_RELOCATED"
    assert service.store.project_root("book-faires") == target.resolve()
    assert service.store.project_authority_routes() == [
        ("book-faires", target.resolve())
    ]
    assert (target / "active_pointer.json").read_bytes() == pointer_before
    assert not (target / "candidates").exists()
    assert (legacy / "accepted" / "PV0" / "history.txt").is_file()
    assert not (legacy / "accepted" / "PV1").exists()
    legacy_marker = json.loads(
        (legacy / "NON_AUTHORITATIVE_LEGACY_HISTORY.json").read_text(
            encoding="utf-8"
        )
    )
    assert legacy_marker["accepted_pointer_authority"] is False
    assert legacy_marker["candidate_authority"] is False

    layout = json.loads(
        (target / "project_authority.json").read_text(encoding="utf-8")
    )
    assert layout["canonical_lane_count"] == len(CANONICAL_LANE_IDS) == 18
    assert layout["materialized_sector_directory_count"] == 18
    assert layout["study_brain"]["stored_lane_created"] is False
    assert not (target / "sectors" / "study_brain").exists()
    assert {
        path.name for path in (target / "sectors").iterdir() if path.is_dir()
    } == set(CANONICAL_LANE_IDS)
    for lane_id in CANONICAL_LANE_IDS:
        reference = json.loads(
            (target / "sectors" / lane_id / "authority.ref.json").read_text(
                encoding="utf-8"
            )
        )
        assert reference["state"] in {
            "ACCEPTED_MATERIALIZED",
            "SCHEMA_READY_UNPOPULATED",
        }
        assert reference["empty_lane_payload_fabricated"] is False

    inspected = service.storage_connector_inspect("book-faires")
    assert inspected["project_route"]["project_authority_mode"] == (
        "EXPLICIT_USER_PROJECT_ROOT"
    )
    assert inspected["project_route"]["runtime_separated"] is True
    assert inspected["project_authority"]["layout_materialized"] is True
    assert service.store.pointer("book-faires").accepted_pv == "PV1"
    assert service.store.pointer("book-faires").generation == 1


def test_project_authority_relocation_fails_closed_on_wrong_confirmation(
    service, source_repository: Path, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    legacy = service.store.project_root("book-faires")
    pointer_before = (legacy / "active_pointer.json").read_bytes()
    target = tmp_path / "user-projects" / "book-faires"

    with pytest.raises(EvidenceLaneError) as raised:
        service.register_project(
            project_id="book-faires",
            display_name="Book Faires",
            repository_path=str(source_repository),
            expected_owner="example",
            expected_name="book-faires",
            allowed_branches=["main"],
            project_authority_root=str(target),
            project_authority_migration_confirmation="MOVE_IT",
            expected_accepted_pv="PV1",
            expected_pointer_generation=1,
            selected_by="human-test",
        )

    assert raised.value.code == "PROJECT_AUTHORITY_MIGRATION_CONFIRMATION_REQUIRED"
    assert service.store.project_root("book-faires") == legacy
    assert (legacy / "active_pointer.json").read_bytes() == pointer_before
    assert not target.exists()


def test_external_project_authority_registration_is_idempotent(
    service, source_repository: Path, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)

    repeated = _relocate(service, source_repository, target)

    assert repeated["status"] == "PASS"
    assert repeated["state"] == "REGISTERED_EXTERNAL_AUTHORITY_IDEMPOTENT_REUSE"
    assert repeated["pointer_moved"] is False
    assert repeated["candidate_created"] is False
    assert repeated["hil_inferred"] is False


def test_external_project_authority_uses_live_overlay_and_one_verified_archive(
    service, source_repository: Path, tmp_path: Path
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    legacy_accepted = target / "accepted" / "PV1"
    legacy_identity = service.store.validate_accepted(
        "book-faires", "PV1", require_promotable=False
    )["manifest_sha256"]

    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Seal the external project root without copied candidates.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=["One verified accepted archive exists."],
        stop_condition="Stop at the explicit test HIL.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    candidate = service.refresh("book-faires", session_id)["candidate"]
    candidate_id = candidate["candidate_id"]
    overlay_receipt = json.loads(
        (
            target
            / "receipts"
            / "candidate-overlays"
            / f"{candidate_id}.json"
        ).read_text(encoding="utf-8")
    )
    expected_rows = {row["path"]: row for row in overlay_receipt["working_members"]}
    current_rows = {
        row["path"]: row
        for row in working_overlay_manifest(target, project_id="book-faires")["members"]
    }
    assert expected_rows == current_rows, {
        "added": sorted(current_rows.keys() - expected_rows.keys()),
        "removed": sorted(expected_rows.keys() - current_rows.keys()),
        "changed": sorted(
            path
            for path in expected_rows.keys() & current_rows.keys()
            if expected_rows[path] != current_rows[path]
        ),
    }
    overlay = service.store.candidate_validation("book-faires", candidate_id)

    assert overlay["storage_kind"] == "LIVE_PROJECT_ROOT_CANDIDATE_OVERLAY"
    assert overlay["candidate_directory_created"] is False
    assert not (target / "candidates").exists()
    assert service.store.candidate_runtime_path("book-faires", candidate_id) == target
    assert legacy_accepted.is_dir()
    assert service.store.pointer("book-faires").accepted_pv == "PV1"

    approved = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_external_pv2",
    )

    assert approved["pointer"]["accepted_pv"] == "PV2"
    assert approved["pointer"]["generation"] == 2
    artifacts = list((target / "accepted").iterdir())
    assert len(artifacts) == 1
    assert artifacts[0].is_file() and artifacts[0].name.startswith("PV2__")
    validation = validate_project_pv_archive(artifacts[0])
    assert validation["status"] == "PASS"
    assert validation["retention"]["accepted_lineage"] == [
        {"pv_id": "PV1", "manifest_sha256": legacy_identity}
    ]
    assert validation["retention"]["prior_accepted_bytes_in_live_history_inferred"] is False
    assert (target / "project_authority.json").is_file()
    assert not (target / "candidates").exists()


def test_external_project_archive_requires_exact_postseal_receipt(
    service, source_repository: Path, tmp_path: Path
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    declaration = "AC12 executable: validate the external-root candidate."
    manifest = source_repository / "evidence" / "acceptance" / "commands.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.acceptance-command-manifest.v1",
                "commands": {
                    declaration: {
                        "argv": [
                            "$RUNTIME_PYTHON",
                            "-c",
                            (
                                "import os,sys,pathlib; p=pathlib.Path("
                                "os.environ.get('EVIDENCE_LANE_CANDIDATE_PATH','')); "
                                "sys.exit(0 if p.is_dir() else 9)"
                            ),
                        ],
                        "phase": "POSTSEAL",
                        "timeout_seconds": 30,
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    git(source_repository, "add", ".")
    git(source_repository, "commit", "-m", "Add external postseal check")
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Prove external-root post-seal promotion gating.",
        permitted_paths=["evidence/acceptance/commands.json"],
        permitted_tools=["repository_read", "test"],
        acceptance_checks=[declaration],
        stop_condition="Stop at the explicit test HIL.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    candidate = service.refresh("book-faires", session_id)["candidate"]
    receipt_path = Path(candidate["postseal_acceptance_receipt"])
    receipt_bytes = receipt_path.read_bytes()
    receipt_path.unlink()

    with pytest.raises(EvidenceLaneError) as missing:
        service.store.promote(
            "book-faires",
            candidate["candidate_id"],
            expected_pointer_generation=1,
            decided_by="human-test",
            decision_id="decision_external_missing_postseal",
        )

    assert missing.value.code == "POSTSEAL_ACCEPTANCE_RECEIPT_REQUIRED"
    assert service.store.pointer("book-faires").accepted_pv == "PV1"
    assert (target / "accepted" / "PV1").is_dir()
    receipt_path.write_bytes(receipt_bytes)
    promoted = service.store.promote(
        "book-faires",
        candidate["candidate_id"],
        expected_pointer_generation=1,
        decided_by="human-test",
        decision_id="decision_external_valid_postseal",
    )
    assert promoted["pointer"]["accepted_pv"] == "PV2"
    assert promoted["receipt"]["postseal_acceptance"]["status"] == "PASS"


def test_working_sector_migration_uses_accepted_parent_and_removes_duplicates(
    service, source_repository: Path, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    pointer_before = (target / "active_pointer.json").read_bytes()

    (target / "task_backlog.json").write_text(
        json.dumps(
            {
                "schema": "evidence-lane.linear-task-backlog.v1",
                "project_id": "book-faires",
                "plans": [],
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    plan = sqlite3.connect(target / "plan_runtime_projection.sqlite")
    plan.execute("CREATE TABLE plan_row(task_id TEXT PRIMARY KEY, state TEXT NOT NULL)")
    plan.execute("INSERT INTO plan_row VALUES ('R1', 'ACTIVE')")
    plan.commit()
    plan.close()
    lineage = target / "lineage"
    lineage.mkdir(exist_ok=True)
    (lineage / "session_test.jsonl").write_text(
        '{"event":"visible"}\n', encoding="utf-8"
    )
    (lineage / "chat_lineage_head.json").write_text(
        '{"revision":1}\n', encoding="utf-8"
    )
    control = sqlite3.connect(lineage / "chat_lineage.sqlite")
    control.execute("CREATE TABLE event(sequence INTEGER PRIMARY KEY, value TEXT)")
    control.execute("INSERT INTO event(value) VALUES ('visible')")
    control.commit()
    control.close()
    replay = target / "sdk" / "replay"
    replay.mkdir(parents=True)
    replay_db = sqlite3.connect(replay / "sdk.agent-learning.v1.sqlite")
    replay_db.execute("CREATE TABLE replay(id TEXT PRIMARY KEY)")
    replay_db.commit()
    replay_db.close()
    rebinds = target / "active_contract_rebindings"
    rebinds.mkdir()
    (rebinds / "one.json").write_text('{"state":"DONE"}\n', encoding="utf-8")
    local_code = source_repository / "src" / "app.py"
    local_code.write_text(
        local_code.read_text(encoding="utf-8")
        + "\nTURN_ENTRY_WORKING_SECTOR_MARKER = True\n",
        encoding="utf-8",
    )
    universe_before = {
        path.relative_to(target / "universe").as_posix(): path.read_bytes()
        for path in (target / "universe").rglob("*")
        if path.is_file()
    }
    assert universe_before

    result = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )

    assert result["status"] == "PASS"
    assert result["state"] == "WORKING_SECTOR_AUTHORITY_COMMITTED"
    assert (target / "active_pointer.json").read_bytes() == pointer_before
    assert {
        path.name for path in (target / "sectors").iterdir() if path.is_dir()
    } >= set(CANONICAL_LANE_IDS)
    assert all(
        (target / "sectors" / lane_id / "study_brain.json").is_file()
        for lane_id in CANONICAL_LANE_IDS
    )
    assert (target / "sectors" / "plan" / "task_backlog.json").is_file()
    assert (
        target / "sectors" / "plan" / "plan_runtime_projection.sqlite"
    ).is_file()
    assert (
        target
        / "sectors"
        / "chat_lineage"
        / "lineage"
        / "chat_lineage.sqlite"
    ).is_file()
    assert (
        target
        / "sectors"
        / "chat_lineage"
        / "lineage"
        / "chat_lineage_head.json"
    ).is_file()
    assert (
        target
        / "receipts"
        / "chat-lineage-runtime"
        / "session_test.jsonl"
    ).is_file()
    assert not (target / "task_backlog.json").exists()
    assert not (target / "plan_runtime_projection.sqlite").exists()
    assert not (target / "lineage").exists()
    assert not (target / "snapshots").exists()
    assert {
        path.relative_to(target / "universe").as_posix(): path.read_bytes()
        for path in (target / "universe").rglob("*")
        if path.is_file()
    } == universe_before
    assert not (target / "sdk").exists()
    assert not (target / "active_contract_rebindings").exists()
    assert (
        target / "receipts" / "sdk-replay" / "sdk.agent-learning.v1.sqlite"
    ).is_file()
    assert (
        target / "receipts" / "active-contract-rebindings" / "one.json"
    ).is_file()
    assert result["pointer_moved"] is False
    assert result["candidate_created"] is False
    assert result["hil_inferred"] is False
    query = query_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
        query="TURN_ENTRY_WORKING_SECTOR_MARKER",
        lane_ids=["local_code"],
        limit=5,
    )
    assert query["status"] == "PASS"
    assert query["queried_lane_ids"] == ["local_code"]
    assert query["lane_study_brains"][0]["profile_id"] == (
        "study-brain-routing-profile-v1:local_code"
    )
    assert query["hits"]
    assert all(hit["lane_id"] == "local_code" for hit in query["hits"])
    assert query["raw_plan_loaded"] is False
    assert query["raw_pv_loaded"] is False
    assert query["raw_chat_lineage_loaded"] is False
    lineage_head = (
        target
        / "sectors"
        / "chat_lineage"
        / "lineage"
        / "chat_lineage_head.json"
    )
    changed_head = json.loads(lineage_head.read_text(encoding="utf-8"))
    changed_head["test_refresh_marker"] = "operational-authority-drift"
    lineage_head.write_text(json.dumps(changed_head), encoding="utf-8")
    refreshed = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    assert refreshed["status"] == "PASS"
    assert refreshed["state"] == "WORKING_SECTOR_AUTHORITY_REFRESHED"


def test_working_sector_migration_hash_accounts_excluded_dirty_content(
    service, source_repository: Path, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    (target / "task_backlog.json").write_text(
        json.dumps(
            {
                "schema": "evidence-lane.linear-task-backlog.v1",
                "project_id": "book-faires",
                "plans": [],
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    plan = sqlite3.connect(target / "plan_runtime_projection.sqlite")
    plan.execute("CREATE TABLE plan_row(task_id TEXT PRIMARY KEY, state TEXT NOT NULL)")
    plan.execute("INSERT INTO plan_row VALUES ('R1', 'ACTIVE')")
    plan.commit()
    plan.close()
    lineage = target / "lineage"
    lineage.mkdir(exist_ok=True)
    (lineage / "chat_lineage_head.json").write_text(
        '{"revision":1}\n', encoding="utf-8"
    )
    control = sqlite3.connect(lineage / "chat_lineage.sqlite")
    control.execute("CREATE TABLE event(sequence INTEGER PRIMARY KEY, value TEXT)")
    control.commit()
    control.close()
    excluded = source_repository / "tests" / "test_secret_fixture.py"
    excluded.parent.mkdir(exist_ok=True)
    excluded.write_text(
        'password = "ultramarine-giraffe-731"\n', encoding="utf-8"
    )

    result = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )

    assert result["status"] == "PASS"
    inventory = sqlite3.connect(
        target / "sectors" / "artifacts" / "working_delta_inventory.sqlite"
    )
    try:
        row = inventory.execute(
            """
            SELECT content_policy, content_policy_reason, sha256
            FROM working_path_inventory WHERE path=?
            """,
            ("tests/test_secret_fixture.py",),
        ).fetchone()
    finally:
        inventory.close()
    assert row is not None
    assert row[0] == "HASH_LOCATOR_ONLY"
    assert row[1] == "ASSIGNED_SECRET_MATERIAL_EXCLUDED"
    assert isinstance(row[2], str) and len(row[2]) == 64


def test_working_sector_migration_refreshes_when_dirty_identity_changes(
    service, source_repository: Path, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    (target / "task_backlog.json").write_text(
        json.dumps(
            {
                "schema": "evidence-lane.linear-task-backlog.v1",
                "project_id": "book-faires",
                "plans": [],
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    plan = sqlite3.connect(target / "plan_runtime_projection.sqlite")
    plan.execute("CREATE TABLE plan_row(task_id TEXT PRIMARY KEY, state TEXT NOT NULL)")
    plan.execute("INSERT INTO plan_row VALUES ('R1', 'ACTIVE')")
    plan.commit()
    plan.close()
    lineage = target / "lineage"
    lineage.mkdir(exist_ok=True)
    (lineage / "chat_lineage_head.json").write_text(
        '{"revision":1}\n', encoding="utf-8"
    )
    control = sqlite3.connect(lineage / "chat_lineage.sqlite")
    control.execute("CREATE TABLE event(sequence INTEGER PRIMARY KEY, value TEXT)")
    control.commit()
    control.close()
    first = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    pointer_before = (target / "active_pointer.json").read_bytes()
    changed = source_repository / "working-refresh.md"
    changed.write_text("new working Delta\n", encoding="utf-8")

    refreshed = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    repeated = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    interrupted_stage = target / ".sectors-working-INTERRUPTED.staging"
    (target / "sectors").replace(interrupted_stage)
    recovered = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )

    assert first["state"] == "WORKING_SECTOR_AUTHORITY_COMMITTED"
    assert refreshed["state"] == "WORKING_SECTOR_AUTHORITY_REFRESHED"
    assert repeated["state"] == "WORKING_SECTOR_AUTHORITY_IDEMPOTENT_REUSE"
    assert recovered["state"] == "WORKING_SECTOR_AUTHORITY_IDEMPOTENT_REUSE"
    assert recovered["interrupted_stage_recovery"]["status"] == "PASS"
    assert recovered["interrupted_stage_recovery"]["recovered_stage"] == (
        interrupted_stage.name
    )
    assert first["working_identity"]["working_identity_sha256"] != refreshed[
        "working_identity"
    ]["working_identity_sha256"]
    assert (target / "active_pointer.json").read_bytes() == pointer_before
    assert (target / "sectors" / "plan" / "task_backlog.json").is_file()
    assert (
        target
        / "sectors"
        / "chat_lineage"
        / "lineage"
        / "chat_lineage.sqlite"
    ).is_file()
    assert not (target / "task_backlog.json").exists()
    assert not (target / "lineage").exists()
    assert refreshed["pointer_moved"] is False
    assert refreshed["candidate_created"] is False
    assert refreshed["hil_inferred"] is False

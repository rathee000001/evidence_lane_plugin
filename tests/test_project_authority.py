from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin import project_authority
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS
from evidence_lane_plugin.plan_runtime import write_plan_runtime_projection
from evidence_lane_plugin.project_authority import (
    PROJECT_AUTHORITY_CONFIRMATION,
    migrate_working_project_sectors,
    query_working_project_sectors,
)
from evidence_lane_plugin.project_overlay import (
    build_project_overlay,
    validate_project_overlay,
)
from evidence_lane_plugin.reader import PVReader

from .conftest import build_and_approve_pv1, git


def test_windows_path_replace_retries_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    calls = 0
    real_replace = project_authority.os.replace

    def transient_replace(left: object, right: object) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(13, "sharing violation")
        real_replace(left, right)

    monkeypatch.setattr(project_authority.os, "name", "nt")
    monkeypatch.setattr(project_authority.os, "replace", transient_replace)
    monkeypatch.setattr(project_authority.time, "sleep", lambda _: None)

    report = project_authority._replace_path_with_retry(
        source,
        destination,
        operation="TEST_TRANSIENT_REPLACE",
    )

    assert report["attempt_count"] == 3
    assert report["transient_retry_count"] == 2
    assert destination.is_dir()
    assert not source.exists()


def test_windows_path_replace_exhaustion_is_structured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()

    def blocked_replace(left: object, right: object) -> None:
        raise PermissionError(13, "sharing violation")

    monkeypatch.setattr(project_authority.os, "name", "nt")
    monkeypatch.setattr(project_authority.os, "replace", blocked_replace)
    monkeypatch.setattr(project_authority.time, "sleep", lambda _: None)

    with pytest.raises(EvidenceLaneError) as raised:
        project_authority._replace_path_with_retry(
            source,
            destination,
            operation="TEST_BLOCKED_REPLACE",
        )

    assert raised.value.code == "PROJECT_WORKING_WINDOWS_PATH_REPLACE_BLOCKED"
    assert raised.value.details["attempt_count"] == 12
    assert raised.value.details["operation"] == "TEST_BLOCKED_REPLACE"


def test_accepted_lane_schema_binding_compatibility_is_narrow() -> None:
    base = {
        "valid": False,
        "checksum_set_match": True,
        "checksum_mismatches": {},
        "bundle_sha256": "A" * 64,
        "declared_bundle_sha256": "A" * 64,
        "lane_emission_contract_valid": True,
        "lane_directory_set_valid": True,
        "parallel_execution_valid": True,
        "topology_valid": True,
        "source_routes_valid": True,
        "lane_manifest_errors": {},
        "lane_disposition_contract": {"valid": True},
        "lanes": {
            "analysis": {
                "valid": False,
                "lane_schema_binding": {},
                "integrity": ["ok"],
                "foreign_key_errors": [],
                "schema_version": "evidence-lane.universal-lane.v2",
                "lane_schema_builder_projection": {"status": "PASS"},
                "lane_schema_evolution": {"valid": True},
                "counts": {"chunk_index": 7},
                "fts_rows": 7,
            }
        },
    }

    compatible = project_authority._accepted_lane_schema_binding_compatibility(base)
    assert compatible["status"] == "PASS"
    assert compatible["compatible"] is True
    assert compatible["compatible_lane_ids"] == ["analysis"]
    assert compatible["accepted_bytes_rewritten"] is False

    tampered = json.loads(json.dumps(base))
    tampered["lanes"]["analysis"]["integrity"] = ["database disk image is malformed"]
    rejected = project_authority._accepted_lane_schema_binding_compatibility(tampered)
    assert rejected["status"] == "FAIL"
    assert rejected["compatible"] is False
    assert rejected["invalid_lane_ids"] == ["analysis"]


def test_working_sector_source_rebuild_recognizes_only_lane_sqlite_seal_drift() -> None:
    lane_reports = {lane_id: {"valid": True} for lane_id in CANONICAL_LANE_IDS}
    lane_errors = {}
    for lane_id in CANONICAL_LANE_IDS:
        sqlite_name = project_authority.LANE_REGISTRY[lane_id].sqlite_filename
        lane_errors[lane_id] = {
            "schema": "evidence-lane.lane-manifest.v3",
            "lane_id": lane_id,
            "declared_stable_artifacts": {sqlite_name: "A" * 64},
            "actual_stable_artifacts": {sqlite_name: "B" * 64},
            "declared_evidence_artifacts": {sqlite_name: "A" * 64},
            "actual_evidence_artifacts": {sqlite_name: "B" * 64},
            "missing_required_artifacts": [],
            "mmd_valid": True,
            "dot_valid": True,
            "topology_reconciliation": {"status": "PASS"},
            "four_file_contract": {
                "missing": [],
                "tools_json_valid": True,
                "computed_tool_identity_sha256": "C" * 64,
                "declared_tool_identity_sha256": "C" * 64,
            },
            "artifact_role_contract": {
                "extensions": [],
                "undeclared_extension_files": [],
                "required_roles": [{"exists": True}],
            },
        }
    validation = {
        "lane_manifest_errors": lane_errors,
        "lanes": lane_reports,
        "checksum_mismatches": {
            "chat_lineage/.chat-lineage-writer.lock": {
                "declared": None,
                "actual": "D" * 64,
            }
        },
        "lane_emission_contract_valid": True,
        "lane_directory_set_valid": True,
        "parallel_execution_valid": True,
        "topology_valid": True,
        "source_routes_valid": True,
        "lane_disposition_contract": {"valid": True},
    }

    assert project_authority._working_sector_source_rebuild_required(validation)

    wrong_artifact = json.loads(json.dumps(validation))
    wrong_artifact["lane_manifest_errors"]["local_code"][
        "actual_stable_artifacts"
    ]["local_code.mmd"] = "E" * 64
    assert not project_authority._working_sector_source_rebuild_required(
        wrong_artifact
    )

    non_operational_wrapper = json.loads(json.dumps(validation))
    non_operational_wrapper["checksum_mismatches"] = {
        "local_code/local_code.mmd": {"declared": "A" * 64, "actual": "B" * 64}
    }
    assert not project_authority._working_sector_source_rebuild_required(
        non_operational_wrapper
    )


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


def _materialize_working_sectors_for_external_test(
    target: Path, source_repository: Path
) -> dict:
    backlog = {
        "schema": "evidence-lane.linear-task-backlog.v1",
        "project_id": "book-faires",
        "plans": [],
        "tasks": [],
    }
    write_plan_runtime_projection(
        target / "plan_runtime_projection.sqlite", backlog
    )
    (target / "task_backlog.json").write_text(
        json.dumps(backlog), encoding="utf-8"
    )
    lineage = target / "lineage"
    lineage.mkdir(exist_ok=True)
    head = lineage / "chat_lineage_head.json"
    if not head.is_file():
        head.write_text('{"revision":1}\n', encoding="utf-8")
    control_path = lineage / "chat_lineage.sqlite"
    if not control_path.is_file():
        control = sqlite3.connect(control_path)
        control.execute("CREATE TABLE event(sequence INTEGER PRIMARY KEY, value TEXT)")
        control.commit()
        control.close()
    return migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )


def test_root_nested_history_filter_runs_before_limit(tmp_path: Path) -> None:
    database = tmp_path / "root-nested-history-docs.sqlite"
    fts_table = project_authority.LANE_REGISTRY["docs"].fts_table
    connection = sqlite3.connect(database)
    connection.execute(
        f"""
        CREATE VIRTUAL TABLE {fts_table} USING fts5(
            path,
            locator,
            text_content,
            chunk_id UNINDEXED,
            tokenize='unicode61'
        )
        """
    )
    current_paths = {f"current-{index:03d}.md" for index in range(100)}
    connection.executemany(
        f"INSERT INTO {fts_table}(path, locator, text_content, chunk_id) "
        "VALUES (?, ?, ?, ?)",
        [
            (path, "line:1", "current route marker", index + 1)
            for index, path in enumerate(sorted(current_paths))
        ],
    )
    connection.execute(
        f"INSERT INTO {fts_table}(path, locator, text_content, chunk_id) "
        "VALUES (?, ?, ?, ?)",
        ("historical-only.md", "line:1", "current route marker", 1001),
    )
    connection.commit()
    connection.close()

    result = project_authority._query_root_nested_pv_history(
        database,
        lane_id="docs",
        fts_query='"current" OR "route" OR "marker"',
        current_paths=current_paths,
        limit=1,
    )

    assert result["excluded_current_path_count"] == 100
    assert len(result["rows"]) == 1
    assert result["rows"][0][1] == "historical-only.md"


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
    assert (legacy / "accepted" / "PV1").exists()
    assert (target / "accepted").is_dir()
    assert not any((target / "accepted").iterdir())
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


def test_external_empty_accepted_directory_uses_exact_continuity_reference(
    service, source_repository: Path, tmp_path: Path
) -> None:
    _session_id, _ = build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)

    # First seal a continuity receipt against the migrated external authority.
    # Relocation never copies or opens accepted storage; this empty directory is
    # only the explicit external storage shape under test.
    service.resume_session(
        project_id="book-faires",
        host="CODEX_CLI",
        host_session_id="codex-external-authority-before-storage-normalization",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    pointer_before = service.store.pointer("book-faires").as_dict()
    status = service.status_window("book-faires")
    current = status["accepted_summary"]["current"]
    assert status["accepted_summary"]["count"] == 1
    assert status["accepted_summary"]["highest_accepted_ordinal"] == 1
    assert status["candidate_summary"]["next_candidate_pv"] == "PV2"
    assert current["accepted_artifact_available"] is None
    assert current["accepted_artifact_integrity_validated"] is False
    assert current["accepted_archive_queried"] is False
    assert current["continuity_reference_integrity_validated"] is True
    assert (
        current["validation_scope"]
        == "LIVE_ROOT_RUNTIME_CONTINUITY_REFERENCE"
    )
    assert status["lane_projection"]["authority"] in {
        "WORKING_SECTORS_WITH_ACCEPTED_POINTER_REFERENCE",
        "ACCEPTED_POINTER_REFERENCE_ONLY",
    }

    resumed = service.resume_session(
        project_id="book-faires",
        host="CODEX_CLI",
        host_session_id="codex-external-authority-after-storage-normalization",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )

    assert resumed["status"] == "PASS"
    assert resumed["pointer"] == pointer_before
    entry_pointer = resumed["runtime_continuity"]["entry_pointer"]
    assert entry_pointer["accepted_artifact_available"] is None
    assert entry_pointer["accepted_artifact_integrity_validated"] is False
    assert entry_pointer["accepted_archive_queried"] is False
    assert entry_pointer["accepted_authority_integrity_validated"] is True
    assert entry_pointer["validation_scope"] == (
        "LIVE_ROOT_RUNTIME_CONTINUITY_REFERENCE"
    )


def _write_legacy_task_binding(
    target: Path,
    *,
    package_sha256: str,
    suffix: str,
) -> Path:
    pointer = json.loads((target / "active_pointer.json").read_text(encoding="utf-8"))
    payload = {
        "schema": "evidence-lane.codex-exact-task-project-session-binding.v1",
        "status": "PASS",
        "project_id": "book-faires",
        "accepted_pointer": {
            "accepted_pv": pointer["accepted_pv"],
            "generation": pointer["generation"],
            "manifest_sha256": pointer["accepted_manifest_sha256"],
            "package_sha256": package_sha256,
        },
    }
    path = target / "receipts" / "codex-task-bindings" / f"legacy-{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def test_external_legacy_v1_pointer_continuity_uses_root_receipts_only(
    service, source_repository: Path, tmp_path: Path, monkeypatch
) -> None:
    build_and_approve_pv1(service)
    package_sha256 = service.store.validate_accepted(
        "book-faires", "PV1", require_promotable=False
    )["package_sha256"]
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    _write_legacy_task_binding(
        target,
        package_sha256=package_sha256,
        suffix="one",
    )

    def forbidden_accepted_path(*_args, **_kwargs):
        raise AssertionError("legacy continuity opened accepted storage")

    monkeypatch.setattr(service.store, "accepted_path", forbidden_accepted_path)
    continuity = service.store.live_root_pointer_continuity("book-faires", "PV1")

    assert continuity["status"] == "PASS"
    assert continuity["continuity_mode"] == "LEGACY_V1_PROMOTION_POINTER_BOUND"
    assert continuity["validation_scope"] == (
        "LIVE_ROOT_LEGACY_PROMOTION_AND_TASK_BINDINGS"
    )
    assert continuity["package_sha256"] == package_sha256
    assert continuity["swap_journal_sha256"] is None
    assert continuity["archive_verification"] == "UNAVAILABLE_LEGACY_BASELINE"
    assert continuity["migration_required_at_next_promotion"] is True
    assert continuity["accepted_archive_opened"] is False
    assert continuity["accepted_archive_queried"] is False


def test_external_legacy_v1_pointer_continuity_rejects_package_hash_conflict(
    service, source_repository: Path, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    package_sha256 = service.store.validate_accepted(
        "book-faires", "PV1", require_promotable=False
    )["package_sha256"]
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    _write_legacy_task_binding(
        target,
        package_sha256=package_sha256,
        suffix="one",
    )
    _write_legacy_task_binding(
        target,
        package_sha256="F" * 64,
        suffix="conflict",
    )

    with pytest.raises(EvidenceLaneError) as raised:
        service.store.live_root_pointer_continuity("book-faires", "PV1")

    assert raised.value.code == "LIVE_ROOT_POINTER_CONTINUITY_RECEIPT_MISMATCH"
    assert raised.value.details["legacy_package_hash_count"] == 2


def test_external_status_never_opens_present_accepted_artifact(
    service, source_repository: Path, tmp_path: Path, monkeypatch
) -> None:
    _session_id, _ = build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    service.resume_session(
        project_id="book-faires",
        host="CODEX_CLI",
        host_session_id="codex-external-live-root-only",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )

    def forbidden_accepted_path(*_args, **_kwargs):
        raise AssertionError("ordinary external status opened accepted storage")

    monkeypatch.setattr(service.store, "accepted_path", forbidden_accepted_path)
    status = service.status_window("book-faires")

    assert status["accepted_summary"]["current"]["accepted_archive_queried"] is False
    assert status["lane_projection"]["accepted_archive_queried"] is False
    assert status["current_freshness"]["authority"] in {
        "WORKING_SECTORS",
        "ACCEPTED_POINTER_REFERENCE_ONLY",
    }


def test_project_overlay_appends_progressive_live_root_hil_history(
    service, source_repository: Path, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    target = tmp_path / "user-projects" / "book-faires"
    _relocate(service, source_repository, target)
    _materialize_working_sectors_for_external_test(target, source_repository)

    baseline_number = 12
    baseline_pv = f"PV{baseline_number}"
    parent_pv = f"PV{baseline_number - 1}"
    next_pv = f"PV{baseline_number + 1}"
    following_pv = f"PV{baseline_number + 2}"

    baseline = tmp_path / f"overlay-{baseline_pv.lower()}"
    baseline_result = build_project_overlay(
        baseline,
        lane_bundle_path=target / "sectors",
        lineage_source=None,
        candidate_id=f"{baseline_pv}_LEGACY_BASELINE__TEST",
        proposed_pv=baseline_pv,
        parent_accepted_pv=parent_pv,
        pointer_generation=baseline_number,
        code_mode="local_code",
        created_at="2026-08-23T23:00:00Z",
    )
    assert baseline_result["valid"] is True

    first = tmp_path / f"overlay-{next_pv.lower()}"
    first_result = build_project_overlay(
        first,
        lane_bundle_path=target / "sectors",
        lineage_source=None,
        candidate_id=f"{next_pv}_HIL_PROPOSAL__TEST",
        proposed_pv=next_pv,
        parent_accepted_pv=baseline_pv,
        pointer_generation=baseline_number,
        code_mode="local_code",
        created_at="2026-08-24T00:00:00Z",
        truth_state="HIL_PROPOSAL_ONLY",
        accepted_parent_access="LIVE_ROOT_BASELINE_ONLY_ACCEPTED_ARCHIVE_UNOPENED",
        prior_overlay_path=baseline,
    )
    assert first_result["valid"] is True
    assert first_result["transition_count"] == 1

    local_code = project_authority.LANE_REGISTRY["local_code"]
    local_code_database = (
        target / "sectors" / "local_code" / local_code.sqlite_filename
    )
    connection = sqlite3.connect(local_code_database)
    connection.execute("PRAGMA user_version=266")
    connection.commit()
    connection.close()

    first_connection = sqlite3.connect(first / "project_overlay.sqlite")
    first_local_code_row = first_connection.execute(
        """
        SELECT snapshot.lane_database_sha256,snapshot.source_count,
               snapshot.chunk_count,snapshot.fact_count
        FROM sector_hil_snapshot AS snapshot
        JOIN pv_hil_transition AS transition
          ON transition.transition_id=snapshot.transition_id
        WHERE transition.proposed_pv=? AND snapshot.sector_id='local_code'
        """,
        (next_pv,),
    ).fetchone()
    first_connection.close()
    assert first_local_code_row is not None
    first_local_code = {
        "lane_database_sha256": first_local_code_row[0],
        "source_count": first_local_code_row[1],
        "chunk_count": first_local_code_row[2],
        "fact_count": first_local_code_row[3],
    }

    second = tmp_path / f"overlay-{following_pv.lower()}"
    second_result = build_project_overlay(
        second,
        lane_bundle_path=target / "sectors",
        lineage_source=None,
        candidate_id=f"{following_pv}_HIL_PROPOSAL__TEST",
        proposed_pv=following_pv,
        parent_accepted_pv=next_pv,
        pointer_generation=baseline_number + 1,
        code_mode="local_code",
        created_at="2026-08-24T01:00:00Z",
        truth_state="HIL_PROPOSAL_ONLY",
        accepted_parent_access="LIVE_ROOT_BASELINE_ONLY_ACCEPTED_ARCHIVE_UNOPENED",
        prior_overlay_path=first,
    )
    validation = validate_project_overlay(second)
    assert second_result["valid"] is True
    assert validation["valid"] is True
    assert validation["transition_count"] == 2
    assert validation["transition_chain_errors"] == []
    modified = second_result["project_overlay_delta"]["modified"]
    assert len(modified) == 1
    assert modified[0]["sector_id"] == "local_code"
    assert modified[0]["before"] == first_local_code
    assert (
        modified[0]["after"]["lane_database_sha256"]
        != modified[0]["before"]["lane_database_sha256"]
    )
    assert second_result["accepted_archive_opened"] is False
    assert second_result["accepted_archive_queried"] is False

    shutil.copytree(second, target / "project_overlay", dirs_exist_ok=True)
    comparison = PVReader(service.store).diff(
        "book-faires", next_pv, following_pv
    )
    assert comparison["status"] == "PASS"
    assert comparison["progressive_history"] is True
    assert [
        row["sector_id"]
        for row in comparison["project_overlay_delta"]["modified"]
    ] == ["local_code"]
    assert comparison["accepted_archive_opened"] is False
    assert comparison["accepted_archive_queried"] is False


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
    result = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )

    assert result["status"] == "PASS"
    assert result["state"] == "WORKING_SECTOR_AUTHORITY_COMMITTED"
    universe_before = {
        path.relative_to(target / "universe").as_posix(): path.read_bytes()
        for path in (target / "universe").rglob("*")
        if path.is_file()
    }
    assert (target / "active_pointer.json").read_bytes() == pointer_before
    assert {
        path.name for path in (target / "sectors").iterdir() if path.is_dir()
    } >= set(CANONICAL_LANE_IDS)
    assert all(
        (target / "sectors" / lane_id / "study_brain.json").is_file()
        for lane_id in CANONICAL_LANE_IDS
    )
    local_study = json.loads(
        (target / "sectors" / "local_code" / "study_brain.json").read_text(
            encoding="utf-8"
        )
    )
    assert local_study["additional_sources_remain_lane_scoped"] is True
    assert local_study["source_registry_authority"].endswith(
        "/local_code_sector_v001.sqlite"
    )
    assert {
        "local_code_sector_v001.sqlite",
        "local_code.mmd",
        "local_code.dot",
        "tools.json",
        "lane_pointer.json",
        "lane_manifest.json",
        "study_brain.json",
    } == set(local_study["lane_owned_artifacts"])
    assert (target / "sectors" / "plan" / "task_backlog.json").is_file()
    assert (
        target / "sectors" / "plan" / "plan_runtime_projection.sqlite"
    ).is_file()
    assert (
        target
        / "sectors"
        / "chat_lineage"
        / "chat_lineage.sqlite"
    ).is_file()
    assert (
        target
        / "sectors"
        / "chat_lineage"
        / "chat_lineage_head.json"
    ).is_file()
    assert (
        target
        / "sectors"
        / "chat_lineage"
        / "session_test.jsonl"
    ).is_file()
    direct_lineage = target / "sectors" / "chat_lineage"
    nested_legacy = direct_lineage / "lineage"
    nested_legacy.mkdir()
    for name in (
        "chat_lineage.sqlite",
        "chat_lineage_head.json",
        "session_test.jsonl",
    ):
        (direct_lineage / name).replace(nested_legacy / name)
    project_authority.refresh_working_sector_operational_checksums(
        target,
        authority="CHAT_LINEAGE",
    )
    reconciled_lineage = (
        project_authority.reconcile_chat_lineage_operational_layout(target)
    )
    assert reconciled_lineage["state"] == "DIRECT_SECTOR_ROOT"
    assert reconciled_lineage["legacy_root_count"] == 1
    assert not nested_legacy.exists()
    assert (direct_lineage / "chat_lineage.sqlite").is_file()
    assert (direct_lineage / "chat_lineage_head.json").is_file()
    assert (direct_lineage / "session_test.jsonl").is_file()
    assert project_authority.validate_lane_bundle(target / "sectors")["valid"]

    # A Source Intake implementation can be interrupted after replacing one
    # derived lane database but before resealing that lane's manifest.  Keep
    # the database structurally valid, make only its declared database hashes
    # stale, and prove the explicit working-sector route rebuilds from the
    # governed repository source rather than accepting those derived bytes.
    local_code_manifest_path = (
        target / "sectors" / "local_code" / "lane_manifest.json"
    )
    local_code_manifest = json.loads(
        local_code_manifest_path.read_text(encoding="utf-8")
    )
    local_code_sqlite_name = project_authority.LANE_REGISTRY[
        "local_code"
    ].sqlite_filename
    local_code_manifest["stable_artifacts"][local_code_sqlite_name] = "A" * 64
    local_code_manifest["evidence_artifacts"][local_code_sqlite_name] = "A" * 64
    local_code_manifest_path.write_text(
        json.dumps(local_code_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    project_authority._refresh_lane_bundle_checksums(target / "sectors")
    stale_seal_validation = project_authority.validate_lane_bundle(
        target / "sectors"
    )
    assert stale_seal_validation["valid"] is False
    assert set(stale_seal_validation["lane_manifest_errors"]) == {"local_code"}
    recovered = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    assert recovered["status"] == "PASS"
    assert recovered["state"] == "WORKING_SECTOR_AUTHORITY_REFRESHED"
    assert recovered["build_parent_kind"] == (
        "CURRENT_WORKING_SECTORS_SOURCE_REBUILD_REQUIRED"
    )
    assert project_authority.validate_lane_bundle(target / "sectors")["valid"]
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
    writer_lock = (
        target / "sectors" / "chat_lineage" / ".chat-lineage-writer.lock"
    )
    writer_lock.write_text("runtime coordination lease\n", encoding="utf-8")
    lock_query = query_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
        query="TURN_ENTRY_WORKING_SECTOR_MARKER",
        lane_ids=["local_code"],
        limit=5,
    )
    assert lock_query["status"] == "PASS"
    assert lock_query["project_integrity_receipt"][
        "operational_checksum_drift_ignored"
    ] is True
    assert lock_query["project_integrity_receipt"][
        "operational_checksum_drift_paths"
    ] == ["chat_lineage/.chat-lineage-writer.lock"]
    lane_status = service.lane_status("book-faires", "local_code")
    assert lane_status["status"] in {"PASS", "STALE"}
    assert lane_status["bundle"]["operational_checksum_drift_ignored"] is True
    assert lane_status["bundle"]["operational_checksum_drift_paths"] == [
        "chat_lineage/.chat-lineage-writer.lock"
    ]
    lane_search = service.lane_search(
        "book-faires",
        "local_code",
        "TURN_ENTRY_WORKING_SECTOR_MARKER",
        limit=5,
        retrieval="fts5",
    )
    assert lane_search["status"] in {"PASS", "STALE"}
    assert lane_search["results"]
    selected_lineage_query = query_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
        query="TURN_ENTRY_WORKING_SECTOR_MARKER",
        lane_ids=["chat_lineage"],
        limit=5,
    )
    assert selected_lineage_query["status"] == "PASS"
    assert selected_lineage_query["queried_lane_ids"] == ["chat_lineage"]
    assert selected_lineage_query["working_lane_integrity_receipts"][0][
        "operational_checksum_drift_ignored"
    ] is True
    assert selected_lineage_query["working_lane_integrity_receipts"][0][
        "operational_checksum_drift_paths"
    ] == ["chat_lineage/.chat-lineage-writer.lock"]
    writer_lock.unlink()
    operational_head = (
        target / "sectors" / "chat_lineage" / "chat_lineage_head.json"
    )
    operational_head_payload = json.loads(
        operational_head.read_text(encoding="utf-8")
    )
    operational_head_payload["query_successor_marker"] = True
    operational_head.write_text(
        json.dumps(operational_head_payload), encoding="utf-8"
    )
    operational_refresh = (
        project_authority.refresh_working_sector_operational_checksums(
            target,
            authority="CHAT_LINEAGE",
        )
    )
    assert operational_refresh["status"] == "PASS"
    assert operational_refresh["before_bundle_sha256"] != (
        operational_refresh["after_bundle_sha256"]
    )
    successor_query = query_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
        query="TURN_ENTRY_WORKING_SECTOR_MARKER",
        lane_ids=["local_code"],
        limit=5,
    )
    assert successor_query["status"] == "PASS"
    assert successor_query["project_integrity_receipt"][
        "bundle_binding_state"
    ] == "VALIDATED_OPERATIONAL_SUCCESSOR_BUNDLE"
    assert successor_query["project_integrity_receipt"][
        "committed_baseline_bundle_sha256"
    ] != successor_query["project_integrity_receipt"]["bundle_sha256"]
    source_scaffold_receipt = (
        target
        / "receipts"
        / "project-authority"
        / "source-scaffold-retirement.json"
    )
    source_scaffold_receipt_before = source_scaffold_receipt.read_bytes()
    lineage_head = (
        target
        / "sectors"
        / "chat_lineage"
        / "chat_lineage_head.json"
    )
    changed_head = json.loads(lineage_head.read_text(encoding="utf-8"))
    changed_head["test_refresh_marker"] = "operational-authority-drift"
    lineage_head.write_text(json.dumps(changed_head), encoding="utf-8")
    atomic_insertions = (
        target / "sectors" / "plan" / "plan_atomic_insertions"
    )
    atomic_insertions.mkdir(parents=True, exist_ok=True)
    insertion_path = atomic_insertions / "live-plan-steer.json"
    insertion_bytes = json.dumps({"state": "ACCEPTED_PLAN_STEER"}).encode("utf-8")
    insertion_path.write_bytes(insertion_bytes)
    refreshed = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    assert refreshed["status"] == "PASS"
    assert refreshed["state"] == "WORKING_SECTOR_AUTHORITY_REFRESHED"
    assert (
        target
        / "sectors"
        / "plan"
        / "plan_atomic_insertions"
        / "live-plan-steer.json"
    ).read_bytes() == insertion_bytes
    assert not (target / "sources" / "objects").exists()
    assert not (target / "sources" / "source_manifests").exists()
    assert source_scaffold_receipt.read_bytes() == source_scaffold_receipt_before
    assert all(
        (
            target
            / "sectors"
            / lane_id
            / "historical_authority.ref.json"
        ).is_file()
        for lane_id in CANONICAL_LANE_IDS
    )
    sectors_before_idempotent = {
        path.relative_to(target / "sectors").as_posix(): path.read_bytes()
        for path in (target / "sectors").rglob("*")
        if path.is_file()
    }
    pointer_before_idempotent = (target / "active_pointer.json").read_bytes()
    (target / "sources" / "objects").mkdir(parents=True)
    (target / "sources" / "source_manifests").mkdir(parents=True)
    idempotent = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    assert idempotent["state"] == "WORKING_SECTOR_AUTHORITY_IDEMPOTENT_REUSE"
    assert not (target / "sources" / "objects").exists()
    assert not (target / "sources" / "source_manifests").exists()
    assert {
        row["path"]: row["state"]
        for row in idempotent["source_scaffold_retirement"]["rows"]
    } == {
        "sources/objects": "RETIRED_EMPTY",
        "sources/source_manifests": "RETIRED_EMPTY",
    }
    retired_receipt_bytes = source_scaffold_receipt.read_bytes()
    assert retired_receipt_bytes != source_scaffold_receipt_before
    assert {
        path.relative_to(target / "sectors").as_posix(): path.read_bytes()
        for path in (target / "sectors").rglob("*")
        if path.is_file()
    } == sectors_before_idempotent
    assert (target / "active_pointer.json").read_bytes() == pointer_before_idempotent

    absent_reuse = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    assert absent_reuse["state"] == "WORKING_SECTOR_AUTHORITY_IDEMPOTENT_REUSE"
    assert source_scaffold_receipt.read_bytes() == retired_receipt_bytes
    assert {
        path.relative_to(target / "sectors").as_posix(): path.read_bytes()
        for path in (target / "sectors").rglob("*")
        if path.is_file()
    } == sectors_before_idempotent
    assert (target / "active_pointer.json").read_bytes() == pointer_before_idempotent

    objects = target / "sources" / "objects"
    manifests = target / "sources" / "source_manifests"
    objects.mkdir(parents=True)
    manifests.mkdir(parents=True)
    (objects / "retained.bin").write_bytes(b"retained-source-object")
    (manifests / "retained.json").write_text(
        '{"state":"RETAINED"}\n', encoding="utf-8"
    )
    preserved = migrate_working_project_sectors(
        target,
        repository_root=source_repository,
        project_id="book-faires",
        accepted_pv="PV1",
        pointer_generation=1,
    )
    assert preserved["state"] == "WORKING_SECTOR_AUTHORITY_IDEMPOTENT_REUSE"
    assert (objects / "retained.bin").read_bytes() == b"retained-source-object"
    assert (manifests / "retained.json").read_text(encoding="utf-8") == (
        '{"state":"RETAINED"}\n'
    )
    assert {
        row["path"]: row["state"]
        for row in preserved["source_scaffold_retirement"]["rows"]
    } == {
        "sources/objects": "PRESERVED_NONEMPTY",
        "sources/source_manifests": "PRESERVED_NONEMPTY",
    }
    assert {
        path.relative_to(target / "sectors").as_posix(): path.read_bytes()
        for path in (target / "sectors").rglob("*")
        if path.is_file()
    } == sectors_before_idempotent
    assert (target / "active_pointer.json").read_bytes() == pointer_before_idempotent

    layout_path = target / "project_authority.json"
    layout_bytes = layout_path.read_bytes()
    layout = json.loads(layout_bytes.decode("utf-8"))
    layout["repository_path"] = str(tmp_path / "wrong-repository")
    layout_body = {
        key: value for key, value in layout.items() if key != "layout_sha256"
    }
    layout["layout_sha256"] = sha256_bytes(canonical_json_bytes(layout_body))
    layout_path.write_text(
        json.dumps(layout, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    try:
        with pytest.raises(EvidenceLaneError) as wrong_repository:
            query_working_project_sectors(
                target,
                repository_root=source_repository,
                project_id="book-faires",
                accepted_pv="PV1",
                pointer_generation=1,
                query="TURN_ENTRY_WORKING_SECTOR_MARKER",
                lane_ids=["local_code"],
                limit=5,
            )
        assert wrong_repository.value.code == (
            "PROJECT_WORKING_QUERY_PROJECT_BINDING_MISMATCH"
        )
    finally:
        layout_path.write_bytes(layout_bytes)

    layout = json.loads(layout_bytes.decode("utf-8"))
    layout["project_id"] = "wrong-project"
    layout_body = {
        key: value for key, value in layout.items() if key != "layout_sha256"
    }
    layout["layout_sha256"] = sha256_bytes(canonical_json_bytes(layout_body))
    layout_path.write_text(
        json.dumps(layout, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    try:
        with pytest.raises(EvidenceLaneError) as wrong_project:
            query_working_project_sectors(
                target,
                repository_root=source_repository,
                project_id="book-faires",
                accepted_pv="PV1",
                pointer_generation=1,
                query="TURN_ENTRY_WORKING_SECTOR_MARKER",
                lane_ids=["local_code"],
                limit=5,
            )
        assert wrong_project.value.code == (
            "PROJECT_WORKING_QUERY_PROJECT_BINDING_MISMATCH"
        )
    finally:
        layout_path.write_bytes(layout_bytes)

    unselected_profile = target / "sectors" / "docs" / "study_brain.json"
    unselected = json.loads(unselected_profile.read_text(encoding="utf-8"))
    unselected["state"] = "TAMPERED"
    unselected_profile.write_text(
        json.dumps(unselected, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(EvidenceLaneError) as unselected_tamper:
        query_working_project_sectors(
            target,
            repository_root=source_repository,
            project_id="book-faires",
            accepted_pv="PV1",
            pointer_generation=1,
            query="TURN_ENTRY_WORKING_SECTOR_MARKER",
            lane_ids=["local_code"],
            limit=5,
        )
    assert unselected_tamper.value.code == (
        "PROJECT_WORKING_QUERY_BUNDLE_INTEGRITY_MISMATCH"
    )


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
    git(source_repository, "add", "--", "tests/test_secret_fixture.py")

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


def test_working_sector_migration_excludes_local_evidence_paths(
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
    local_evidence = source_repository / "evidence" / "local-history" / "receipt.json"
    local_evidence.parent.mkdir(parents=True)
    local_evidence.write_text('{"local_only":true}\n', encoding="utf-8")

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
            "SELECT path FROM working_path_inventory WHERE path LIKE 'evidence/%'"
        ).fetchone()
    finally:
        inventory.close()
    assert row is None
    receipt = json.loads(
        (
            target
            / "receipts"
            / "project-authority"
            / "working-sector-migration.json"
        ).read_text(encoding="utf-8")
    )
    exclusion = receipt["local_only_exclusion"]
    assert exclusion["excluded_path_count"] == 1
    assert exclusion["excluded_prefix_counts"]["evidence/"] == 1
    assert exclusion["raw_excluded_paths_returned"] is False
    assert exclusion["excluded_content_read"] is False


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
    local_test = source_repository / "local-test-only.txt"
    local_test.write_text("must remain outside Git-index authority\n", encoding="utf-8")
    git(source_repository, "add", "working-refresh.md")

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
        / "chat_lineage.sqlite"
    ).is_file()
    assert not (target / "task_backlog.json").exists()
    assert not (target / "lineage").exists()
    assert refreshed["pointer_moved"] is False
    assert refreshed["candidate_created"] is False
    assert refreshed["hil_inferred"] is False
    refreshed_manifest = json.loads(
        (target / "sectors" / "manifest.json").read_text(encoding="utf-8")
    )
    refreshed_routes = json.loads(
        (target / "sectors" / "routes.json").read_text(encoding="utf-8")
    )["routes"]
    observed_sources: dict[str, str] = {}
    for lane_id in CANONICAL_LANE_IDS:
        lane = project_authority.LANE_REGISTRY[lane_id]
        connection = sqlite3.connect(
            target / "sectors" / lane_id / lane.sqlite_filename
        )
        try:
            observed_sources.update(
                {
                    str(path): str(source_sha256)
                    for path, source_sha256 in connection.execute(
                        "SELECT path, sha256 FROM source_registry"
                    ).fetchall()
                }
            )
        finally:
            connection.close()
    assert refreshed_manifest["source_policy"]["source_paths_overridden"] is False
    assert refreshed_manifest["source_policy"]["selection_mode"] == "GIT_TRACKED_ONLY"
    assert refreshed_manifest["source_count"] == len(refreshed_routes)
    assert set(observed_sources) == set(refreshed_routes)
    assert observed_sources == {
        path: sha256_bytes((source_repository / Path(path)).read_bytes())
        for path in refreshed_routes
    }
    assert {"src/app.py", "working-refresh.md"} <= set(refreshed_routes)
    assert "local-test-only.txt" not in refreshed_routes
    inventory = sqlite3.connect(
        target / "sectors" / "artifacts" / "working_delta_inventory.sqlite"
    )
    try:
        local_test_inventory = inventory.execute(
            """
            SELECT content_policy,content_policy_reason
            FROM working_path_inventory WHERE path=?
            """,
            ("local-test-only.txt",),
        ).fetchone()
    finally:
        inventory.close()
    assert local_test_inventory == (
        "HASH_LOCATOR_ONLY",
        "UNTRACKED_NOT_GIT_INDEX_AUTHORITY",
    )
    assert refreshed_manifest["summary"]["full_build_lanes"] == []
    assert refreshed_manifest["summary"]["full_validation_fallbacks"] == []
    assert len(refreshed_manifest["reports"]) == len(CANONICAL_LANE_IDS)
    assert all(
        row["build_mode"] in {"UNCHANGED_REUSE", "INCREMENTAL_REFRESH"}
        for row in refreshed_manifest["reports"]
    )
    assert any(
        row["build_mode"] == "UNCHANGED_REUSE"
        for row in refreshed_manifest["reports"]
    )
    migration_receipt = json.loads(
        (
            target
            / "receipts"
            / "project-authority"
            / "working-sector-migration.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        migration_receipt["build_parent_kind"]
        == "CURRENT_VALIDATED_WORKING_SECTORS"
    )
    assert migration_receipt["canonical_lane_refresh"]["authority_scope"] == (
        "ALL_18_CANONICAL_LANES"
    )
    assert migration_receipt["canonical_lane_refresh"]["lane_report_count"] == 18
    assert migration_receipt["complete_governed_source_replay"] is True
    assert migration_receipt["current_source_authority_scope"] == (
        "GIT_INDEX_CURRENT_WORKTREE_BYTES"
    )
    assert migration_receipt["complete_governed_source_path_count"] == len(
        refreshed_routes
    )
    assert migration_receipt["canonical_lane_refresh"][
        "full_validation_fallbacks"
    ] == []

"""Deterministic Git/code PV builder retained from the historical method laws."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, cast

from . import database
from .acceptance import declarations_for_phase, run_acceptance_checks
from .engine_identity import build_engine_identity
from .errors import EvidenceLaneError, require
from .git_adapter import (
    diff_patch,
    identity_json,
    inspect_repository,
)
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .ids import new_ulid, prefixed_id
from .ingest import ingest_repository, refresh_repository
from .lane_engine import build_lane_bundle
from .mode_governance import validate_mode_binding
from .models import SessionRecord, TaskContract
from .next_actions import hil_next_action, refresh_output_handoff
from .pv_package import build_pv_package
from .runtime_continuity import validate_runtime_continuity
from .store import ProjectStore
from .timeutil import utc_now


def _db_file_index(path: Path) -> dict[str, dict[str, Any]]:
    with database.connect(path, readonly=True) as connection:
        rows = connection.execute(
            "SELECT path, sha256, size_bytes, code_family FROM files ORDER BY path"
        ).fetchall()
    return {
        row["path"]: {
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "code_family": row["code_family"],
        }
        for row in rows
    }


def compare_source_indexes(
    prior_database: Path | None,
    current_database: Path,
) -> dict[str, Any]:
    prior = _db_file_index(prior_database) if prior_database else {}
    current = _db_file_index(current_database)
    added = [
        {"path": path, **current[path]}
        for path in sorted(current.keys() - prior.keys())
    ]
    deleted = [
        {"path": path, **prior[path]} for path in sorted(prior.keys() - current.keys())
    ]
    modified = [
        {
            "path": path,
            "before_sha256": prior[path]["sha256"],
            "after_sha256": current[path]["sha256"],
            "before_bytes": prior[path]["size_bytes"],
            "after_bytes": current[path]["size_bytes"],
            "code_family": current[path]["code_family"],
        }
        for path in sorted(prior.keys() & current.keys())
        if prior[path]["sha256"] != current[path]["sha256"]
    ]
    unchanged = sum(
        1
        for path in prior.keys() & current.keys()
        if prior[path]["sha256"] == current[path]["sha256"]
    )
    payload = {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
    }
    payload["delta_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    return payload


class CodePVEngine:
    def __init__(
        self,
        *,
        store: ProjectStore,
        source_repository_root: str | Path,
        package_source_root: str | Path,
    ) -> None:
        self.store = store
        self.source_repository_root = Path(source_repository_root).resolve()
        self.package_source_root = Path(package_source_root).resolve()

    def doctor(self) -> dict[str, Any]:
        schema_path = self.package_source_root / "schema.sql"
        checks = {
            "git": shutil.which("git") is not None,
            "python": True,
            "sqlite_fts5": False,
            "schema_file": schema_path.is_file(),
            "store_writable": False,
        }
        with sqlite3.connect(":memory:") as connection:
            try:
                connection.execute("CREATE VIRTUAL TABLE test_fts USING fts5(content)")
                checks["sqlite_fts5"] = True
            except sqlite3.OperationalError:
                checks["sqlite_fts5"] = False
        probe = self.store.root / ".doctor-probe"
        try:
            probe.write_bytes(b"evidence-lane-doctor")
            checks["store_writable"] = probe.read_bytes() == b"evidence-lane-doctor"
        finally:
            probe.unlink(missing_ok=True)
        identity = build_engine_identity(
            package_root=self.package_source_root,
            repository_root=self.source_repository_root,
        )[0].as_dict()
        return {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "engine": identity,
        }

    def build_candidate(
        self,
        *,
        project_id: str,
        session: SessionRecord,
        run_id: str,
        lineage_path: str | Path,
        task: TaskContract | None,
        initial_entry: bool,
    ) -> dict[str, Any]:
        config = self.store.config(project_id)
        pointer = self.store.pointer(project_id)
        require(
            session.project_id == project_id,
            "SESSION_PROJECT_MISMATCH",
            "The session is not authorized for this project.",
            status="MISMATCH",
        )
        require(
            pointer.generation == session.accepted_pointer_generation,
            "SESSION_POINTER_STALE",
            "The accepted pointer changed after session boot.",
            status="STALE",
            session_generation=session.accepted_pointer_generation,
            current_generation=pointer.generation,
        )
        runtime_continuity_value = session.metadata.get("runtime_continuity")
        require(
            isinstance(runtime_continuity_value, dict),
            "RUNTIME_CONTINUITY_REQUIRED",
            "A candidate build requires the boot/resume ENV and storage continuity receipt.",
            status="BLOCKED",
        )
        runtime_continuity = validate_runtime_continuity(
            cast(dict[str, Any], runtime_continuity_value)
        )
        mode_binding_value = session.metadata.get(
            "task_mode_binding" if task is not None else "active_mode_binding"
        )
        mode_binding: dict[str, Any] | None = None
        if isinstance(mode_binding_value, dict):
            mode_binding = validate_mode_binding(
                mode_binding_value,
                expected_task_id=(task.task_id if task is not None else None),
            )
        if initial_entry:
            require(
                pointer.accepted_pv is None,
                "INITIAL_ENTRY_ALREADY_EXISTS",
                "PV1 can only be built when no accepted PV exists.",
                status="BLOCKED",
            )
        acceptance_health = run_acceptance_checks(
            config.repository_path,
            task.acceptance_checks if task else [],
        )
        require(
            acceptance_health["source_unchanged"],
            "ACCEPTANCE_CHECK_MUTATED_SOURCE",
            "An acceptance command changed the governed source. Restore or explicitly "
            "include that change before building a candidate.",
            status="BLOCKED",
            acceptance_health=acceptance_health,
        )
        mode_execution: dict[str, Any] | None = None
        if mode_binding is not None:
            governance = cast(dict[str, Any], mode_binding["mode_governance"])
            contracts = cast(list[dict[str, Any]], governance["contracts"])
            ci_cd_required = any(
                bool(contract.get("ci_cd", {}).get("required"))
                for contract in contracts
            )
            mode_execution = {
                "schema": "evidence-lane.mode-execution.v1",
                "selection_source": mode_binding["selection_source"],
                "selected_mode_ids": list(mode_binding["selected_mode_ids"]),
                "mode_intersection": mode_binding["mode_intersection"],
                "canonical_lanes": list(mode_binding["canonical_lanes"]),
                "mode_binding_receipt_sha256": mode_binding[
                    "binding_receipt_sha256"
                ],
                "combined_operator_receipt_sha256": governance[
                    "combined_operator_receipt_sha256"
                ],
                "visible_formula_response": list(
                    governance["visible_formula_response"]
                ),
                "operator_contracts": contracts,
                "lane_hil_contracts": [contract["hil"] for contract in contracts],
                "ci_cd": {
                    "required_by_selected_mode": ci_cd_required,
                    "prebuild_receipt_status": acceptance_health.get(
                        "prebuild_status", acceptance_health["status"]
                    ),
                    "prebuild_receipt_verdict": acceptance_health.get(
                        "prebuild_verdict", acceptance_health["verdict"]
                    ),
                    "declared": acceptance_health["declared"],
                    "executed": acceptance_health.get(
                        "prebuild_executed", acceptance_health["executed"]
                    ),
                    "postseal_pending": acceptance_health.get(
                        "postseal_pending", 0
                    ),
                    "commands_inferred": acceptance_health["commands_inferred"],
                    "approve_gate": (
                        "PASS"
                        if not ci_cd_required
                        or acceptance_health.get(
                            "prebuild_status", acceptance_health["status"]
                        )
                        == "PASS"
                        else "OPEN_OR_FAILED"
                    ),
                },
                "six_way_token_vocabulary_preserved": governance[
                    "six_way_token_vocabulary_preserved"
                ],
                "six_way_hil_is_lane_specific": True,
                "mode_selection_is_not_hil_approval": True,
                "candidate_created_by_selection": False,
                "pointer_moved_by_selection": False,
            }
            mode_execution["execution_receipt_sha256"] = sha256_bytes(
                canonical_json_bytes(mode_execution)
            )
        identity = inspect_repository(
            config.repository_path,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
            expected_branch=(
                config.allowed_branches[0]
                if len(config.allowed_branches) == 1
                else None
            ),
            require_clean=initial_entry,
        )
        require(
            identity.branch in config.allowed_branches,
            "BRANCH_NOT_AUTHORIZED",
            "The current Git branch is not authorized for this project.",
            status="BLOCKED",
            branch=identity.branch,
            allowed_branches=config.allowed_branches,
        )
        repository_payload = identity_json(identity, config.repository_path)
        proposed_pv = self.store.next_pv_id(project_id)
        candidate_id = f"{proposed_pv}_CANDIDATE__RUN_{new_ulid()}"
        created_at = utc_now()
        project_root = self.store.project_root(project_id)
        build_parent = project_root / ".build"
        build_parent.mkdir(parents=True, exist_ok=True)
        build_root = Path(tempfile.mkdtemp(prefix=f"{candidate_id}.", dir=build_parent))
        try:
            db_path = build_root / "code.sqlite"
            prior_db = None
            if pointer.accepted_pv:
                prior_db = (
                    self.store.accepted_path(project_id, pointer.accepted_pv)
                    / "code.sqlite"
                )
                require(
                    prior_db.is_file(),
                    "ACCEPTED_CODE_DATABASE_MISSING",
                    "The accepted entry PV has no code compatibility database.",
                    status="MISMATCH",
                    accepted_pv=pointer.accepted_pv,
                )
                shutil.copyfile(prior_db, db_path)
            database.initialize(
                db_path,
                extra_metadata={
                    "project_id": project_id,
                    "candidate_id": candidate_id,
                    "proposed_pv": proposed_pv,
                    "created_at": created_at,
                },
            )
            started_at = utc_now()
            with database.connect(db_path) as connection:
                with database.transaction(connection):
                    repository_values = (
                        identity.provider,
                        identity.repository_url,
                        identity.owner,
                        identity.name,
                        identity.branch,
                        identity.commit_sha,
                        identity.tree_sha,
                        repository_payload["worktree_sha256"],
                        int(identity.is_clean),
                        json.dumps(
                            repository_payload["submodules"],
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        identity.lfs_state,
                    )
                    if prior_db:
                        repository_row = connection.execute(
                            """
                            SELECT repository_id FROM repositories
                            ORDER BY repository_id DESC LIMIT 1
                            """
                        ).fetchone()
                        require(
                            repository_row is not None,
                            "ACCEPTED_REPOSITORY_ROW_MISSING",
                            "The accepted code database has no repository identity.",
                            status="MISMATCH",
                        )
                        repository_id = int(repository_row["repository_id"])
                        connection.execute(
                            """
                            UPDATE repositories SET
                                provider=?, repository_url=?, owner=?, name=?,
                                branch=?, commit_sha=?, tree_sha=?, worktree_sha256=?,
                                is_clean=?, submodules_json=?, lfs_state=?
                            WHERE repository_id=?
                            """,
                            (*repository_values, repository_id),
                        )
                        ingestion = refresh_repository(
                            connection,
                            repository_id=repository_id,
                            repository_root=config.repository_path,
                            parent_pv=pointer.accepted_pv or "NONE",
                        )
                    else:
                        repo_cursor = connection.execute(
                            """
                            INSERT INTO repositories(
                                provider, repository_url, owner, name, branch, commit_sha,
                                tree_sha, worktree_sha256, is_clean, submodules_json, lfs_state
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            repository_values,
                        )
                        repository_id = database.required_lastrowid(repo_cursor)
                        ingestion = ingest_repository(
                            connection,
                            repository_id=repository_id,
                            repository_root=config.repository_path,
                        )
                    connection.execute(
                        """
                        INSERT INTO pointers(pointer_kind, pointer_value, pointer_sha256, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            "accepted_entry",
                            pointer.accepted_pv or "NONE",
                            pointer.accepted_manifest_sha256 or "NONE",
                            created_at,
                        ),
                    )
                    if task:
                        connection.execute(
                            """
                            INSERT INTO tasks(
                                task_id, task_class, requested_outcome,
                                permitted_paths_json, permitted_tools_json,
                                acceptance_checks_json, write_boundary, stop_condition,
                                hil_required, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                task.task_id,
                                task.task_class.value,
                                task.requested_outcome,
                                json.dumps(task.permitted_paths, sort_keys=True),
                                json.dumps(task.permitted_tools, sort_keys=True),
                                json.dumps(task.acceptance_checks, sort_keys=True),
                                task.write_boundary,
                                task.stop_condition,
                                int(task.hil_required),
                                task.status,
                            ),
                        )
                    for check in acceptance_health["checks"]:
                        connection.execute(
                            """
                            INSERT INTO acceptance_results(
                                run_id, declaration, command_text, status, returncode,
                                duration_seconds, output_tail, output_truncated, reason
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id,
                                check["declaration"],
                                check["command"],
                                check["status"],
                                check["returncode"],
                                check["duration_seconds"],
                                check["output_tail"],
                                int(check["output_truncated"]),
                                check["reason"],
                            ),
                        )
                    connection.execute(
                        """
                        INSERT INTO runs(
                            run_id, task_id, session_id, agent_id, host_kind,
                            source_commit_sha, source_worktree_sha256,
                            started_at, completed_at, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            task.task_id if task else None,
                            session.session_id,
                            str(session.metadata.get("agent_id", "single-agent")),
                            session.host.value,
                            identity.commit_sha,
                            repository_payload["worktree_sha256"],
                            started_at,
                            utc_now(),
                            "CAPTURED",
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO pv_ancestry(
                            child_candidate_id, parent_accepted_pv, proposed_pv,
                            run_id, parent_manifest_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            candidate_id,
                            pointer.accepted_pv,
                            proposed_pv,
                            run_id,
                            pointer.accepted_manifest_sha256,
                            created_at,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO provenance(
                            source_kind, source_identity, output_kind, output_identity,
                            authority, sha256, details_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "git_worktree",
                            f"{identity.repository_url}@{identity.commit_sha}",
                            "code_sqlite",
                            candidate_id,
                            "exact repository bytes in files.exact_bytes",
                            repository_payload["worktree_sha256"],
                            json.dumps(
                                repository_payload,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    completed_at = utc_now()
                    connection.execute(
                        """
                        INSERT INTO builder_receipts(
                            receipt_id, phase, status, started_at, completed_at, details_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            prefixed_id("build"),
                            "whole_source_ingestion",
                            "PASS",
                            started_at,
                            completed_at,
                            json.dumps(
                                ingestion.as_dict(),
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.commit()
            database.validate(db_path)
            delta = compare_source_indexes(prior_db, db_path)
            parent_lane_bundle = None
            if pointer.accepted_pv:
                possible_parent = (
                    self.store.accepted_path(project_id, pointer.accepted_pv) / "lanes"
                )
                if possible_parent.is_dir():
                    parent_lane_bundle = possible_parent
            lane_report = build_lane_bundle(
                repository_root=config.repository_path,
                output_directory=build_root / "lane_bundle",
                code_mode=(
                    "github_code" if identity.provider == "github" else "local_code"
                ),
                parent_lane_bundle=parent_lane_bundle,
                parent_pv=pointer.accepted_pv,
                proposed_pv=proposed_pv,
                pointer_generation=pointer.generation,
                source_overrides=session.metadata.get("source_lane_overrides"),
                git_mode=str(session.metadata.get("git_arm_mode") or "AUTO"),
            )
            patch = diff_patch(config.repository_path)
            patch_sha256 = sha256_bytes(patch.encode("utf-8"))
            engine_identity, toolchain = build_engine_identity(
                package_root=self.package_source_root,
                repository_root=self.source_repository_root,
            )
            project_identity = {
                "schema": "evidence-lane.project-identity.v1",
                "project_id": project_id,
                "display_name": config.display_name,
                "repository": repository_payload,
                "sensitivity": config.sensitivity,
                "source_authority": (
                    "exact governed source bytes captured in code.sqlite and the "
                    "eighteen-lane SQLite bundle"
                ),
                "universal_lanes": {
                    "lane_count": lane_report["lane_count"],
                    "code_mode": lane_report["code_mode"],
                    "bundle_sha256": lane_report["bundle_sha256"],
                },
            }
            entry_slip = {
                "schema": "evidence-lane.entry-slip.v1",
                "session_id": session.session_id,
                "run_id": run_id,
                "project_id": project_id,
                "accepted_entry_pv": pointer.accepted_pv,
                "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
                "pointer_generation": pointer.generation,
                "repository_entry": session.repository,
                "task": task.as_dict() if task else None,
                "runtime_continuity": runtime_continuity,
                "mode_execution": mode_execution,
                "entered_at": session.created_at,
            }
            next_action_contract = hil_next_action(
                project_id=project_id,
                session_id=session.session_id,
                candidate_id=candidate_id,
                proposed_pv=proposed_pv,
                mode_execution=mode_execution,
            )
            output_handoff = refresh_output_handoff(
                host_kind=session.host.value,
                client_can_edit_source=(
                    session.metadata.get("client_source_edit_authority") == "DIRECT"
                ),
                candidate_id=candidate_id,
                proposed_pv=proposed_pv,
            )
            invocation = runtime_continuity.get("invocation")
            exit_prompt_label = (
                str(
                    invocation.get("exit_slip_next_prompt_label")
                    or "PV_EXIT_SUGGESTED_NEXT_PROMPT"
                )
                if isinstance(invocation, dict)
                else "PV_EXIT_SUGGESTED_NEXT_PROMPT"
            )
            exit_slip = {
                "schema": "evidence-lane.exit-slip.v1",
                "session_id": session.session_id,
                "run_id": run_id,
                "project_id": project_id,
                "proposed_pv": proposed_pv,
                "candidate_id": candidate_id,
                "repository_exit": repository_payload,
                "source_delta": delta,
                "git_patch_sha256": patch_sha256,
                "git_patch_bytes": len(patch.encode("utf-8")),
                "task": task.as_dict() if task else None,
                "runtime_continuity": runtime_continuity,
                "pv_exit_prompt": {
                    "label": exit_prompt_label,
                    "suggested_next_prompt": next_action_contract[
                        "suggested_next_prompt"
                    ],
                    "choices": next_action_contract.get("choices", []),
                    "copyable": True,
                    "host_owned_composer": True,
                    "auto_submit": False,
                },
                "mode_execution": mode_execution,
                "acceptance_checks": (
                    acceptance_health
                    if task
                    else {
                        "status": "PASS",
                        "verdict": "INITIAL_ENTRY_BUILD",
                        "declared": 0,
                        "executed": 0,
                        "checks": [],
                        "commands_inferred": False,
                    }
                ),
                "lane_refresh": lane_report["summary"],
                "next_action": next_action_contract,
                "host_output_handoff": output_handoff,
                "exited_at": created_at,
            }
            package_warnings = list(ingestion.warnings)
            if lane_report["summary"]["blocked_sources"]:
                package_warnings.append(
                    {
                        "status": "PARTIAL",
                        "code": "LANE_SOURCES_BLOCKED_OR_UNSUPPORTED",
                        "count": lane_report["summary"]["blocked_sources"],
                        "exact_source_bytes_preserved": True,
                    }
                )
            package_result = build_pv_package(
                build_root / "package",
                database_path=db_path,
                lineage_source=lineage_path,
                project_identity=project_identity,
                active_pointer={
                    "project_id": project_id,
                    "accepted_pv": pointer.accepted_pv,
                    "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
                    "generation": pointer.generation,
                    "updated_at": pointer.updated_at,
                    "prior_generation": pointer.prior_generation,
                },
                entry_slip=entry_slip,
                exit_slip=exit_slip,
                engine_identity=engine_identity.as_dict(),
                candidate_id=candidate_id,
                proposed_pv=proposed_pv,
                parent_accepted_pv=pointer.accepted_pv,
                parent_manifest_sha256=pointer.accepted_manifest_sha256,
                run_id=run_id,
                created_at=created_at,
                warnings=package_warnings,
                lane_bundle_path=build_root / "lane_bundle",
                code_mode=lane_report["code_mode"],
                connector_brain_path=(
                    self.store.project_root(project_id) / "connector_brain.sqlite"
                ),
            )
            stored = self.store.place_candidate(
                project_id, candidate_id, build_root / "package"
            )
            postseal_declarations = declarations_for_phase(
                config.repository_path,
                task.acceptance_checks if task else [],
                phase="POSTSEAL",
            )
            postseal_acceptance = None
            postseal_receipt_path = None
            postseal_receipt_sha256 = None
            if postseal_declarations:
                postseal_acceptance = run_acceptance_checks(
                    config.repository_path,
                    postseal_declarations,
                    phase="POSTSEAL",
                    environment={
                        "EVIDENCE_LANE_CANDIDATE_PATH": str(
                            self.store.candidate_path(project_id, candidate_id)
                        ),
                        "EVIDENCE_LANE_PROJECT_ROOT": str(project_root),
                        "EVIDENCE_LANE_PROJECT_ID": project_id,
                        "EVIDENCE_LANE_EXPECTED_CANDIDATE_ID": candidate_id,
                        "EVIDENCE_LANE_EXPECTED_ACCEPTED_PV": (
                            pointer.accepted_pv or "NONE"
                        ),
                        "EVIDENCE_LANE_EXPECTED_POINTER_GENERATION": str(
                            pointer.generation
                        ),
                        "EVIDENCE_LANE_EXPECTED_COMMIT": identity.commit_sha,
                    },
                )
                postseal_receipt = {
                    "schema": "evidence-lane.postseal-acceptance.receipt.v1",
                    "project_id": project_id,
                    "candidate_id": candidate_id,
                    "accepted_pv_retained": pointer.accepted_pv,
                    "pointer_generation_retained": pointer.generation,
                    "source_commit_sha": identity.commit_sha,
                    "acceptance": postseal_acceptance,
                    "recorded_at": utc_now(),
                }
                postseal_receipt["receipt_sha256"] = sha256_bytes(
                    canonical_json_bytes(postseal_receipt)
                )
                postseal_receipt_path = (
                    project_root
                    / "receipts"
                    / f"postseal_{candidate_id.lower()}.json"
                )
                postseal_receipt_sha256 = postseal_receipt["receipt_sha256"]
                atomic_write_json(postseal_receipt_path, postseal_receipt)
            return {
                **package_result,
                "stored_path": str(self.store.candidate_path(project_id, candidate_id)),
                "stored_validation": stored,
                "repository": repository_payload,
                "source_delta": delta,
                "ingestion": ingestion.as_dict(),
                "acceptance_checks": acceptance_health,
                "postseal_acceptance": postseal_acceptance,
                "postseal_acceptance_receipt": (
                    str(postseal_receipt_path) if postseal_receipt_path else None
                ),
                "postseal_acceptance_receipt_sha256": postseal_receipt_sha256,
                "lane_refresh": lane_report,
                "next_action": next_action_contract,
                "mode_execution": mode_execution,
                "toolchain_manifest_sha256": engine_identity.toolchain_manifest_sha256,
                "toolchain_package_count": len(toolchain["packages"]),
            }
        finally:
            resolved_build = build_root.resolve()
            resolved_parent = build_parent.resolve()
            try:
                resolved_build.relative_to(resolved_parent)
            except ValueError as exc:
                raise EvidenceLaneError(
                    "BUILD_CLEANUP_PATH_ESCAPE",
                    "The temporary build path escaped the governed build directory.",
                    status="FAIL",
                ) from exc
            shutil.rmtree(resolved_build, ignore_errors=True)

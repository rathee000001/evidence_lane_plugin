from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin import database
from evidence_lane_plugin.compact_storage import decompress_exact_bytes
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import (
    atomic_write_bytes,
    atomic_write_json,
    sha256_file,
)
from evidence_lane_plugin.pv_package import validate_pv_package
from evidence_lane_plugin.service import EvidenceLaneService
from evidence_lane_plugin.source_policy import known_environment_secrets
from evidence_lane_plugin.topology import _renderer_environment

from .conftest import boot_local


def test_live_root_initial_build_bootstraps_pv0_without_hil_or_candidate(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    project_root = tmp_path / "live-projects" / "live-root-project"
    application = EvidenceLaneService(data_root=tmp_path / "store-current")
    registered = application.register_project(
        project_id="live-root-project",
        display_name="Live Root Project",
        repository_path=str(source_repository),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
        project_authority_root=str(project_root),
    )
    assert registered["status"] == "PASS"
    application.plan_tasks(
        "live-root-project",
        tasks=[
            {
                "task_id": "initial-source-intake-pv0",
                "task_class": "verify_result",
                "requested_outcome": (
                    "Materialize Source Intake and establish the no-HIL PV0 baseline."
                ),
                "permitted_paths": [],
                "permitted_tools": ["repository_read"],
                "acceptance_checks": ["PV0 exists at generation zero without HIL."],
                "stop_condition": "Continue the accepted initial Plan after PV0.",
            }
        ],
        planned_by="human-test",
        plan_id="initial-evi-plan",
    )
    boot = application.boot_session(
        project_id="live-root-project",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"permission_mode": "test"},
        host_session_id="host-session-pv0-test",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )

    result = application.build_initial(
        "live-root-project", boot["session"]["session_id"]
    )

    assert result["status"] == "PASS"
    assert result["pointer"]["accepted_pv"] == "PV0"
    assert result["pointer"]["generation"] == 0
    assert result["candidate"] is None
    assert result["candidate_created"] is False
    assert result["human_hil_required"] is False
    assert result["hil_choices"] == []
    assert result["next_action"] == "CONTINUE_ACTIVE_GOAL_AND_STEP_TASK_LIST"
    assert result["next_action_contract"]["evi_plan_completed_before_pv0"] is True
    assert result["source_intake"]["working_authority_refresh"]["status"] == "PASS"
    assert (
        result["source_intake"]["working_authority_refresh"][
            "pv0_bootstrap_pending"
        ]
        is True
    )
    assert (project_root / "sectors" / "manifest.json").is_file()
    assert not (project_root / "candidates").exists()
    assert result["accepted_archive_opened"] is False
    assert result["accepted_archive_queried"] is False


def test_live_root_initial_build_requires_evi_plan_before_source_work(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    project_root = tmp_path / "live-projects" / "plan-first-project"
    application = EvidenceLaneService(data_root=tmp_path / "hidden-control")
    application.register_project(
        project_id="plan-first-project",
        display_name="Plan First Project",
        repository_path=str(source_repository),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        project_authority_root=str(project_root),
    )
    boot = application.boot_session(
        project_id="plan-first-project",
        user_id="user-test",
        workspace_id=str(source_repository),
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"permission_mode": "test"},
        host_session_id="host-session-plan-first",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )

    with pytest.raises(EvidenceLaneError) as blocked:
        application.build_initial(
            "plan-first-project",
            boot["session"]["session_id"],
        )
    assert blocked.value.code == "PV0_INITIAL_PLAN_REQUIRED"
    assert not (project_root / "sectors").exists()


def test_mermaid_renderer_uses_explicit_installed_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"test-browser-placeholder")
    monkeypatch.setenv("PUPPETEER_EXECUTABLE_PATH", str(browser))
    environment, resolved = _renderer_environment()
    assert resolved == str(browser.resolve())
    assert environment["PUPPETEER_EXECUTABLE_PATH"] == resolved


def test_database_context_closes_connection_and_wal_handles(tmp_path: Path) -> None:
    path = tmp_path / "code.sqlite"
    database.initialize(path)

    with database.connect(path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("close_probe", "PASS"),
        )

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_initial_pv_captures_svelte_compact_exact_bytes_and_fts(
    service,
    source_repository: Path,
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    result = service.build_initial("book-faires", session_id)
    assert result["next_action"] == "PRESENT_PROJECT_AUTHORITY_HIL"
    assert result["suggested_next_prompt"].startswith("/evi-build ")
    assert result["next_action_contract"] == result["candidate"]["next_action"]
    assert not (service.store.project_root("book-faires") / ".build").exists()
    candidate = Path(result["candidate"]["stored_path"])
    validation = validate_pv_package(candidate)
    assert validation["status"] == "PASS"
    assert validation["proposed_pv"] == "PV1"
    exit_slip = json.loads((candidate / "exit_slip.json").read_text(encoding="utf-8"))
    assert exit_slip["next_action"] == result["next_action_contract"]
    assert validation["rendering_status"] in {
        "PASS",
        "RENDER_SKIPPED",
        "RENDER_FAILED",
    }
    assert sorted(path.name for path in candidate.iterdir() if path.is_file())[:1]
    with sqlite3.connect(candidate / "code.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        svelte = connection.execute(
            """
            SELECT f.path, f.size_bytes, f.sha256, f.code_family,
                   f.ingestion_status, cas.compression, cas.compressed_bytes
            FROM files AS f
            JOIN file_content_cas AS cas ON cas.sha256=f.sha256
            WHERE f.path = 'src/Counter.svelte'
            """
        ).fetchone()
        assert svelte is not None
        assert svelte["code_family"] == "svelte"
        assert svelte["ingestion_status"] == "EXACT_TEXT_CHUNKED"
        assert decompress_exact_bytes(
            compression=str(svelte["compression"]),
            payload=bytes(svelte["compressed_bytes"]),
            expected_size=int(svelte["size_bytes"]),
            expected_sha256=str(svelte["sha256"]),
        ) == (source_repository / "src" / "Counter.svelte").read_bytes()
        chunks = connection.execute(
            """
            SELECT COUNT(*)
            FROM chunks c JOIN files f ON f.file_id = c.file_id
            WHERE f.path = 'src/Counter.svelte'
            """
        ).fetchone()[0]
        assert chunks >= 1
        search = connection.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'increment'"
        ).fetchone()[0]
        assert search >= 1
        symbols = connection.execute(
            """
            SELECT COUNT(*)
            FROM symbols s JOIN files f ON f.file_id = s.file_id
            WHERE f.path = 'src/Counter.svelte' AND s.name = 'increment'
            """
        ).fetchone()[0]
        assert symbols == 1
    forbidden = [
        path.name.lower()
        for path in candidate.rglob("*")
        if path.is_file() and path.name.lower() in {"env.json", "uop.json"}
    ]
    assert forbidden == []


def test_sealed_candidate_contains_zero_exact_environment_secret_occurrences(
    service,
    source_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-proj-candidate-package-regression-0123456789"
    monkeypatch.setenv("CANDIDATE_PACKAGE_REGRESSION_API_KEY", secret)
    known_environment_secrets.cache_clear()
    (source_repository / ".env.local").write_text(
        f"OPENAI_API_KEY={secret}\n", encoding="utf-8"
    )
    (source_repository / ".runtime").mkdir()
    (source_repository / ".runtime" / "session.json").write_text(
        '{"operational":true}\n', encoding="utf-8"
    )
    (source_repository / "configured-secret.txt").write_text(
        secret, encoding="utf-8"
    )
    (source_repository / "untracked-operational.txt").write_text(
        secret, encoding="utf-8"
    )
    (source_repository / ".gitignore").write_text(
        "untracked-operational.txt\n", encoding="utf-8"
    )
    subprocess.run(
        [
            "git",
            "add",
            "-f",
            ".env.local",
            ".runtime/session.json",
            "configured-secret.txt",
            ".gitignore",
        ],
        cwd=source_repository,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add source-policy fixtures"],
        cwd=source_repository,
        check=True,
        capture_output=True,
        text=True,
    )

    boot = boot_local(service)
    result = service.build_initial("book-faires", boot["session"]["session_id"])
    candidate = Path(result["candidate"]["stored_path"])

    assert validate_pv_package(candidate)["status"] == "PASS"
    secret_bytes = secret.encode("utf-8")
    assert not [
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*")
        if path.is_file() and secret_bytes in path.read_bytes()
    ]
    with sqlite3.connect(candidate / "code.sqlite") as connection:
        indexed = {
            str(row[0])
            for row in connection.execute(
                "SELECT path FROM files WHERE path IN (?, ?, ?)",
                (".env.local", ".runtime/session.json", "configured-secret.txt"),
            )
        }
    assert indexed == set()
    assert not (candidate / ".env.local").exists()
    assert not (candidate / ".runtime").exists()
    known_environment_secrets.cache_clear()


def test_tamper_is_detected(service) -> None:
    boot = boot_local(service)
    result = service.build_initial("book-faires", boot["session"]["session_id"])
    candidate = Path(result["candidate"]["stored_path"])
    receipt = candidate / "entry_slip.json"
    receipt.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(EvidenceLaneError) as error:
        validate_pv_package(candidate)
    assert error.value.code == "PV_CHECKSUM_MISMATCH"


def test_renderer_failure_is_warning_not_sqlite_failure(
    service, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EVIDENCE_LANE_MERMAID_CLI", raising=False)
    boot = boot_local(service)
    result = service.build_initial("book-faires", boot["session"]["session_id"])
    candidate = Path(result["candidate"]["stored_path"])
    manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    receipt = json.loads((candidate / "pv_receipt.json").read_text(encoding="utf-8"))
    assert manifest["rendering"]["status"] == "RENDER_SKIPPED"
    assert receipt["status"] == "PASS"
    assert receipt["database_validation"]["valid"] is True


def test_resealed_outer_package_cannot_hide_stale_render_receipt(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVIDENCE_LANE_MERMAID_CLI", raising=False)
    boot = boot_local(service)
    result = service.build_initial("book-faires", boot["session"]["session_id"])
    candidate = Path(result["candidate"]["stored_path"])
    topology = candidate / "project_master_topology.mmd"
    topology.write_text(
        topology.read_text(encoding="utf-8") + "%% stale-after-render\n",
        encoding="utf-8",
    )

    manifest_path = candidate / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for member in manifest["members"]:
        if member["path"] == "project_master_topology.mmd":
            member["sha256"] = sha256_file(topology)
            member["bytes"] = topology.stat().st_size
    atomic_write_json(manifest_path, manifest)
    receipt_path = candidate / "pv_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["manifest_sha256"] = sha256_file(manifest_path)
    atomic_write_json(receipt_path, receipt)
    checksum_members = sorted(
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    atomic_write_bytes(
        candidate / "SHA256SUMS.txt",
        "".join(
            f"{sha256_file(candidate / name)} *{name}\n"
            for name in checksum_members
        ).encode("utf-8"),
    )

    with pytest.raises(EvidenceLaneError) as error:
        validate_pv_package(candidate)
    assert error.value.code == "PV_RENDER_RECEIPT_STALE"

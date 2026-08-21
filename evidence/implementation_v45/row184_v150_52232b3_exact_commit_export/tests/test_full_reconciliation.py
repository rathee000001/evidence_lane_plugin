from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.full_reconciliation import (
    reconcile_git_history,
    reconcile_source_matrix,
)
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    register_source_batch,
)
from evidence_lane_plugin.source_git_history import build_registered_git_history


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def test_source_matrix_accounts_every_source_against_all_18_lanes(
    tmp_path: Path,
) -> None:
    code = tmp_path / "sample.py"
    brain = tmp_path / "sample.sqlite"
    code.write_text("def sample():\n    return 1\n", encoding="utf-8")
    brain.write_bytes(b"fixture-sqlite-extension-evidence")
    registry = tmp_path / "authority.sqlite"
    register_source_batch(
        registry,
        [
            SourceAuthoritySpec(
                str(code),
                1,
                "local_code",
                assertions={"purpose": "code fixture"},
            ),
            SourceAuthoritySpec(
                str(brain),
                2,
                "brain_loader",
                assertions={"purpose": "brain fixture"},
            ),
        ],
    )
    crosswalk = {
        "expected_source_count": 2,
        "sources": [
            {
                "ordinal": 1,
                "name": code.name,
                "reason_for_presence": "code proof",
                "planned_use": "inspect code routing",
                "rejected_use": "no implicit execution",
                "license_state": "TEST_ONLY",
            },
            {
                "ordinal": 2,
                "name": brain.name,
                "reason_for_presence": "brain proof",
                "planned_use": "inspect brain routing",
                "rejected_use": "no implicit loading",
                "license_state": "TEST_ONLY",
            },
        ],
    }
    with _connect(registry) as connection:
        report = reconcile_source_matrix(
            connection,
            crosswalk,
            expected_source_count=2,
        )

    assert report["status"] == "PASS"
    assert report["lane_count"] == 18
    assert report["matrix_cell_count"] == 36
    cells = {
        (row["ordinal"], row["lane_id"]): row
        for row in report["matrix"]["cells"]
    }
    assert cells[(1, "local_code")]["state"] == "PRIMARY_ROUTE"
    assert cells[(1, "github_code")]["state"] == "COMPATIBLE_MEMBER_EVIDENCE"
    assert cells[(2, "brain_loader")]["state"] == "PRIMARY_ROUTE"
    assert cells[(2, "sqlite_brain")]["state"] == "COMPATIBLE_MEMBER_EVIDENCE"


def test_full_history_reconciliation_rebuilds_roots_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Reconciliation Test")
    _git(repository, "config", "user.email", "reconciliation@example.invalid")
    source = repository / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "source.py")
    _git(repository, "commit", "-m", "root")
    source.write_text("VALUE = 2\n", encoding="utf-8")
    _git(repository, "add", "source.py")
    _git(repository, "commit", "-m", "change")

    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(
        registry,
        [SourceAuthoritySpec(str(repository), 1, "local_code")],
    )
    build_registered_git_history(registry, str(batch["batch_id"]), 1)
    with _connect(registry) as connection:
        report = reconcile_git_history(connection)

    assert report["status"] == "PASS_WITH_EXPLICIT_GRAPH_BOUNDARY"
    assert report["snapshot_count"] == 1
    assert report["commit_count"] == 2
    assert report["snapshots"][0]["full_reachable_history"] is True

    with _connect(registry) as connection:
        connection.execute(
            "UPDATE source_git_snapshot SET history_root_sha256=?",
            ("0" * 64,),
        )
        connection.commit()
    with (
        _connect(registry) as connection,
        pytest.raises(EvidenceLaneError) as mismatch,
    ):
        reconcile_git_history(connection)
    assert mismatch.value.code == "FULL_RECONCILIATION_GIT_SEAL_MISMATCH"

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from evidence_lane_plugin.git_history import index_git_history
from evidence_lane_plugin.ingest import governed_source_files
from evidence_lane_plugin.source_policy import (
    content_exclusion_reason,
    known_environment_secrets,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repository(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "policy@example.invalid")
    _git(root, "config", "user.name", "Source Policy Test")


def test_git_source_inventory_is_tracked_only_and_secret_safe(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "sk-proj-unit-test-secret-value-0123456789"
    monkeypatch.setenv("SOURCE_POLICY_TEST_API_KEY", secret)
    known_environment_secrets.cache_clear()
    root = tmp_path / "repository"
    root.mkdir()
    _init_repository(root)
    (root / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    (root / ".env").write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    (root / ".runtime").mkdir()
    (root / ".runtime" / "session.json").write_text(
        '{"state":"live"}\n', encoding="utf-8"
    )
    (root / "configured.txt").write_text(secret, encoding="utf-8")
    _git(root, "add", "safe.py", ".env", ".runtime/session.json", "configured.txt")
    _git(root, "commit", "-m", "tracked fixture")
    (root / "untracked-operational.log").write_text(secret, encoding="utf-8")

    selection, included, excluded = governed_source_files(root)

    assert selection == "GIT_TRACKED_ONLY"
    assert [path for path, _ in included] == ["safe.py"]
    assert {row["path"] for row in excluded} == {
        ".env",
        ".runtime/session.json",
        "configured.txt",
    }
    assert "untracked-operational.log" not in {
        path for path, _ in included
    } | {row["path"] for row in excluded}
    assert secret not in repr(excluded)
    known_environment_secrets.cache_clear()


def test_git_history_excludes_sensitive_paths_and_secret_blobs(
    tmp_path: Path, monkeypatch
) -> None:
    secret = "sk-proj-history-secret-value-0123456789"
    monkeypatch.setenv("SOURCE_POLICY_HISTORY_API_KEY", secret)
    known_environment_secrets.cache_clear()
    root = tmp_path / "history"
    root.mkdir()
    _init_repository(root)
    (root / ".env.local").write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    (root / "safe.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "leaked.txt").write_text(secret, encoding="utf-8")
    _git(root, "add", ".env.local", "safe.py", "leaked.txt")
    _git(root, "commit", "-m", "history fixture")

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    report = index_git_history(connection, root)

    assert report["status"] == "PASS"
    assert report["excluded_secret_blobs"] >= 1
    assert connection.execute(
        "SELECT COUNT(*) FROM git_chunk_occurrence WHERE path LIKE '.env%'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM git_file_change WHERE path LIKE '.env%'"
    ).fetchone()[0] == 0
    for row in connection.execute("SELECT exact_bytes FROM git_blob_cas"):
        assert secret.encode("utf-8") not in bytes(row[0])
    for row in connection.execute("SELECT text_content FROM git_content_chunk_cas"):
        assert secret not in str(row[0])
    connection.close()
    known_environment_secrets.cache_clear()


def test_obvious_token_fixtures_remain_source_but_realistic_tokens_fail_closed() -> None:
    fixture = b'TOKEN = "sk-proj-unit-test-fixture-01234567890123456789"\n'
    synthetic_sequence = b'TOKEN = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"\n'
    realistic = b'TOKEN = "sk-proj-A7b9C2d4E6f8G0h2J4k6L8m0N2p4R6t8"\n'

    assert content_exclusion_reason(fixture) is None
    assert content_exclusion_reason(synthetic_sequence) is None
    assert content_exclusion_reason(realistic) == "TOKEN_SHAPED_MATERIAL_EXCLUDED"

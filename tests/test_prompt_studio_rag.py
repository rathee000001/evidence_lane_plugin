from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "plugins" / "evidence-lane-plugin" / "evidence" / "prompt_studio"
BROWSER = (
    ROOT / "apps" / "evidence-lane-app"
    / "app"
    / "_data"
    / "studio-rag-index.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _browser_index_is_tracked() -> bool:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--error-unmatch",
            BROWSER.relative_to(ROOT).as_posix(),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0


def test_prompt_studio_rag_artifacts_are_hash_bound_and_queryable() -> None:
    if not _browser_index_is_tracked():
        return
    browser = json.loads(BROWSER.read_text(encoding="utf-8"))
    assert browser["schema"] == "EVIDENCE_LANE_PROMPT_STUDIO_RAG_V1"
    assert browser["release"] == "3.0.0"
    assert browser["history_mode"] == "LIVE_GIT"
    assert browser["tools"]["chunker"].startswith("llama-index-core==0.14.23")
    assert browser["tools"]["provider"].startswith("none;")
    assert browser["source_count"] >= 90
    assert browser["chunk_count"] >= 250
    source_paths = {source["path"] for source in browser["sources"]}
    assert "docs/UPSTREAM_REFERENCE_PROVENANCE.md" in source_paths
    assert (
        "apps/evidence-lane-app/app/_data/upstream-references.ts"
        in source_paths
    )
    assert (
        "apps/evidence-lane-app/app/_data/current-execution-plan.ts"
        in source_paths
    )
    assert "plugins/evidence-lane-plugin/src/evidence_lane_plugin/session.py" in source_paths
    assert (
        "apps/evidence-lane-app/app/_components/lane-proof-explorer.tsx"
        in source_paths
    )
    assert "apps/evidence-lane-app/app/readme/page.tsx" in source_paths
    assert "apps/evidence-lane-app/app/security/page.tsx" in source_paths

    local_outputs = {
        "manifest": EVIDENCE / "manifest.json",
        "sqlite": EVIDENCE / "studio_search.sqlite",
    }
    if not all(path.is_file() for path in local_outputs.values()):
        return

    manifest = json.loads(
        local_outputs["manifest"].read_text(encoding="utf-8")
    )
    sqlite_path = local_outputs["sqlite"]
    assert manifest["schema"] == browser["schema"]
    assert manifest["release"] == browser["release"]
    assert manifest["history_mode"] == browser["history_mode"]
    assert "ancestor Git metadata" in manifest["corpus"]["boundary"]
    assert manifest["validation"]["sqlite_integrity"] == "ok"
    assert manifest["validation"]["secret_scan"] == "PASS"
    assert manifest["outputs"]["sqlite"]["sha256"] == _sha256(sqlite_path)
    assert manifest["outputs"]["browser_json"]["sha256"] == _sha256(BROWSER)
    assert browser["corpus_sha256"] == manifest["corpus"]["sha256"]
    assert browser["source_count"] == manifest["corpus"]["source_count"]
    assert browser["chunk_count"] == manifest["corpus"]["chunk_count"]

    with sqlite3.connect(sqlite_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        source_count = connection.execute("SELECT count(*) FROM source_registry").fetchone()[0]
        chunk_count = connection.execute("SELECT count(*) FROM chunk_index").fetchone()[0]
        refresh_hits = connection.execute(
            "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'refresh'"
        ).fetchone()[0]
        human_hits = connection.execute(
            "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'human AND acceptance'"
        ).fetchone()[0]
        tfidf_terms = connection.execute("SELECT count(*) FROM tfidf_term").fetchone()[0]
        tfidf_vectors = connection.execute("SELECT count(*) FROM tfidf_vector").fetchone()[0]
        joined_vectors = connection.execute(
            """SELECT count(*)
               FROM tfidf_vector AS vector
               JOIN chunk_index AS chunk
                 ON chunk.chunk_rowid = vector.chunk_rowid
               JOIN tfidf_term AS term
                 ON term.term_id = vector.term_id
               WHERE chunk.chunk_id <> '' AND term.term <> ''"""
        ).fetchone()[0]
        fts_schema = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='chunks_fts'"
        ).fetchone()[0]

    assert metadata["corpus_sha256"] == browser["corpus_sha256"]
    assert metadata["history_through_sha"] == browser["history_through_sha"]
    assert metadata["llama_index_core"] == "0.14.23"
    assert metadata["storage_schema"] == (
        "external-content FTS5 + compact integer-key materialized TF-IDF v3"
    )
    assert source_count == browser["source_count"]
    assert chunk_count == browser["chunk_count"]
    assert refresh_hits > 0
    assert human_hits > 0
    assert tfidf_terms > 500
    assert tfidf_vectors > chunk_count
    assert joined_vectors == tfidf_vectors
    assert "content='chunk_index'" in fts_schema
    assert int(manifest["validation"]["sqlite_size_bytes"]) == sqlite_path.stat().st_size
    assert manifest["validation"]["sqlite_fixed_size_cap"] is False


def test_prompt_studio_local_evidence_is_excluded_from_git_and_packages() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    assert "plugins/evidence-lane-plugin/evidence/" in ignored
    assert "plugins/evidence-lane-plugin/_evidence_lane_rehearsal/" in ignored


def test_prompt_studio_public_corpus_excludes_private_runtime_paths() -> None:
    if not _browser_index_is_tracked():
        return
    browser = json.loads(BROWSER.read_text(encoding="utf-8"))
    normalized_paths = [source["path"].casefold().replace("\\", "/") for source in browser["sources"]]

    assert all("/.runtime/" not in f"/{path}" for path in normalized_paths)
    assert all("/.env" not in f"/{path}" for path in normalized_paths)
    assert all("/projects/" not in f"/{path}" for path in normalized_paths)
    assert all("accepted_pointer" not in path for path in normalized_paths)
    assert all("session_flash/" not in f"/{path}" for path in normalized_paths)
    assert all("studio-rag-index.json" not in path for path in normalized_paths)
    assert all("dummy-lane-artifacts.json" not in path for path in normalized_paths)
    assert all(".egg-info/" not in path for path in normalized_paths)
    assert any(path.startswith("git/history/") for path in normalized_paths)

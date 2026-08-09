from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_WHOLE_SOURCE_FILE_BYTES = 16 * 1024 * 1024
EVIDENCE = ROOT / "plugins" / "evidence-lane-plugin" / "evidence" / "prompt_studio"
BROWSER = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "remote_adapter"
    / "app"
    / "_data"
    / "studio-rag-index.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_prompt_studio_rag_artifacts_are_hash_bound_and_queryable() -> None:
    manifest = json.loads((EVIDENCE / "manifest.json").read_text(encoding="utf-8"))
    browser = json.loads(BROWSER.read_text(encoding="utf-8"))
    sqlite_path = EVIDENCE / "studio_search.sqlite"

    assert manifest["schema"] == "EVIDENCE_LANE_PROMPT_STUDIO_RAG_V1"
    assert manifest["release"] == "1.4.0"
    assert manifest["history_mode"] == "FROZEN_SEALED_INDEX_NO_GIT"
    assert "no Git command is invoked" in manifest["corpus"]["boundary"]
    assert manifest["validation"]["sqlite_integrity"] == "ok"
    assert manifest["validation"]["secret_scan"] == "PASS"
    assert manifest["outputs"]["sqlite"]["sha256"] == _sha256(sqlite_path)
    assert manifest["outputs"]["browser_json"]["sha256"] == _sha256(BROWSER)
    assert browser["corpus_sha256"] == manifest["corpus"]["sha256"]
    assert browser["history_mode"] == manifest["history_mode"]
    assert browser["source_count"] == manifest["corpus"]["source_count"]
    assert browser["chunk_count"] == manifest["corpus"]["chunk_count"]
    assert browser["tools"]["chunker"].startswith("llama-index-core==0.14.23")
    assert browser["tools"]["provider"].startswith("none;")
    assert browser["source_count"] >= 90
    assert browser["chunk_count"] >= 250
    source_paths = {source["path"] for source in browser["sources"]}
    assert "docs/UPSTREAM_REFERENCE_PROVENANCE.md" in source_paths
    assert (
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/upstream-references.ts"
        in source_paths
    )
    assert (
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/current-execution-plan.ts"
        in source_paths
    )
    assert "plugins/evidence-lane-plugin/src/evidence_lane_plugin/session.py" in source_paths
    assert (
        "plugins/evidence-lane-plugin/remote_adapter/app/_components/lane-proof-explorer.tsx"
        in source_paths
    )
    assert "plugins/evidence-lane-plugin/remote_adapter/app/readme/page.tsx" in source_paths
    assert "plugins/evidence-lane-plugin/remote_adapter/app/security/page.tsx" in source_paths

    with sqlite3.connect(sqlite_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
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
    assert metadata["storage_schema"] == "external-content FTS5 + integer-key materialized TF-IDF v2"
    assert source_count == browser["source_count"]
    assert chunk_count == browser["chunk_count"]
    assert refresh_hits > 0
    assert human_hits > 0
    assert tfidf_terms > 500
    assert tfidf_vectors > chunk_count
    assert joined_vectors == tfidf_vectors
    assert "content='chunk_index'" in fts_schema
    assert sqlite_path.stat().st_size <= MAX_WHOLE_SOURCE_FILE_BYTES


def test_prompt_studio_public_corpus_excludes_private_runtime_paths() -> None:
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

"""Build the public-safe Prompt Studio SQLite and browser retrieval artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

LLAMA_INDEX_VERSION = "0.14.23"
SCHEMA = "EVIDENCE_LANE_PROMPT_STUDIO_RAG_V1"
_RUN_TIMEOUT_SECONDS = 60
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{1,63}", re.IGNORECASE)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "how", "in", "into", "is", "it", "of", "on", "or", "that",
    "the", "this", "to", "was", "what", "when", "where", "which", "with",
}


@dataclass(frozen=True)
class SourceDocument:
    path: str
    title: str
    href: str
    kind: str
    text: str


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(
        args,
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_RUN_TIMEOUT_SECONDS,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if token.lower() not in STOP_WORDS
    ]


def _assert_public_safe(path: str, text: str) -> None:
    lowered = path.lower().replace("\\", "/")
    forbidden = ("/.env", "/.runtime/", "/projects/", "accepted_pointer", "session_flash/")
    if any(marker in f"/{lowered}" for marker in forbidden):
        raise RuntimeError(f"public corpus path is forbidden: {path}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise RuntimeError(f"secret-like token found in public corpus: {path}")


PUBLIC_TEXT_SUFFIXES = {
    ".css", ".cjs", ".js", ".json", ".md", ".py", ".toml", ".ts", ".tsx",
    ".txt", ".yaml", ".yml",
}
PUBLIC_PLUGIN_EXCLUSIONS = (
    "plugins/evidence-lane-plugin/evidence/",
    "plugins/evidence-lane-plugin/remote_adapter/.vercel/",
    "plugins/evidence-lane-plugin/remote_adapter/public/",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/session_flash/",
)
PUBLIC_PLUGIN_EXACT_EXCLUSIONS = {
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/dummy-lane-artifacts.json",
    # The retrieval implementation contains its own confidence canaries. Indexing
    # those questions would let the corpus answer them from the test definition
    # itself and turn deliberate no-hits into false positives.
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/studio-retrieval.ts",
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/studio-rag-index.json",
    "plugins/evidence-lane-plugin/requirements.lock.txt",
    "plugins/evidence-lane-plugin/remote_adapter/package-lock.json",
}
LOCAL_SCAN_IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    "build",
    "node_modules",
}
FROZEN_NO_GIT_ADDITIONS = {
    "plugins/evidence-lane-plugin/COPYRIGHT.md",
    "plugins/evidence-lane-plugin/LICENSE.md",
    "plugins/evidence-lane-plugin/README.md",
    "plugins/evidence-lane-plugin/THIRD_PARTY_NOTICES.md",
    "plugins/evidence-lane-plugin/remote_adapter/app/_components/hero-orbit.tsx",
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/governed-linked-deltas.ts",
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/prior-execution-plan.ts",
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/release-identity.ts",
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/website-current-execution.ts",
    "plugins/evidence-lane-plugin/remote_adapter/app/api/studio-query/openrouter-general.ts",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/goal_usage.py",
    "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_stdio_compat.py",
    "plugins/evidence-lane-plugin/scripts/build_release_candidate_rehearsal.py",
}
FROZEN_NO_GIT_FIXED_ADDITIONS = {
    "docs/DEPENDENCY_LICENSE_AUDIT.md",
}


def _github_blob(path: str, revision: str) -> str:
    return f"https://github.com/rathee000001/evidence_lane_plugin/blob/{revision}/{path}"


def _public_plugin_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if not normalized.startswith("plugins/evidence-lane-plugin/"):
        return False
    if normalized in PUBLIC_PLUGIN_EXACT_EXCLUSIONS:
        return False
    if any(normalized.startswith(prefix) for prefix in PUBLIC_PLUGIN_EXCLUSIONS):
        return False
    name = Path(normalized).name
    return Path(normalized).suffix.lower() in PUBLIC_TEXT_SUFFIXES or name in {
        "Dockerfile", "requirements.in",
    }


def _plugin_source_kind(path: str) -> str:
    if "/skills/" in path or "/commands/" in path:
        return "plugin_control"
    if "/remote_adapter/app/" in path:
        return "website_source"
    if "/remote_adapter/" in path:
        return "public_adapter_source"
    if "/src/evidence_lane_plugin/" in path:
        return "plugin_runtime_source"
    if "/scripts/" in path or "/hooks/" in path:
        return "plugin_build_source"
    return "plugin_contract"


def _plugin_source_title(path: str) -> str:
    relative = path.removeprefix("plugins/evidence-lane-plugin/")
    return f"Evidence Lane source: {relative}"


def _source_specs(
    repo: Path,
    revision: str,
    tracked_paths: set[str],
) -> list[tuple[Path, str, str, str]]:
    fixed: list[tuple[str, str, str, str]] = [
        ("README.md", "Repository README", _github_blob("README.md", revision), "documentation"),
        ("SECURITY.md", "Security policy", _github_blob("SECURITY.md", revision), "policy"),
        ("LICENSE.md", "Proprietary license", _github_blob("LICENSE.md", revision), "policy"),
        ("COPYRIGHT.md", "Copyright and ownership", _github_blob("COPYRIGHT.md", revision), "policy"),
        ("docs/CREDITS_AND_CONTRIBUTIONS.md", "Credits and contribution policy", "/credits", "policy"),
        ("docs/DEPENDENCY_LICENSE_AUDIT.md", "Direct dependency license audit", "/credits", "policy"),
        ("docs/UPSTREAM_REFERENCE_PROVENANCE.md", "Upstream reference provenance", "/credits", "provenance"),
    ]
    specs = [(repo / path, title, href, kind) for path, title, href, kind in fixed]
    internal_hrefs = {
        "plugins/evidence-lane-plugin/remote_adapter/app/page.tsx": "/",
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/delta-ledger.ts": "/#delta-ledger",
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/current-execution-plan.ts": "/#delta-ledger",
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/lane-contracts.ts": "/lanes",
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/mode-governance.json": "/operators",
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/plugin-surfaces.ts": "/architecture",
        "plugins/evidence-lane-plugin/remote_adapter/app/_data/upstream-references.ts": "/provenance",
    }
    for route in (
        "architecture", "connect", "copyright", "credits", "hil", "lanes", "license",
        "operators", "privacy", "proof", "provenance", "readme", "security", "studio",
        "support", "terms",
    ):
        internal_hrefs[f"plugins/evidence-lane-plugin/remote_adapter/app/{route}/page.tsx"] = f"/{route}"
    for relative in sorted(path for path in tracked_paths if _public_plugin_path(path)):
        href = internal_hrefs.get(relative, _github_blob(relative, revision))
        specs.append(
            (repo / relative, _plugin_source_title(relative), href, _plugin_source_kind(relative))
        )
    return specs


def _load_documents(
    repo: Path,
    *,
    tracked_paths: set[str] | None = None,
    revision: str | None = None,
) -> list[SourceDocument]:
    if tracked_paths is None:
        tracked_paths = {
            value
            for value in _run(repo, "git", "ls-files", "-z").split("\0")
            if value
        }
    if revision is None:
        revision = _run(repo, "git", "rev-parse", "HEAD")
    documents: list[SourceDocument] = []
    for path, title, href, kind in _source_specs(repo, revision, tracked_paths):
        if not path.is_file():
            raise FileNotFoundError(f"required public corpus source is missing: {path}")
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
        relative = path.relative_to(repo).as_posix()
        if relative not in tracked_paths:
            raise RuntimeError(f"public corpus source is not Git-tracked: {relative}")
        _assert_public_safe(relative, text)
        documents.append(SourceDocument(relative, title, href, kind, text))
    return documents


def _local_public_plugin_paths(repo: Path) -> set[str]:
    paths: set[str] = set()
    plugin_root = repo / "plugins/evidence-lane-plugin"
    for path in plugin_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(repo)
        if LOCAL_SCAN_IGNORED_PARTS.intersection(relative.parts) or any(
            part.casefold().endswith(".egg-info") for part in relative.parts
        ):
            continue
        normalized = relative.as_posix()
        if _public_plugin_path(normalized):
            paths.add(normalized)
    return paths


def _frozen_history_inputs(
    repo: Path,
    artifact_path: Path,
) -> tuple[
    list[SourceDocument],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str,
    str,
]:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("schema") != SCHEMA:
        raise RuntimeError(f"frozen history schema mismatch: {artifact.get('schema')}")
    history_sha = str(artifact.get("history_through_sha") or "")
    history_date = str(artifact.get("history_through_date") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", history_sha):
        raise RuntimeError("frozen history input has no exact 40-character commit SHA")
    if not history_date:
        raise RuntimeError("frozen history input has no commit date")

    all_sources = list(artifact.get("sources") or [])
    git_sources = [row for row in all_sources if row.get("kind") == "git_history"]
    public_sources = [row for row in all_sources if row.get("kind") != "git_history"]
    tracked_paths = {str(row["path"]) for row in public_sources}
    indexed_plugin_paths = {
        path for path in tracked_paths if path.startswith("plugins/evidence-lane-plugin/")
    }
    live_plugin_paths = _local_public_plugin_paths(repo)
    expected_added = {
        path for path in FROZEN_NO_GIT_ADDITIONS if (repo / path).is_file()
    }
    if indexed_plugin_paths | expected_added != live_plugin_paths:
        missing = sorted(indexed_plugin_paths - live_plugin_paths)
        added = sorted(live_plugin_paths - indexed_plugin_paths - expected_added)
        raise RuntimeError(
            "frozen no-Git source manifest mismatch; "
            f"missing={missing[:20]!r}; added={added[:20]!r}"
        )
    tracked_paths.update(expected_added)
    tracked_paths.update(
        path for path in FROZEN_NO_GIT_FIXED_ADDITIONS if (repo / path).is_file()
    )
    documents = _load_documents(
        repo,
        tracked_paths=tracked_paths,
        revision=history_sha,
    )
    if {document.path for document in documents} != tracked_paths:
        raise RuntimeError("frozen no-Git source manifest did not round-trip exactly")

    first_history_source_id = len(documents) + 1
    old_to_new: dict[int, int] = {}
    frozen_source_rows: list[dict[str, Any]] = []
    for offset, row in enumerate(sorted(git_sources, key=lambda item: int(item["id"]))):
        old_id = int(row["id"])
        new_id = first_history_source_id + offset
        old_to_new[old_id] = new_id
        frozen_source_rows.append(
            {
                "id": new_id,
                "path": str(row["path"]),
                "title": str(row["title"]),
                "href": str(row["href"]),
                "kind": "git_history",
                "sha256": str(row["sha256"]),
                "bytes": int(row["bytes"]),
            }
        )

    frozen_chunks: list[dict[str, Any]] = []
    for row in artifact.get("chunks") or []:
        old_source_id = int(row["source_id"])
        if old_source_id not in old_to_new:
            continue
        text = str(row["text"])
        path = next(
            source["path"]
            for source in frozen_source_rows
            if source["id"] == old_to_new[old_source_id]
        )
        _assert_public_safe(str(path), text)
        term_counts = Counter(_tokens(text))
        frozen_chunks.append(
            {
                "id": str(row["id"]),
                "source_id": old_to_new[old_source_id],
                "ordinal": int(row["ordinal"]),
                "locator": str(row["locator"]),
                "text": text,
                "sha256": str(row["sha256"]),
                "token_count": sum(term_counts.values()),
                "term_counts": dict(sorted(term_counts.items())),
            }
        )
    if not frozen_source_rows or not frozen_chunks:
        raise RuntimeError("frozen history input contains no reusable Git evidence")
    return documents, frozen_source_rows, frozen_chunks, history_sha, history_date


def _git_documents(repo: Path) -> list[SourceDocument]:
    commits = _run(repo, "git", "rev-list", "--reverse", "HEAD").splitlines()
    documents: list[SourceDocument] = []
    for commit in commits:
        fields = _run(
            repo,
            "git",
            "show",
            "-s",
            "--format=%H%x1f%P%x1f%cI%x1f%s%x1f%b%x1f--END--",
            commit,
        ).split("\x1f")
        if len(fields) < 6 or fields[-1] != "--END--":
            raise RuntimeError(f"unexpected Git metadata shape for {commit}")
        sha, parents, committed_at, subject, body = fields[:5]
        changed = _run(repo, "git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
        text = "\n".join(
            part for part in (
                f"Git commit {sha}",
                f"Committed {committed_at}",
                f"Parents {parents or 'root'}",
                f"Subject {subject}",
                body.strip(),
                "Changed paths:\n" + (changed or "(root metadata only)"),
            ) if part
        )
        history_path = f"git/history/{sha}.txt"
        _assert_public_safe(history_path, text)
        documents.append(
            SourceDocument(
                path=history_path,
                title=f"Git {sha[:12]}: {subject}",
                href=f"https://github.com/rathee000001/evidence_lane_plugin/commit/{sha}",
                kind="git_history",
                text=text,
            )
        )
    return documents


def _chunk_documents(documents: list[SourceDocument]) -> list[dict[str, Any]]:
    try:
        from llama_index.core.node_parser import SentenceSplitter
    except ImportError as exc:
        raise RuntimeError(
            'llama-index-core is required; install the pinned extra with: pip install -e ".[rag]"'
        ) from exc
    installed = version("llama-index-core")
    if installed != LLAMA_INDEX_VERSION:
        raise RuntimeError(
            f"llama-index-core version mismatch: expected {LLAMA_INDEX_VERSION}, got {installed}"
        )
    splitter = SentenceSplitter(chunk_size=480, chunk_overlap=64)
    chunks: list[dict[str, Any]] = []
    for source_id, document in enumerate(documents, start=1):
        cursor = 0
        for ordinal, chunk_text in enumerate(splitter.split_text(document.text), start=1):
            normalized = chunk_text.strip()
            if not normalized:
                continue
            offset = document.text.find(normalized, cursor)
            if offset < 0:
                offset = document.text.find(normalized)
            cursor = max(cursor, offset + len(normalized))
            line = document.text.count("\n", 0, max(offset, 0)) + 1
            token_counts = Counter(_tokens(normalized))
            stable_id = _sha256_text(
                f"{document.path}\n{ordinal}\n{normalized}"
            )[:24]
            chunks.append(
                {
                    "id": stable_id,
                    "source_id": source_id,
                    "ordinal": ordinal,
                    "locator": f"line:{line}",
                    "text": normalized,
                    "sha256": _sha256_text(normalized),
                    "token_count": sum(token_counts.values()),
                    "term_counts": dict(sorted(token_counts.items())),
                }
            )
    return chunks


def _build_artifacts(
    repo: Path,
    *,
    frozen_git_history_from: Path | None = None,
) -> dict[str, Any]:
    evidence_dir = repo / "plugins/evidence-lane-plugin/evidence/prompt_studio"
    browser_path = repo / "plugins/evidence-lane-plugin/remote_adapter/app/_data/studio-rag-index.json"
    database_path = evidence_dir / "studio_search.sqlite"
    manifest_path = evidence_dir / "manifest.json"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    history_mode = "LIVE_GIT"
    frozen_source_rows: list[dict[str, Any]] = []
    frozen_chunks: list[dict[str, Any]] = []
    if frozen_git_history_from is None:
        documents = _load_documents(repo) + _git_documents(repo)
        history_sha = _run(repo, "git", "rev-parse", "HEAD")
        history_date = _run(repo, "git", "show", "-s", "--format=%cI", history_sha)
    else:
        history_mode = "FROZEN_SEALED_INDEX_NO_GIT"
        (
            documents,
            frozen_source_rows,
            frozen_chunks,
            history_sha,
            history_date,
        ) = _frozen_history_inputs(repo, frozen_git_history_from)
    chunks = _chunk_documents(documents) + frozen_chunks
    document_count = len(chunks)
    document_frequency: Counter[str] = Counter()
    for chunk in chunks:
        document_frequency.update(chunk["term_counts"].keys())
    idf = {
        term: math.log((1 + document_count) / (1 + frequency)) + 1
        for term, frequency in sorted(document_frequency.items())
    }
    for chunk in chunks:
        total = max(chunk["token_count"], 1)
        chunk["tfidf"] = [
            [term, count, (count / total) * idf[term]]
            for term, count in chunk.pop("term_counts").items()
        ]

    source_rows = []
    for source_id, document in enumerate(documents, start=1):
        source_rows.append(
            {
                "id": source_id,
                "path": document.path,
                "title": document.title,
                "href": document.href,
                "kind": document.kind,
                "sha256": _sha256_text(document.text),
                "bytes": len(document.text.encode("utf-8")),
            }
        )
    source_rows.extend(frozen_source_rows)

    corpus_binding = "\n".join(
        f"{row['path']}\t{row['sha256']}" for row in source_rows
    )
    corpus_sha = _sha256_text(corpus_binding)
    avg_length = sum(chunk["token_count"] for chunk in chunks) / max(len(chunks), 1)

    browser_artifact = {
        "schema": SCHEMA,
        "release": "1.5.0",
        "history_through_sha": history_sha,
        "history_through_date": history_date,
        "history_mode": history_mode,
        "corpus_sha256": corpus_sha,
        "source_count": len(source_rows),
        "chunk_count": len(chunks),
        "average_chunk_tokens": avg_length,
        "tools": {
            "chunker": f"llama-index-core=={LLAMA_INDEX_VERSION} SentenceSplitter(480,64)",
            "lexical": "SQLite FTS5/BM25 forensic authority",
            "tfidf": "tf=count/tokens; idf=ln((1+N)/(1+df))+1",
            "hybrid": "reciprocal-rank fusion k=60",
            "provider": "none; extractive local retrieval only",
        },
        "document_frequency": dict(sorted(document_frequency.items())),
        "sources": source_rows,
        "chunks": chunks,
    }
    browser_bytes = (json.dumps(browser_artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    browser_path.write_bytes(browser_bytes)

    if database_path.exists():
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        PRAGMA page_size=4096;
        PRAGMA auto_vacuum=NONE;
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        PRAGMA application_id=1162629459;
        PRAGMA user_version=2;
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE source_registry(
            source_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            href TEXT NOT NULL,
            kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL
        );
        CREATE TABLE chunk_index(
            chunk_rowid INTEGER PRIMARY KEY,
            chunk_id TEXT NOT NULL UNIQUE,
            source_id INTEGER NOT NULL REFERENCES source_registry(source_id),
            ordinal INTEGER NOT NULL,
            locator TEXT NOT NULL,
            text_content TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            token_count INTEGER NOT NULL,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            UNIQUE(source_id, ordinal)
        );
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id UNINDEXED,
            path UNINDEXED,
            title,
            text_content,
            content='chunk_index',
            content_rowid='chunk_rowid',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TABLE tfidf_term(
            term_id INTEGER PRIMARY KEY,
            term TEXT NOT NULL UNIQUE,
            document_frequency INTEGER NOT NULL,
            document_count INTEGER NOT NULL,
            idf REAL NOT NULL
        );
        CREATE TABLE tfidf_vector(
            chunk_rowid INTEGER NOT NULL REFERENCES chunk_index(chunk_rowid),
            term_id INTEGER NOT NULL REFERENCES tfidf_term(term_id),
            term_count INTEGER NOT NULL,
            token_count INTEGER NOT NULL,
            tf REAL NOT NULL,
            tfidf REAL NOT NULL,
            PRIMARY KEY(chunk_rowid, term_id)
        ) WITHOUT ROWID;
        """
    )
    metadata = {
        "schema": SCHEMA,
        "release": "1.5.0",
        "history_through_sha": history_sha,
        "history_through_date": history_date,
        "history_mode": history_mode,
        "corpus_sha256": corpus_sha,
        "source_count": str(len(source_rows)),
        "chunk_count": str(len(chunks)),
        "llama_index_core": LLAMA_INDEX_VERSION,
        "ranking": "SQLite FTS5/BM25 + materialized TF-IDF + RRF(k=60)",
        "storage_schema": "external-content FTS5 + integer-key materialized TF-IDF v2",
        "public_safe": "true",
    }
    connection.executemany(
        "INSERT INTO metadata(key,value) VALUES(?,?)",
        sorted(metadata.items()),
    )
    connection.executemany(
        "INSERT INTO source_registry VALUES(:id,:path,:title,:href,:kind,:sha256,:bytes)",
        source_rows,
    )
    source_by_id = {row["id"]: row for row in source_rows}
    for chunk in chunks:
        source = source_by_id[chunk["source_id"]]
        connection.execute(
            """INSERT INTO chunk_index(
                chunk_id,source_id,ordinal,locator,text_content,sha256,
                token_count,path,title
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                chunk["id"], chunk["source_id"], chunk["ordinal"], chunk["locator"],
                chunk["text"], chunk["sha256"], chunk["token_count"],
                source["path"], source["title"],
            ),
        )
    connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    connection.executemany(
        """INSERT INTO tfidf_term(
            term,document_frequency,document_count,idf
        ) VALUES(?,?,?,?)""",
        [(term, document_frequency[term], document_count, idf[term]) for term in sorted(idf)],
    )
    chunk_rowids = dict(connection.execute("SELECT chunk_id,chunk_rowid FROM chunk_index"))
    term_ids = dict(connection.execute("SELECT term,term_id FROM tfidf_term"))
    vector_rows = []
    for chunk in chunks:
        for term, count, score in chunk["tfidf"]:
            vector_rows.append(
                (
                    chunk_rowids[chunk["id"]], term_ids[term], count,
                    chunk["token_count"], count / max(chunk["token_count"], 1), score,
                )
            )
    connection.executemany(
        "INSERT INTO tfidf_vector VALUES(?,?,?,?,?,?)",
        vector_rows,
    )
    connection.commit()
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    fts_probe = connection.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'refresh'"
    ).fetchone()[0]
    connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    connection.commit()
    connection.execute("VACUUM")
    connection.close()
    if integrity != "ok" or fts_probe < 1:
        raise RuntimeError(f"retrieval artifact validation failed: integrity={integrity}, refresh_hits={fts_probe}")

    database_sha = _sha256_bytes(database_path.read_bytes())
    browser_sha = _sha256_bytes(browser_bytes)
    manifest = {
        "schema": SCHEMA,
        "release": "1.5.0",
        "history_through_sha": history_sha,
        "history_mode": history_mode,
        "history_commit_count": sum(1 for row in source_rows if row["kind"] == "git_history"),
        "corpus": {
            "boundary": (
                "all Git-tracked public-safe Evidence Lane plugin text plus canonical "
                "repository policies and ancestor Git metadata; generated proof binaries, "
                "retrieval self-inputs, private session flash, runtime state, brains, and "
                "secrets are excluded"
                if history_mode == "LIVE_GIT"
                else
                "the exact prior public source manifest re-read from current local bytes plus "
                "frozen, previously sealed ancestor Git chunks; no Git command is invoked; "
                "generated proof binaries, retrieval self-inputs, private session flash, "
                "runtime state, brains, and secrets are excluded"
            ),
            "sha256": corpus_sha,
            "source_count": len(source_rows),
            "chunk_count": len(chunks),
        },
        "retrieval": browser_artifact["tools"],
        "outputs": {
            "sqlite": {"path": database_path.relative_to(repo).as_posix(), "sha256": database_sha, "bytes": database_path.stat().st_size},
            "browser_json": {"path": browser_path.relative_to(repo).as_posix(), "sha256": browser_sha, "bytes": len(browser_bytes)},
        },
        "validation": {"sqlite_integrity": integrity, "fts_refresh_hits": fts_probe, "secret_scan": "PASS"},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frozen-git-history-from",
        type=Path,
        help=(
            "Regenerate current public source bytes without invoking Git while carrying "
            "forward exact previously sealed Git-history sources and chunks."
        ),
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    frozen_input = args.frozen_git_history_from
    if frozen_input is not None and not frozen_input.is_absolute():
        frozen_input = repo / frozen_input
    manifest = _build_artifacts(
        repo,
        frozen_git_history_from=frozen_input,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

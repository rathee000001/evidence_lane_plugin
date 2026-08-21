from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "build_prompt_studio_index.py"
)
FIXED_PUBLIC_PATHS = (
    "README.md",
    "ARCHITECTURE.md",
    "SECURITY.md",
    "LICENSE.md",
    "docs/COPYRIGHT.md",
    "docs/CREDITS_AND_CONTRIBUTIONS.md",
    "docs/DEPENDENCY_LICENSE_AUDIT.md",
    "docs/HOOKS.md",
    "docs/MCP.md",
    "docs/PUBLIC_SITE_SOURCE_MAP.md",
    "docs/SKILLS.md",
    "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
)
PLUGIN_PUBLIC_PATH = "plugins/evidence-lane-plugin/README.md"


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prompt_studio_index_test_builder", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def _prepare_frozen_fixture(repo: Path) -> Path:
    public_paths = (*FIXED_PUBLIC_PATHS, PLUGIN_PUBLIC_PATH)
    sources: list[dict[str, object]] = []
    for source_id, relative in enumerate(public_paths, start=1):
        text = f"Public Evidence Lane refresh and human acceptance proof for {relative}.\n"
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        sources.append(
            {
                "id": source_id,
                "path": relative,
                "title": relative,
                "href": f"https://example.test/{relative}",
                "kind": "plugin_contract" if relative == PLUGIN_PUBLIC_PATH else "policy",
                "sha256": _sha256_text(text.strip()),
                "bytes": len(text.strip().encode("utf-8")),
            }
        )

    git_source_id = len(sources) + 1
    git_text = "Frozen Git history evidence preserves refresh and human acceptance."
    sources.append(
        {
            "id": git_source_id,
            "path": "git/history/0123456789abcdef0123456789abcdef01234567.txt",
            "title": "Frozen Git evidence",
            "href": "https://example.test/commit/0123456789abcdef0123456789abcdef01234567",
            "kind": "git_history",
            "sha256": _sha256_text(git_text),
            "bytes": len(git_text.encode("utf-8")),
        }
    )
    frozen = {
        "schema": "EVIDENCE_LANE_PROMPT_STUDIO_RAG_V1",
        "history_through_sha": "0123456789abcdef0123456789abcdef01234567",
        "history_through_date": "2026-08-09T00:00:00Z",
        "sources": sources,
        "chunks": [
            {
                "id": "FROZEN-GIT-CHUNK",
                "source_id": git_source_id,
                "ordinal": 1,
                "locator": "line:1",
                "text": git_text,
                "sha256": _sha256_text(git_text),
            }
        ],
    }
    frozen_path = repo.parent / "frozen-studio-index.json"
    frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
    (repo / "plugins/evidence-lane-plugin/remote_adapter/app/_data").mkdir(
        parents=True,
        exist_ok=True,
    )
    return frozen_path


def _forbid_process_execution(*_args: object, **_kwargs: object) -> str:
    raise AssertionError("frozen no-Git regeneration attempted external process execution")


def test_frozen_history_regeneration_never_invokes_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_builder()
    repo = tmp_path / "repo"
    frozen_path = _prepare_frozen_fixture(repo)
    monkeypatch.setattr(builder, "_run", _forbid_process_execution)

    manifest = builder._build_artifacts(
        repo,
        frozen_git_history_from=frozen_path,
    )

    assert manifest["history_mode"] == "FROZEN_SEALED_INDEX_NO_GIT"
    assert manifest["history_commit_count"] == 1
    assert "no Git command is invoked" in manifest["corpus"]["boundary"]
    assert manifest["validation"]["sqlite_integrity"] == "ok"
    assert manifest["validation"]["fts_refresh_hits"] == 14
    assert manifest["validation"]["secret_scan"] == "PASS"
    assert manifest["validation"]["sqlite_fixed_size_cap"] is False
    assert manifest["validation"]["sqlite_size_bytes"] > 0
    browser = json.loads(
        (
            repo
            / "plugins/evidence-lane-plugin/remote_adapter/app/_data/studio-rag-index.json"
        ).read_text(encoding="utf-8")
    )
    assert browser["history_mode"] == "FROZEN_SEALED_INDEX_NO_GIT"
    assert any(source["kind"] == "git_history" for source in browser["sources"])


def test_frozen_history_rejects_an_unlisted_public_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _load_builder()
    repo = tmp_path / "repo"
    frozen_path = _prepare_frozen_fixture(repo)
    unexpected = repo / "plugins/evidence-lane-plugin/unlisted.py"
    unexpected.write_text("UNLISTED = True\n", encoding="utf-8")
    monkeypatch.setattr(builder, "_run", _forbid_process_execution)

    with pytest.raises(RuntimeError, match="frozen no-Git source manifest mismatch"):
        builder._frozen_history_inputs(repo, frozen_path)

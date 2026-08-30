from __future__ import annotations

import json
import tomllib
from pathlib import Path

from evidence_lane_plugin.context_index_routing import context_index_catalog
from evidence_lane_plugin.hybrid_retrieval import (
    RetrievalCandidate,
    VectorRetrievalRequest,
    faiss_vector_rank,
    rank_bm25_candidates,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "evidence-lane-plugin"


def test_unpatched_chromadb_is_absent_from_dependency_inputs_and_locks() -> None:
    project = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [str(value).casefold() for value in project["project"]["dependencies"]]
    assert not any(value.startswith("chromadb") for value in dependencies)
    for relative in (
        "requirements.in",
        "requirements.toolchain.in",
        "plugins/evidence-lane-plugin/requirements.lock.txt",
        "plugins/evidence-lane-plugin/requirements.toolchain.lock.txt",
    ):
        assert "chromadb" not in (REPOSITORY_ROOT / relative).read_text(
            encoding="utf-8"
        ).casefold()


def test_chromadb_has_no_executable_or_generated_tool_route() -> None:
    matrix = json.loads(
        (PLUGIN_ROOT / "toolchains" / "tool-requirement-matrix.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert "ChromaDB" not in {str(row["tool"]) for row in matrix["requirements"]}
    for relative in (
        "src/evidence_lane_plugin/ai_toolchain.py",
        "src/evidence_lane_plugin/context_index_routing.py",
        "src/evidence_lane_plugin/hybrid_retrieval.py",
        "src/evidence_lane_plugin/lane_engine.py",
        "src/evidence_lane_plugin/runtime_toolchain.py",
        "scripts/generate_toolchain_execution_matrix.py",
    ):
        text = (PLUGIN_ROOT / relative).read_text(encoding="utf-8")
        assert "ChromaDB" not in text
        assert "chromadb" not in text.casefold()


def test_chromadb_license_record_is_directly_purged() -> None:
    assert not (
        PLUGIN_ROOT
        / "toolchains"
        / "licenses"
        / "requirements"
        / "069-chromadb"
        / "LICENSE-RECORD.json"
    ).exists()


def test_retained_local_retrieval_routes_still_work() -> None:
    catalog = context_index_catalog()
    tool_ids = {str(row["tool_id"]) for row in catalog["indexes"]}
    assert {"SQLite_FTS5_BM25", "FAISS_CPU"}.issubset(tool_ids)
    assert "ChromaDB" not in tool_ids

    lexical = rank_bm25_candidates(
        "alpha",
        [
            RetrievalCandidate(candidate_id="a", text="alpha beta"),
            RetrievalCandidate(candidate_id="b", text="gamma delta"),
        ],
        limit=1,
    )
    assert lexical["status"] == "PASS"
    assert lexical["results"][0]["candidate_id"] == "a"

    vector = faiss_vector_rank(
        VectorRetrievalRequest(
            candidate_ids=["a", "b"],
            vectors=[[1.0, 0.0], [0.0, 1.0]],
            query_vector=[1.0, 0.0],
            limit=1,
        )
    )
    assert vector["status"] == "PASS"
    assert vector["results"][0]["candidate_id"] == "a"

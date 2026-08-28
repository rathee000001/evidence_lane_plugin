from __future__ import annotations

import csv
import importlib.util
import os
from pathlib import Path

import pytest
from evidence_lane_plugin.ai_toolchain import resolve_lane_toolchain
from evidence_lane_plugin.code_toolchain import extract_tree_sitter_facts
from evidence_lane_plugin.data_toolchain import (
    DataInspectionRequest,
    inspect_excel_openpyxl,
    inspect_sqlalchemy_sqlite,
    inspect_tableau_hyper,
    inspect_tabular_pandas,
)
from evidence_lane_plugin.entity_reconciliation import reconcile_entity_candidates
from evidence_lane_plugin.graph_pipeline import SemanticGraph
from evidence_lane_plugin.hybrid_retrieval import (
    RetrievalCandidate,
    VectorRetrievalRequest,
    chroma_vector_rank,
    faiss_vector_rank,
    langchain_retrieval_pipeline,
    rank_bm25_candidates,
)
from evidence_lane_plugin.semantic_retrieval import (
    VectorModelContract,
    embed_with_local_sentence_transformer,
    ensure_authority_vector_schema,
    query_authority_vectors,
    replace_authority_vectors,
)
from evidence_lane_plugin.sqlite_execution import verify_and_optimize_sqlite_authority
from evidence_lane_plugin.tabular_toolchain import (
    DuckDBStageRequest,
    stage_result_to_lane_payloads,
    stage_tabular_source,
    stage_tabular_source_polars,
)
from pydantic import ValidationError


def test_lane_toolchain_is_codex_only_and_conditional() -> None:
    receipt = resolve_lane_toolchain(
        lane_id="data_excel",
        host_profile="CODEX_CLI",
        available_tools={"DuckDB", "APSW_SQLite_engine", "pandas"},
    )
    assert receipt["status"] == "PASS"
    assert receipt["plane"] == "CODEX"
    assert receipt["host_profile"] == "CODEX_CLI"
    assert receipt["conditional_execution"] is True
    assert receipt["run_every_tool"] is False
    assert receipt["chatgpt_plane_mixed"] is False
    assert receipt["runnable_tools"][:3] == [
        "DuckDB",
        "APSW_SQLite_engine",
        "pandas",
    ]
    with pytest.raises(ValueError, match="CHATGPT_TOOLCHAIN_PLANE_NOT_IMPLEMENTED"):
        resolve_lane_toolchain(
            lane_id="data_excel",
            host_profile="CHATGPT",
        )


def test_duckdb_request_rejects_non_codex_host(tmp_path: Path) -> None:
    source = tmp_path / "sample.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        DuckDBStageRequest(
            source_path=source,
            lane_id="data_excel",
            host_profile="CHATGPT",  # type: ignore[arg-type]
        )


def test_duckdb_stage_executes_and_projects_to_sqlite_payloads(
    tmp_path: Path,
) -> None:
    pytest.importorskip("duckdb")
    source = tmp_path / "sample.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "value"])
        writer.writerow(["alpha", 1])
        writer.writerow(["beta", 2])
    receipt = stage_tabular_source(
        DuckDBStageRequest(
            source_path=source,
            lane_id="data_excel",
            host_profile="CODEX_DESKTOP",
        )
    )
    assert receipt.engine == "DUCKDB_IN_MEMORY"
    assert receipt.total_rows == 2
    assert receipt.columns == ["name", "value"]
    assert receipt.duckdb_persisted_as_authority is False
    documents, facts = stage_result_to_lane_payloads(receipt)
    assert documents
    assert facts[0]["kind"] == "duckdb_tabular_stage_receipt"
    assert facts[1]["payload"]["durable_authority"] == "OWNING_LANE_SQLITE"


def test_sqlite_execution_receipt_preserves_sqlite_authority(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "authority.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE VIRTUAL TABLE facts_fts USING fts5(text)")
    connection.execute("INSERT INTO facts_fts(text) VALUES('evidence lane')")
    connection.commit()
    connection.close()
    receipt = verify_and_optimize_sqlite_authority(database)
    assert receipt.status == "PASS"
    assert receipt.integrity_check == "ok"
    assert receipt.durable_authority is True
    assert receipt.explicit_bulk_transaction_required is True
    assert receipt.sqlite_fts5_available is True


def test_polars_lazy_stage_executes_without_becoming_authority(tmp_path: Path) -> None:
    pytest.importorskip("polars")
    source = tmp_path / "sample.jsonl"
    source.write_text('{"name":"alpha","value":1}\n{"name":"beta","value":2}\n', encoding="utf-8")
    receipt = stage_tabular_source_polars(
        DuckDBStageRequest(
            source_path=source,
            lane_id="data_excel",
            host_profile="CODEX_VM",
        )
    )
    assert receipt.engine == "POLARS_LAZY"
    assert receipt.total_rows == 2
    assert receipt.polars_persisted_as_authority is False
    _, facts = stage_result_to_lane_payloads(receipt)
    assert {row["kind"] for row in facts} >= {
        "polars_tabular_stage_receipt",
        "json_structure",
        "json_record_sample",
    }


def test_excel_pandas_and_sqlalchemy_adapters_execute(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    pytest.importorskip("pandas")
    workbook_path = tmp_path / "book.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"] = "name"
    sheet["B1"] = "value"
    sheet["A2"] = "alpha"
    sheet["B2"] = "=1+1"
    workbook.save(workbook_path)
    request = DataInspectionRequest(
        source_path=workbook_path,
        host_profile="CODEX_DESKTOP",
    )
    openpyxl_receipt = inspect_excel_openpyxl(request)
    pandas_receipt = inspect_tabular_pandas(request)
    assert openpyxl_receipt["sheets"][0]["bounded_formula_cells"] == 1
    assert pandas_receipt["projections"][0]["bounded_rows"] == 1

    database = tmp_path / "source.sqlite"
    import sqlite3

    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE item(id INTEGER PRIMARY KEY,name TEXT NOT NULL)")
    connection.commit()
    connection.close()
    sqlalchemy_receipt = inspect_sqlalchemy_sqlite(
        DataInspectionRequest(
            source_path=database,
            host_profile="CODEX_CLI",
        )
    )
    assert sqlalchemy_receipt["read_only_uri"] is True
    assert sqlalchemy_receipt["tables"][0]["name"] == "item"


def test_tableau_hyper_adapter_executes_read_only(tmp_path: Path) -> None:
    pytest.importorskip("tableauhyperapi")
    from tableauhyperapi import (
        Connection,
        CreateMode,
        HyperProcess,
        SqlType,
        TableDefinition,
        TableName,
        Telemetry,
    )

    source = tmp_path / "sample.hyper"
    table_name = TableName("Extract", "Data")
    with (
        HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as process,
        Connection(
            process.endpoint,
            str(source),
            create_mode=CreateMode.CREATE_AND_REPLACE,
        ) as connection,
    ):
        connection.catalog.create_schema("Extract")
        connection.catalog.create_table(
            TableDefinition(
                table_name,
                [
                    TableDefinition.Column("name", SqlType.text()),
                    TableDefinition.Column("value", SqlType.big_int()),
                ],
            )
        )
        connection.execute_command(
            'INSERT INTO "Extract"."Data" VALUES (\'alpha\',1),(\'beta\',2)'
        )
    receipt = inspect_tableau_hyper(
        DataInspectionRequest(
            source_path=source,
            host_profile="CODEX_DESKTOP",
        )
    )
    assert receipt["status"] == "PASS"
    assert receipt["tables"][0]["row_count"] == 2
    assert receipt["telemetry_enabled"] is False


def test_rank_bm25_is_parity_not_authority() -> None:
    receipt = rank_bm25_candidates(
        "state travel candidate",
        [
            RetrievalCandidate(candidate_id="a", text="state travel preserves candidate"),
            RetrievalCandidate(candidate_id="b", text="unrelated workbook cell"),
        ],
    )
    assert receipt["status"] == "PASS"
    assert receipt["results"][0]["candidate_id"] == "a"
    assert receipt["sqlite_fts5_bm25_replaced"] is False


def test_tree_sitter_multilanguage_extraction_executes() -> None:
    pytest.importorskip("tree_sitter_language_pack")
    receipt = extract_tree_sitter_facts(
        "app.js",
        "import x from 'pkg';\nfunction run(value) { return x(value); }\n",
    )
    if receipt.status == "RUNTIME_NOT_PREWARMED":
        assert receipt.language in {"javascript", "js"}
        assert receipt.offline_only is True
        assert receipt.auto_download_used is False
        pytest.skip("hidden prewarmed tree-sitter grammar root not supplied")
    assert receipt.status == "PASS"
    assert receipt.language in {"javascript", "js"}
    assert receipt.symbols
    assert receipt.imports
    assert receipt.calls
    assert receipt.offline_only is True
    assert receipt.auto_download_used is False


def test_rustworkx_graph_analysis_executes() -> None:
    pytest.importorskip("rustworkx")
    graph = SemanticGraph("toolchain_graph")
    graph.add_node("A", "A")
    graph.add_node("B", "B")
    graph.add_edge("A", "B")
    _, _, receipt = graph.render_pair()
    assert receipt["graph_analysis"]["status"] == "PASS"
    assert receipt["graph_analysis"]["weak_component_count"] == 1
    assert receipt["graph_analysis"]["is_directed_acyclic"] is True


def test_faiss_and_sqlite_vec_keep_sqlite_ids_canonical() -> None:
    pytest.importorskip("faiss")
    pytest.importorskip("sqlite_vec")
    request = VectorRetrievalRequest(
        candidate_ids=["node-a", "node-b"],
        vectors=[[1.0, 0.0], [0.0, 1.0]],
        query_vector=[0.9, 0.1],
        limit=2,
    )
    faiss_receipt = faiss_vector_rank(request)
    assert faiss_receipt["results"][0]["candidate_id"] == "node-a"
    assert faiss_receipt["persistent_authority"] is False

    if importlib.util.find_spec("chromadb") is not None:
        chroma_receipt = chroma_vector_rank(request)
        assert chroma_receipt["results"][0]["candidate_id"] == "node-a"
        assert chroma_receipt["persistent_authority"] is False

    import sqlite3

    connection = sqlite3.connect(":memory:")
    contract = VectorModelContract(model_id="synthetic-test", dimension=2)
    schema = ensure_authority_vector_schema(connection, contract=contract)
    assert schema["fts5_bm25_mandatory"] is True
    replace_authority_vectors(
        connection,
        contract=contract,
        node_vectors=[("node-a", [1.0, 0.0]), ("node-b", [0.0, 1.0])],
    )
    rows = query_authority_vectors(connection, vector=[0.9, 0.1], limit=2)
    connection.close()
    assert rows[0]["node_id"] == "node-a"


def test_langchain_pipeline_is_governed() -> None:
    result = langchain_retrieval_pipeline().invoke({"query": "candidate"})
    assert result["governed"] is True
    assert result["sqlite_authority_required"] is True


def test_sentence_transformer_uses_only_revision_pinned_local_model() -> None:
    pytest.importorskip("sentence_transformers")
    configured = os.environ.get("EVIDENCE_LANE_TEST_EMBEDDING_MODEL", "")
    if not configured:
        pytest.skip("local embedding-model validation root not supplied")
    contract = VectorModelContract(
        model_id="BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        dimension=384,
        local_model_path=Path(configured),
        allow_network_download=False,
    )
    vectors = embed_with_local_sentence_transformer(
        ["Evidence Lane", "SQLite authority"],
        contract=contract,
    )
    assert len(vectors) == 2
    assert all(len(vector) == 384 for vector in vectors)


def test_entity_reconciliation_never_auto_merges() -> None:
    receipt = reconcile_entity_candidates(
        ["project_authority", "project-authority", "canon"],
        threshold=70,
    )
    assert receipt["status"] == "PASS"
    assert receipt["auto_merge_allowed"] is False
    assert receipt["authority_identity_mutated"] is False
    assert receipt["matches"]
    assert all(row["auto_merged"] is False for row in receipt["matches"])

"""Codex-only DuckDB staging for bounded tabular lane extraction.

DuckDB is an analytical execution engine, never a durable Evidence Lane
authority.  Every result produced here is validated with Pydantic and is then
persisted by the owning lane into its SQLite authority.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file

CODEX_HOST_PROFILES = frozenset({"CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"})
DUCKDB_EXTENSIONS = frozenset({".csv", ".tsv", ".parquet"})
POLARS_EXTENSIONS = frozenset({".csv", ".tsv", ".parquet", ".jsonl", ".ndjson"})


class DuckDBStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: Path
    lane_id: str
    host_profile: Literal["CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"]
    max_rows: int = Field(default=200, ge=1, le=2_000)


class DuckDBStageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: Literal["evidence-lane.duckdb-tabular-stage.v1"]
    status: Literal["PASS"]
    engine: Literal["DUCKDB_IN_MEMORY"]
    engine_version: str
    plane: Literal["CODEX"]
    host_profile: Literal["CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"]
    lane_id: str
    source_path: str
    source_sha256: str
    source_size_bytes: int
    source_format: Literal["CSV", "TSV", "PARQUET"]
    columns: list[str]
    column_types: list[str]
    bounded_rows: list[list[Any]]
    bounded_row_count: int
    total_rows: int
    durable_authority: Literal["OWNING_LANE_SQLITE"]
    duckdb_persisted_as_authority: Literal[False]
    chatgpt_plane_mixed: Literal[False]
    receipt_sha256: str


class PolarsStageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: Literal["evidence-lane.polars-tabular-stage.v1"]
    status: Literal["PASS"]
    engine: Literal["POLARS_LAZY"]
    engine_version: str
    plane: Literal["CODEX"]
    host_profile: Literal["CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"]
    lane_id: str
    source_path: str
    source_sha256: str
    source_size_bytes: int
    source_format: Literal["CSV", "TSV", "PARQUET", "NDJSON"]
    columns: list[str]
    column_types: list[str]
    bounded_rows: list[list[Any]]
    bounded_row_count: int
    total_rows: int
    lazy_plan: str
    durable_authority: Literal["OWNING_LANE_SQLITE"]
    polars_persisted_as_authority: Literal[False]
    chatgpt_plane_mixed: Literal[False]
    receipt_sha256: str


def duckdb_available() -> bool:
    return importlib.util.find_spec("duckdb") is not None


def polars_available() -> bool:
    return importlib.util.find_spec("polars") is not None


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_sha256": sha256_bytes(value), "bytes_length": len(value)}
    return str(value)


def stage_tabular_source(request: DuckDBStageRequest) -> DuckDBStageResult:
    """Run one bounded in-memory DuckDB stage for an applicable Codex lane."""

    path = request.source_path.resolve(strict=True)
    suffix = path.suffix.lower()
    if suffix not in DUCKDB_EXTENSIONS:
        raise ValueError(f"DUCKDB_SOURCE_FORMAT_UNSUPPORTED:{suffix}")
    if request.host_profile not in CODEX_HOST_PROFILES:
        raise ValueError("DUCKDB_CODEX_HOST_PROFILE_REQUIRED")
    if not duckdb_available():
        raise RuntimeError("DUCKDB_DEPENDENCY_UNAVAILABLE")

    import duckdb  # type: ignore[import-not-found]

    connection = duckdb.connect(database=":memory:")
    try:
        if suffix == ".parquet":
            relation = connection.read_parquet(str(path))
            source_format = "PARQUET"
        else:
            relation = connection.read_csv(
                str(path),
                auto_detect=True,
                header=True,
                delimiter="\t" if suffix == ".tsv" else ",",
                sample_size=1_048_576,
            )
            source_format = "TSV" if suffix == ".tsv" else "CSV"
        columns = [str(value) for value in relation.columns]
        column_types = [str(value) for value in relation.types]
        total_rows = int(relation.aggregate("count(*) AS row_count").fetchone()[0])
        rows = [
            [_safe_value(value) for value in row]
            for row in relation.limit(request.max_rows).fetchall()
        ]
    finally:
        connection.close()

    core = {
        "schema_name": "evidence-lane.duckdb-tabular-stage.v1",
        "status": "PASS",
        "engine": "DUCKDB_IN_MEMORY",
        "engine_version": str(duckdb.__version__),
        "plane": "CODEX",
        "host_profile": request.host_profile,
        "lane_id": request.lane_id,
        "source_path": str(path),
        "source_sha256": sha256_file(path),
        "source_size_bytes": path.stat().st_size,
        "source_format": source_format,
        "columns": columns,
        "column_types": column_types,
        "bounded_rows": rows,
        "bounded_row_count": len(rows),
        "total_rows": total_rows,
        "durable_authority": "OWNING_LANE_SQLITE",
        "duckdb_persisted_as_authority": False,
        "chatgpt_plane_mixed": False,
    }
    return DuckDBStageResult.model_validate(
        {
            **core,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(core)),
        }
    )


def stage_tabular_source_polars(request: DuckDBStageRequest) -> PolarsStageResult:
    """Run a bounded Polars lazy stage when its lane/source policy selects it."""

    path = request.source_path.resolve(strict=True)
    suffix = path.suffix.lower()
    if suffix not in POLARS_EXTENSIONS:
        raise ValueError(f"POLARS_SOURCE_FORMAT_UNSUPPORTED:{suffix}")
    if request.host_profile not in CODEX_HOST_PROFILES:
        raise ValueError("POLARS_CODEX_HOST_PROFILE_REQUIRED")
    if not polars_available():
        raise RuntimeError("POLARS_DEPENDENCY_UNAVAILABLE")

    import polars as pl  # type: ignore[import-not-found]

    if suffix == ".parquet":
        lazy = pl.scan_parquet(path)
        source_format = "PARQUET"
    elif suffix in {".jsonl", ".ndjson"}:
        lazy = pl.scan_ndjson(path)
        source_format = "NDJSON"
    else:
        lazy = pl.scan_csv(path, separator="\t" if suffix == ".tsv" else ",")
        source_format = "TSV" if suffix == ".tsv" else "CSV"
    schema = lazy.collect_schema()
    total_rows = int(lazy.select(pl.len().alias("row_count")).collect().item())
    frame = lazy.limit(request.max_rows).collect()
    rows = [
        [_safe_value(value) for value in row]
        for row in frame.iter_rows()
    ]
    core = {
        "schema_name": "evidence-lane.polars-tabular-stage.v1",
        "status": "PASS",
        "engine": "POLARS_LAZY",
        "engine_version": str(pl.__version__),
        "plane": "CODEX",
        "host_profile": request.host_profile,
        "lane_id": request.lane_id,
        "source_path": str(path),
        "source_sha256": sha256_file(path),
        "source_size_bytes": path.stat().st_size,
        "source_format": source_format,
        "columns": [str(name) for name in schema.names()],
        "column_types": [str(value) for value in schema.dtypes()],
        "bounded_rows": rows,
        "bounded_row_count": len(rows),
        "total_rows": total_rows,
        "lazy_plan": lazy.explain(optimized=True),
        "durable_authority": "OWNING_LANE_SQLITE",
        "polars_persisted_as_authority": False,
        "chatgpt_plane_mixed": False,
    }
    return PolarsStageResult.model_validate(
        {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
    )


def stage_result_to_lane_payloads(
    result: DuckDBStageResult | PolarsStageResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project a validated stage into documents/facts for SQLite persistence."""

    rows = result.bounded_rows
    normalized = "\n".join(
        " | ".join("" if value is None else str(value) for value in row)
        for row in rows
    )
    documents = (
        [
            {
                "locator": (
                    "duckdb:bounded-tabular-stage"
                    if result.engine == "DUCKDB_IN_MEMORY"
                    else "polars:bounded-tabular-stage"
                ),
                "text": normalized,
                "metadata": {
                    "engine": result.engine,
                    "bounded_rows": result.bounded_row_count,
                    "total_rows": result.total_rows,
                    "receipt_sha256": result.receipt_sha256,
                },
            }
        ]
        if normalized
        else []
    )
    facts: list[dict[str, Any]] = [
        {
            "kind": (
                "duckdb_tabular_stage_receipt"
                if result.engine == "DUCKDB_IN_MEMORY"
                else "polars_tabular_stage_receipt"
            ),
            "locator": (
                "duckdb:stage"
                if result.engine == "DUCKDB_IN_MEMORY"
                else "polars:stage"
            ),
            "payload": result.model_dump(mode="json"),
        },
        {
            "kind": "tabular_structure",
            "locator": "table",
            "payload": {
                "engine": result.engine,
                "columns": result.columns,
                "column_types": result.column_types,
                "rows_total": result.total_rows,
                "rows_indexed": result.bounded_row_count,
                "analytical_stage_is_durable_authority": False,
                "durable_authority": result.durable_authority,
            },
        },
    ]
    facts.extend(
        {
            "kind": "duckdb_row_sample",
            "locator": f"row:{index}",
            "payload": {
                "values": row,
                "column_count": len(row),
                "columns": result.columns,
            },
        }
        for index, row in enumerate(rows, start=1)
    )
    if result.source_format in {"CSV", "TSV"}:
        facts.append(
            {
                "kind": "csv_header",
                "locator": "row:1",
                "payload": {
                    "columns": result.columns,
                    "column_count": len(result.columns),
                    "delimiter": "\\t" if result.source_format == "TSV" else ",",
                    "engine": result.engine,
                },
            }
        )
        facts.extend(
            {
                "kind": "csv_row_sample",
                "locator": f"row:{index + 1}",
                "payload": {
                    "values": row,
                    "column_count": len(row),
                    "engine": result.engine,
                },
            }
            for index, row in enumerate(rows[:200], start=1)
        )
    elif result.source_format == "PARQUET":
        facts.append(
            {
                "kind": "parquet_schema",
                "locator": "file",
                "payload": {
                    "columns": result.columns,
                    "column_types": result.column_types,
                    "rows": result.total_rows,
                    "engine": result.engine,
                },
            }
        )
        facts.extend(
            {
                "kind": "parquet_row_sample",
                "locator": f"row:{index}",
                "payload": {
                    "values": row,
                    "columns": result.columns,
                    "engine": result.engine,
                },
            }
            for index, row in enumerate(rows[:200], start=1)
        )
    elif result.source_format == "NDJSON":
        facts.append(
            {
                "kind": "json_structure",
                "locator": "$",
                "payload": {
                    "type": "ndjson",
                    "rows": result.total_rows,
                    "columns": result.columns,
                    "engine": result.engine,
                },
            }
        )
        facts.extend(
            {
                "kind": "json_record_sample",
                "locator": f"$[{index - 1}]",
                "payload": {
                    "values": row,
                    "columns": result.columns,
                    "engine": result.engine,
                },
            }
            for index, row in enumerate(rows[:200], start=1)
        )
    return documents, facts


__all__ = [
    "CODEX_HOST_PROFILES",
    "DUCKDB_EXTENSIONS",
    "DuckDBStageRequest",
    "DuckDBStageResult",
    "POLARS_EXTENSIONS",
    "PolarsStageResult",
    "duckdb_available",
    "polars_available",
    "stage_result_to_lane_payloads",
    "stage_tabular_source",
    "stage_tabular_source_polars",
]

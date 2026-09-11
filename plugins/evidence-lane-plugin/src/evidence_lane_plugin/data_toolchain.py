"""Executable Excel, DataFrame, SQLAlchemy, and Tableau Hyper adapters."""

from __future__ import annotations

import importlib.util
import sqlite3
import warnings
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes


class DataInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: Path
    host_profile: str
    max_rows: int = Field(default=200, ge=1, le=2_000)
    max_columns: int = Field(default=64, ge=1, le=256)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=8_388_608)
    expected_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


def _codex_host(value: str) -> str:
    exact = value.strip().upper()
    if exact not in {"CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"}:
        raise ValueError("DATA_TOOLCHAIN_CODEX_HOST_PROFILE_REQUIRED")
    return exact


def _bounded_tabular_inspection(request: DataInspectionRequest, engine: str) -> dict[str, Any]:
    from .storage import reject_links
    from .tabular_inspection import inspect_bytes
    host = _codex_host(request.host_profile)
    path = request.source_path.absolute()
    reject_links(path, Path(path.anchor))
    with path.open('rb') as stream:
        content = stream.read(request.max_file_bytes + 1)
    if len(content) > request.max_file_bytes:
        raise ValueError('DATA_SOURCE_BYTE_BUDGET')
    source_sha = sha256_bytes(content).lower()
    if request.expected_sha256 is not None and source_sha != request.expected_sha256:
        raise ValueError('DATA_SOURCE_HASH_MISMATCH')
    lane = 'data_excel' if path.suffix.lower() in {'.xlsx', '.xlsm', '.xltx', '.xltm', '.xls', '.xlsb', '.ods'} else 'data'
    if engine == 'openpyxl' and path.suffix.lower() not in {'.xlsx', '.xlsm', '.xltx', '.xltm'}:
        raise ValueError('OPENPYXL_WORKBOOK_FORMAT_REQUIRED')
    result = inspect_bytes(engine, lane, path.name, content, max_rows=request.max_rows, max_columns=request.max_columns)
    with path.open('rb') as stream:
        observed = stream.read(request.max_file_bytes + 1)
    if observed != content:
        raise ValueError('DATA_SOURCE_CHANGED_DURING_INSPECTION')
    return {**result, 'host_profile': host, 'host_profile_basis': 'configured_not_attested',
        'source_hash_verified_before_and_after': True, 'read_only': True}


def inspect_excel_openpyxl(request: DataInspectionRequest) -> dict[str, Any]:
    return _bounded_tabular_inspection(request, 'openpyxl')


def inspect_tabular_pandas(request: DataInspectionRequest) -> dict[str, Any]:
    return _bounded_tabular_inspection(request, 'pandas')


def inspect_sqlalchemy_sqlite(
    request: DataInspectionRequest,
) -> dict[str, Any]:
    from .sqlite_execution import private_sqlite_source

    path = request.source_path.absolute()
    host = _codex_host(request.host_profile)
    if path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        raise ValueError("SQLALCHEMY_SQLITE_SOURCE_REQUIRED")
    with private_sqlite_source(path, max_bytes=request.max_file_bytes,
                              expected_sha256=request.expected_sha256) as (image, identity):
        result = _inspect_sqlalchemy_image(image, max_columns=request.max_columns)
    core = {**result, **identity, 'host_profile': host, 'host_profile_basis': 'configured_not_attested',
        'row_data_read': False, 'max_rows_applicable': False, 'source_mutated': False}
    return {**core, 'receipt_sha256': sha256_bytes(canonical_json_bytes(core))}


def inspect_sqlalchemy_sqlite_bytes(content: bytes, *, max_columns: int = 256) -> dict[str, Any]:
    """Current lane-worker entrypoint: inspect the already admitted byte image."""
    from .sqlite_execution import private_sqlite_image

    if type(max_columns) is not int or not 1 <= max_columns <= 256:
        raise ValueError('SQLALCHEMY_COLUMN_BUDGET_INVALID')
    # A raw byte payload has no sidecar grant or associated WAL identity.
    if len(content) < 100 or content[18:20] != b'\x01\x01':
        raise ValueError('SQLALCHEMY_COMPLETE_BYTE_IMAGE_REQUIRED')
    with private_sqlite_image(content) as image:
        result = _inspect_sqlalchemy_image(image, max_columns=max_columns)
    core = {**result, 'source_sha256': sha256_bytes(content).lower(),
        'source_basis': 'exact_admitted_byte_image', 'row_data_read': False,
        'source_mutated': False}
    return {**core, 'receipt_sha256': sha256_bytes(canonical_json_bytes(core))}


def _inspect_sqlalchemy_image(path: Path, *, max_columns: int) -> dict[str, Any]:
    from .sqlite_execution import SQLiteInspectionBudget

    if importlib.util.find_spec("sqlalchemy") is None:
        raise RuntimeError("SQLALCHEMY_DEPENDENCY_UNAVAILABLE")
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.exc import SAWarning

    budget = SQLiteInspectionBudget()
    def read_only_connection():
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            budget.configure(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    engine = create_engine('sqlite+pysqlite://', creator=read_only_connection)
    try:
        # Hold one connection for preflight and reflection. Inspect(engine)
        # otherwise opens separate read transactions for individual operations.
        with engine.connect() as connection, warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter('always', SAWarning)
            connection.exec_driver_sql('BEGIN')
            schema = connection.exec_driver_sql("SELECT type,name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchmany(513)
            if len(schema) > 512:
                raise ValueError('SQLALCHEMY_SCHEMA_BUDGET')
            names = [str(row[1]) for row in schema if row[0] == 'table']
            if len(names) > 128:
                raise ValueError('SQLALCHEMY_TABLE_BUDGET')
            relationship_count = 0
            for name in names:
                budget.check()
                quoted = '"' + name.replace('"', '""') + '"'
                columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quoted})').fetchmany(max_columns + 1)
                if len(columns) > max_columns:
                    raise ValueError('SQLALCHEMY_COLUMN_BUDGET')
                for pragma in ('foreign_key_list', 'index_list'):
                    rows = connection.exec_driver_sql(f'PRAGMA {pragma}({quoted})').fetchmany(4097 - relationship_count)
                    relationship_count += len(rows)
                    if relationship_count > 4096:
                        raise ValueError('SQLALCHEMY_RELATIONSHIP_BUDGET')
            inspector = inspect(connection)
            tables = []
            for name in sorted(inspector.get_table_names()):
                budget.check()
                tables.append({'name': name, 'columns': [
                    {'name': str(row['name']), 'type': str(row['type']), 'nullable': bool(row.get('nullable', True))}
                    for row in inspector.get_columns(name)],
                    'foreign_keys': inspector.get_foreign_keys(name), 'indexes': inspector.get_indexes(name)})
            views = sorted(inspector.get_view_names())
            warning_count = len(observed)
            budget.check()
    except Exception:
        budget.check()
        raise
    finally:
        engine.dispose()
    core = {
        "schema": "evidence-lane.sqlalchemy-inspection.v4",
        "status": "PASS",
        "engine": "SQLAlchemy",
        "tables": tables,
        "views": views,
        "read_only_uri": True,
        "connection_basis": "private_read_only_copy_single_transaction",
        "reflection_warning_count": warning_count,
        "reflection_complete": warning_count == 0,
        "limits": {'max_schema_objects': 512, 'max_tables': 128, 'max_columns': max_columns,
            'max_relationships': 4096, 'max_vm_steps': budget.max_vm_steps, 'timeout_seconds': budget.timeout_seconds},
    }
    if len(canonical_json_bytes(core)) > 2_097_152:
        raise ValueError('SQLALCHEMY_OUTPUT_BUDGET')
    return core


def inspect_tableau_hyper(request: DataInspectionRequest) -> dict[str, Any]:
    """Retained direct adapter, bound to the same v4 private-copy native worker."""
    from .storage import reject_links
    from .tableau_hyper import inspect_hyper
    host = _codex_host(request.host_profile)
    path = request.source_path.absolute()
    reject_links(path, Path(path.anchor))
    if path.suffix.lower() != '.hyper':
        raise ValueError('TABLEAU_HYPER_SOURCE_REQUIRED')
    if request.max_rows > 1000:
        raise ValueError('TABLEAU_HYPER_ROW_BUDGET')
    with path.open('rb') as stream:
        content = stream.read(request.max_file_bytes + 1)
    if len(content) > request.max_file_bytes:
        raise ValueError('TABLEAU_HYPER_FILE_BUDGET')
    source_sha = sha256_bytes(content).lower()
    if request.expected_sha256 is not None and source_sha != request.expected_sha256:
        raise ValueError('DATA_SOURCE_HASH_MISMATCH')
    files, evidence = inspect_hyper([(path.name, content)], max_rows_per_table=request.max_rows)
    collections = files[0]['collections']
    if any(len(row['columns']) > request.max_columns for row in collections['hyper_table']):
        raise ValueError('TABLEAU_HYPER_COLUMN_BUDGET')
    reject_links(path, Path(path.anchor))
    with path.open('rb') as stream:
        if stream.read(request.max_file_bytes + 1) != content:
            raise ValueError('DATA_SOURCE_CHANGED_DURING_INSPECTION')
    core = {'schema': 'evidence-lane.tableau-hyper-inspection.v4', 'status': 'PASS',
        'engine': 'tableauhyperapi', 'host_profile': host, 'host_profile_basis': 'configured_not_attested',
        'source_sha256': source_sha, 'tables': collections['hyper_table'],
        'typed_samples': collections['hyper_row'], 'schemas': collections['hyper_schema'],
        'source_hash_verified_before_and_after': True, 'telemetry_enabled': False,
        'source_mutated': False, 'native_evidence': evidence}
    return {**core, 'receipt_sha256': sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "DataInspectionRequest",
    "inspect_excel_openpyxl",
    "inspect_sqlalchemy_sqlite",
    "inspect_tableau_hyper",
    "inspect_tabular_pandas",
]

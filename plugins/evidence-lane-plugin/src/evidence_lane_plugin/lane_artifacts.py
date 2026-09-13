"""Direct, human-readable projections for one initialized project lane.

SQLite and the root-PV catalog remain authoritative.  These files make each
lane's current structure, pointer, tools, manifest, and migration history
visible at the lane root without generic ``files`` or ``schema`` wrappers.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .storage import STORAGE_LAYOUT, json_text, reject_links

DIRECT_LANE_ARTIFACTS = (
    "authority.ref.json",
    "lane_manifest.json",
    "lane_pointer.json",
    "tools.json",
    "schema-history.v4.json",
    "refresh_receipt.json",
)


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _atomic(path: Path, content: bytes, root: Path) -> None:
    reject_links(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".projection-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _json(value: object) -> bytes:
    return (json_text(value) + "\n").encode("utf-8")


def _schema(lane) -> tuple[list[dict], list[dict], list[dict]]:
    with lane.connection(read_only=True) as connection:
        tables = []
        edges = []
        for row in connection.execute(
            "SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            columns = [dict(item) for item in connection.execute(f'PRAGMA table_info("{row[0]}")')]
            tables.append({"name": row[0], "columns": columns, "sql_sha256": hashlib.sha256((row[1] or "").encode()).hexdigest()})
            for foreign in connection.execute(f'PRAGMA foreign_key_list("{row[0]}")'):
                edges.append({"from_table": row[0], "from_column": foreign[3], "to_table": foreign[2], "to_column": foreign[4]})
        history = []
        present = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='schema_history_files'"
        ).fetchone()
        if present:
            for row in connection.execute("SELECT document_json FROM schema_history_files ORDER BY owner,version"):
                history.append(json.loads(row[0]))
    return tables, edges, history


def _mmd(lane_id: str, tables: list[dict], edges: list[dict]) -> bytes:
    lines = ["erDiagram"]
    for table in tables:
        lines.append(f"  {table['name']} {{")
        for column in table["columns"]:
            kind = str(column.get("type") or "TEXT").replace(" ", "_")
            marker = " PK" if column.get("pk") else ""
            lines.append(f"    {kind} {column['name']}{marker}")
        lines.append("  }")
    for edge in edges:
        lines.append(f"  {edge['from_table']} }}o--|| {edge['to_table']} : {edge['from_column']}")
    if not tables:
        lines.append(f"  {lane_id} {{")
        lines.append("    TEXT uninitialized")
        lines.append("  }")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _dot(lane_id: str, tables: list[dict], edges: list[dict]) -> bytes:
    lines = [f'digraph "{lane_id}" {{', '  rankdir="LR";', '  node [shape=record,fontname="Arial"];']
    for table in tables:
        columns = "|".join(str(column["name"]) for column in table["columns"])
        lines.append(f'  "{table["name"]}" [label="{{{table["name"]}|{columns}}}"];')
    for edge in edges:
        lines.append(f'  "{edge["from_table"]}" -> "{edge["to_table"]}" [label="{edge["from_column"]}"];')
    lines.append("}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def refresh_lane_artifacts(lane) -> dict:
    tables, edges, history = _schema(lane)
    database_sha256 = _digest(lane.database)
    catalog = next((row for row in lane.project.lane_catalog() if row["lane_id"] == lane.lane_id), None)
    root_pointer = "../active_pointer.json"
    schema_history = {
        "schema": "evidence-lane.schema-history.v4",
        "project_id": lane.project_id,
        "lane_id": lane.lane_id,
        "entries": history,
    }
    authority_ref = {
        "schema": "evidence-lane.lane-authority-reference.v4",
        "project_id": lane.project_id,
        "lane_id": lane.lane_id,
        "kind": lane.definition.kind,
        "database": lane.definition.database_relative_path,
        "root_pointer": root_pointer,
        "root_pointer_role": "resolve_current_root_pv_at_read_time",
    }
    tools = {
        "schema": "evidence-lane.lane-tools.v4",
        "project_id": lane.project_id,
        "lane_id": lane.lane_id,
        "operations": list(lane.definition.operations),
        "mutation_policy": lane.definition.mutation_policy,
        "source_types": list(lane.definition.source_types),
        "parser_id": lane.definition.parser_id,
    }
    mmd = _mmd(lane.lane_id, tables, edges)
    dot = _dot(lane.lane_id, tables, edges)
    base = {
        "authority.ref.json": _json(authority_ref),
        "tools.json": _json(tools),
        "schema-history.v4.json": _json(schema_history),
        lane.definition.mmd_filename: mmd,
        lane.definition.dot_filename: dot,
    }
    for name, content in base.items():
        _atomic(lane.folder / name, content, lane.root)
    member_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in base.items()}
    manifest = {
        "schema": "evidence-lane.lane-manifest.v4",
        "project_id": lane.project_id,
        "lane_id": lane.lane_id,
        "kind": lane.definition.kind,
        "storage_layout": STORAGE_LAYOUT,
        "database": lane.definition.database_relative_path,
        "database_sha256": database_sha256,
        "table_count": len(tables),
        "root_pointer": root_pointer,
        "published_lane_head": catalog,
        "members": member_hashes,
        "objects_directory": "objects" if lane.files.is_dir() else None,
    }
    manifest_bytes = _json(manifest)
    _atomic(lane.folder / "lane_manifest.json", manifest_bytes, lane.root)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    pointer = {
        "schema": "evidence-lane.lane-pointer.v4",
        "project_id": lane.project_id,
        "lane_id": lane.lane_id,
        "root_pointer": root_pointer,
        "root_pointer_role": "resolve_current_root_pv_at_read_time",
        "published_lane_head": catalog,
        "database_sha256": database_sha256,
        "manifest_sha256": manifest_sha256,
    }
    pointer_bytes = _json(pointer)
    _atomic(lane.folder / "lane_pointer.json", pointer_bytes, lane.root)
    receipt = {
        "schema": "evidence-lane.lane-artifact-refresh-receipt.v4",
        "project_id": lane.project_id,
        "lane_id": lane.lane_id,
        "database_sha256": database_sha256,
        "manifest_sha256": manifest_sha256,
        "pointer_sha256": hashlib.sha256(pointer_bytes).hexdigest(),
        "member_sha256": member_hashes,
        "generated_from_authoritative_sqlite": True,
    }
    receipt_bytes = _json(receipt)
    _atomic(lane.folder / "refresh_receipt.json", receipt_bytes, lane.root)
    return receipt


def refresh_project_artifacts(project) -> dict:
    catalog = project.lane_catalog()
    root_pv = project.pv_head()
    registration = {**project.registration, "project_id": project.project_id, "source_root": str(project.source_root), "state_root": str(project.root)}
    active_session = None
    initial_source_intake = None
    if any(row["lane_id"] == "receipts" for row in catalog):
        receipts = project.lane("receipts")
        with receipts.connection(read_only=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='receipts'").fetchone():
                row = connection.execute(
                    "SELECT body_json FROM receipts WHERE kind='project_initial_source_intake' ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                initial_source_intake = json.loads(row[0]) if row else None
    if any(row["lane_id"] == "sessions" for row in catalog):
        sessions = project.lane("sessions")
        with sessions.connection(read_only=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='sessions_current'").fetchone():
                row = connection.execute(
                    "SELECT r.* FROM sessions_current c JOIN sessions_records r USING(session_id) WHERE c.singleton=1"
                ).fetchone()
                active_session = dict(row) if row else None
    documents = {
        "project.json": {
            "schema": "evidence-lane.project.v4",
            **registration,
            "initial_source_lane_ids": (
                initial_source_intake.get("selected_lane_ids", []) if initial_source_intake else []
            ),
            "initial_source_intake_recorded": initial_source_intake is not None,
        },
        "project_authority.json": {
            "schema": "evidence-lane.project-root-authority.v4",
            "project_id": project.project_id,
            "resolved_project_root": str(project.root),
            "repository_path": str(project.source_root),
            "storage_layout": STORAGE_LAYOUT,
            "root_pv_head": root_pv,
            "ordered_lane_ids": [row["lane_id"] for row in catalog],
            "lane_catalog": catalog,
            "wrapper_directories": [],
            "initial_source_intake": initial_source_intake,
        },
        "active_pointer.json": {"schema": "evidence-lane.active-root-pv-pointer.v4", "project_id": project.project_id, **root_pv},
        "active_session.json": {"schema": "evidence-lane.active-session-pointer.v4", "project_id": project.project_id, "session": active_session},
        "capture_route.json": {
            "schema": "evidence-lane.capture-route.v4",
            "project_id": project.project_id,
            "capture_route": project.registration["capture_route"],
            "registration_digest": project.registration["registration_digest"],
            "initial_registration_intake_recorded": initial_source_intake is not None,
            "initial_registration_intake_owner_channel_required": True,
            "subsequent_ingestion_requires_selected_project": True,
        },
    }
    for name, document in documents.items():
        _atomic(project.root / name, _json(document), project.root)
    return {"project_id": project.project_id, "root_pv": root_pv, "files": sorted(documents)}


__all__ = ["DIRECT_LANE_ARTIFACTS", "refresh_lane_artifacts", "refresh_project_artifacts"]

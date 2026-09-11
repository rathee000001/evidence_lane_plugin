"""PowerBi versions in their own lane, with separate journaled file export."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue

from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations, read_compatibility
from .powerbi_contracts import (
    PowerBiEdit,
    PowerBiExport,
    PowerBiGenerate,
    PowerBiIndex,
    PowerBiQuery,
    PowerBiRead,
    PowerBiSchemaRead,
    PowerBiSelection,
)
from .powerbi_parsers import EXTENSIONS, digest, parse_powerbi
from .powerbi_schema import POWERBI_MIGRATIONS, POWERBI_TABLES
from .projects import ProjectAccess
from .registry import ActionSpec, Contract, FetchRoute, SearchRoute, SourceMaterialization
from .selector_schema import active_selector_sql
from .storage import bounded_project_read, json_text, now, project_snapshot, reject_links
from .tool_routes import ToolRoute


def _parser_contract():
    from . import (
        powerbi_authoring,
        powerbi_contracts,
        powerbi_json_schema,
        powerbi_native,
        powerbi_parsers,
        powerbi_process,
        powerbi_workers,
    )

    files = {
        Path(module.__file__).name: digest(Path(module.__file__).read_bytes())
        for module in (
            powerbi_authoring,
            powerbi_contracts,
            powerbi_native,
            powerbi_parsers,
            powerbi_workers,
            powerbi_json_schema,
            powerbi_process,
        )
    }
    files["microsoft_schema_manifest"] = digest(
        (powerbi_json_schema.ROOT / "manifest.json").read_bytes()
    )
    return digest(canonical_json_bytes(files))


def _chunks(facts):
    for item in facts["items"]:
        for ordinal, start in enumerate(range(0, len(item["text"]), 4096)):
            yield item["item_id"], ordinal, item["text"][start : start + 4096]


def _chunk_matches(lane, row, expected):
    if expected is None:
        return False
    raw = expected.encode("utf-8")
    if (
        row["registered_bytes"] != len(raw)
        or row["text_object"] != digest(raw)
        or row["text_content"] != expected
    ):
        return False
    # The joined object registration belongs to the already verified, pinned
    # lane connection. Rehash the addressed file without reopening and hashing
    # the entire lane database for every individual text chunk.
    return lane.read_object(row["text_object"], require_registered=False) == raw


def index_powerbi(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    relative = _relative(request.filename)
    if PurePosixPath(relative).suffix.lower() not in EXTENSIONS:
        raise LaneError(
            "POWERBI_FORMAT_UNSUPPORTED",
            "Select an exact Power BI project, model, report or package.",
        )
    names = [relative, *[_relative(name) for name in request.companion_files]]
    packaged = bool(request.companion_files)
    if packaged and PurePosixPath(relative).suffix.lower() not in {
        ".pbip",
        ".pbir",
        ".pbism",
        ".tmdl",
    }:
        raise LaneError(
            "POWERBI_COMPANIONS_UNSUPPORTED",
            "Explicit companion files apply only to folder-based Power BI projects.",
        )
    paths = []
    for name in names:
        path = execution.guard.path(name)
        ProjectAccess(store).authorize(context.client_id, "read", path=path)
        paths.append(str(path))
    powerbi_id = digest(canonical_json_bytes([store.project_id, "source", relative]))
    if current_snapshot(store, powerbi_id) != request.expected_snapshot:
        raise LaneError(
            "POWERBI_SNAPSHOT_CHANGED", "Refresh requires the exact current Power BI snapshot."
        )
    logical_name = relative + ".zip" if packaged else relative
    prior = read_snapshot(store, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior.get('source_route') is not None and execution.guard.source_route is None:
        raise LaneError('SOURCE_ROUTE_SELECTION_REQUIRED', 'Select the refreshed Sources route for this previously routed input.')
    content, parsed = _worker(
        execution,
        "powerbi_parse_file",
        {
            "filenames": paths,
            "names": names,
            "logical_name": logical_name,
            "entrypoint": relative if packaged else None,
            "max_file_bytes": request.max_file_bytes,
            "inspect_models": request.inspect_models,
            "max_rows_per_table": request.max_rows_per_table,
        },
    )
    source_documents = {
        name: _bytes(Path(path), request.max_file_bytes)
        for name, path in zip(names, paths, strict=True)
    }
    observed = [
        {"name": name, "sha256": digest(raw), "bytes": len(raw)}
        for name, raw in source_documents.items()
    ]
    if observed != parsed["evidence"]["source_files"]:
        raise LaneError(
            "POWERBI_SOURCE_CHANGED", "An admitted Power BI source changed during extraction."
        )
    return publish(
        context,
        powerbi_id=powerbi_id,
        logical_name=logical_name,
        source_path=relative,
        origin="source",
        previous=request.expected_snapshot,
        content=content,
        parsed=parsed,
        operation="powerbi_index",
        source_documents=source_documents, max_file_bytes=request.max_file_bytes,
        max_export_bytes=16_777_216 if packaged else request.max_file_bytes, intake=True,
    )


def edit_powerbi(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, facts = read_snapshot(store, request.snapshot_id)
    if (
        manifest["raw_object"] != request.expected_sha256
        or current_snapshot(store, manifest["powerbi_id"]) != request.snapshot_id
    ):
        raise LaneError("POWERBI_EDIT_SNAPSHOT_CHANGED", "Edit the exact current PowerBi bytes.")
    content, parsed = _worker(
        execution,
        "powerbi_edit",
        {
            "logical_name": manifest["logical_name"],
            "content_base64": base64.b64encode(
                store.lane("power_bi").read_object(manifest["raw_object"])
            ).decode("ascii"),
            "expected_sha256": request.expected_sha256,
            "parse_options": facts["parse_options"],
            "replacements": [row.model_dump(mode="json") for row in request.replacements],
        },
    )
    return publish(
        context,
        powerbi_id=manifest["powerbi_id"],
        logical_name=manifest["logical_name"],
        source_path=manifest["source_path"],
        origin=manifest["origin"],
        previous=request.snapshot_id,
        content=content,
        parsed=parsed,
        operation="powerbi_edit",
    )


def read_powerbi(store, request):
    manifest, _ = read_snapshot(store, request.snapshot_id)
    if request.representation == "original_member":
        if request.member not in manifest["source_members"]:
            raise LaneError(
                "POWERBI_ORIGINAL_MEMBER_MISSING",
                "Select an exact original admitted source member.",
            )
        selected = manifest["source_members"][request.member]
        raw = store.lane("power_bi").read_object(selected)
        name = request.member
    else:
        selected = (
            manifest["source_object"]
            if request.representation == "original_source"
            else manifest["raw_object"]
        )
        if selected is None:
            raise LaneError(
                "POWERBI_ORIGINAL_SOURCE_ABSENT",
                "This generated model has no separate original source.",
            )
        raw = store.lane("power_bi").read_object(selected)
        name = (
            manifest["source_path"]
            if request.representation == "original_source"
            else manifest["logical_name"]
        )
    if request.representation == "member":
        from .powerbi_parsers import package_members

        if PurePosixPath(name).suffix.lower() not in {".pbix", ".pbit", ".zip"}:
            raise LaneError(
                "POWERBI_MEMBER_REQUIRES_PACKAGE", "Select a packaged PowerBi snapshot."
            )
        members = package_members(raw)
        if request.member not in members:
            raise LaneError("POWERBI_MEMBER_MISSING", "Select an exact admitted package member.")
        raw, name = members[request.member], request.member
        selected = digest(raw)
    value = raw[request.offset : request.offset + request.max_bytes]
    end = request.offset + len(value)
    return {
        "snapshot_id": request.snapshot_id,
        "logical_name": name,
        "sha256": selected,
        "representation": request.representation,
        "total_bytes": len(raw),
        "offset": request.offset,
        "content_base64": base64.b64encode(value).decode("ascii"),
        "next_offset": end if end < len(raw) else None,
        "source_bytes_mutated": False,
    }


def register_powerbi_actions(engine):
    from .artifact_contract import SelectedViewRefresh

    def export_route(render):
        def applicable(context, request):
            from .artifact_contract import LaneArtifacts
            store = engine.directory.open(context.project_id)
            view = LaneArtifacts(engine, store).current_selection('power_bi.structure')
            return bool(view and view['formats']) is render
        return applicable

    def verify_exported(context, request, output):
        return verify_export(context, request, output, engine=engine)

    for action, workflow in (("powerbi_index", "source-intake"), ("powerbi_refresh", "refresh")):
        engine.registry.register(
            ActionSpec(
                action,
                "Snapshot explicitly admitted Power BI files, report metadata and native TOM model definitions in their separate lane.",
                PowerBiIndex,
                PowerBiResult,
                index_powerbi,
                permission="write",
                mutates=True,
                requires_delta=True,
                # Optional companions are checked individually before IO by index_powerbi
                # and again as the worker's nonempty declared filenames list.
                profile="power_bi",
                workflow=workflow,
                path_fields=("filename",),
                source_lanes=('power_bi',),
                materialization=SourceMaterialization() if action == 'powerbi_index' else None,
                worker_operations=("powerbi_parse_file",),
                verification_checks=("powerbi_snapshot_integrity", "powerbi_source_hash_unchanged"),
                verifier=verify_powerbi,
                tool_routes=(
                    ToolRoute(
                        action + ".native",
                        index_powerbi,
                        ("Python", "PowerBI_TOM", "PBIXRay", "JSONSchema"),
                        systems=("Windows",),
                        reason="Complete Power BI adapter prerequisites; input format selects the actual TOM/PBIX/schema calls recorded in native evidence.",
                    ),
                ),
            )
        )
    for action, model, handler, worker in (
        ("powerbi_generate", PowerBiGenerate, generate_powerbi, "powerbi_generate"),
        ("powerbi_edit", PowerBiEdit, edit_powerbi, "powerbi_edit"),
    ):
        engine.registry.register(
            ActionSpec(
                action,
                "Publish native BIM or schema-valid PBIP/PBIR packages, or exact model/report/resource member changes; rendering and DAX execution remain separate.",
                model,
                PowerBiResult,
                handler,
                permission="write",
                mutates=True,
                requires_delta=True,
                profile="power_bi",
                workflow="build",
                worker_operations=(worker,),
                verification_checks=("powerbi_snapshot_integrity",),
                verifier=verify_powerbi,
                tool_routes=(
                    ToolRoute(
                        action + ".native",
                        handler,
                        ("Python", "PowerBI_TOM", "JSONSchema"),
                        systems=("Windows",),
                    ),
                ),
            )
        )

    def query_handler(function, action):
        def handler(context, request):
            store = engine.directory.open(context.project_id)
            with project_snapshot(store.root):
                return result(store, action, function(store, request))

        return handler

    for action, model, function, description in (
        (
            "powerbi_schema",
            PowerBiSchemaRead,
            read_powerbi_schema,
            "Read the exact offline Microsoft schema catalog or one pinned schema; never fetch a supplied URL.",
        ),
        (
            "powerbi_current",
            PowerBiSelection,
            current_powerbis,
            "Read current PowerBi identities without refreshing sources.",
        ),
        (
            "powerbi_query",
            PowerBiQuery,
            query_powerbi,
            "Read bounded report and model metadata or literal FTS5/BM25 matches.",
        ),
        (
            "powerbi_read",
            PowerBiRead,
            read_powerbi,
            "Read an exact immutable PowerBi file or package member by byte range.",
        ),
    ):
        engine.registry.register(
            ActionSpec(
                action,
                description,
                model,
                PowerBiResult,
                query_handler(function, action),
                profile="power_bi",
                workflow="source-intake",
                queryable_in_delta=True,
                cross_project_read=True,
                studio_read=True,
                read_migrations=POWERBI_MIGRATIONS,
                search=SearchRoute(('power_bi',), 'rows', 'powerbi_current', 'powerbis', 'text', 'any', rerank_text='text') if action == 'powerbi_query' else None,
                fetch=FetchRoute(('power_bi',), 'power_bi') if action == 'powerbi_read' else None,
            )
        )
    engine.registry.register(
        ActionSpec(
            "powerbi_export",
            "Export an exact PowerBi version with a journaled effect and destination hash binding.",
            PowerBiExport,
            PowerBiResult,
            export_powerbi,
            permission="write",
            mutates=True,
            requires_delta=True,
            profile="power_bi",
            workflow="build",
            path_fields=("filename",),
            verification_checks=("powerbi_export_hash_verified",),
            verifier=verify_exported, worker_operations=('powerbi_parse_content', 'render_lane_view'),
            tool_routes=tuple(ToolRoute('powerbi_export.' + ('view' if render else 'native'), export_powerbi,
                (*('Python', 'PowerBI_TOM', 'PBIXRay', 'JSONSchema'), *(('LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx') if render else ())),
                view_refresh=SelectedViewRefresh(engine, 'power_bi.structure'), systems=('Windows',), applicable=export_route(render),
                worker_operations=('powerbi_parse_content', *(('render_lane_view',) if render else ())))
                for render in (False, True)),
        )
    )
    from .powerbi_views import register_powerbi_views

    register_powerbi_views(engine)


def read_powerbi_schema(store, request):
    from .powerbi_json_schema import schema_bundle

    _, documents, evidence = schema_bundle()
    if request.schema_uri is None:
        value = {
            "resources": [
                {key: row[key] for key in ("uri", "sha256", "bytes", "source_path")}
                for _, row in documents.values()
            ],
            "evidence": evidence,
        }
    else:
        if request.schema_uri not in documents:
            raise LaneError(
                "POWERBI_SCHEMA_UNSUPPORTED",
                "Select one exact URI from the bundled Power BI schema catalog.",
            )
        document, row = documents[request.schema_uri]
        value = {
            "document": document,
            "uri": row["uri"],
            "sha256": row["sha256"],
            "evidence": evidence,
        }
    if len(canonical_json_bytes(value)) > request.max_bytes:
        raise LaneError(
            "POWERBI_SCHEMA_OUTPUT_BUDGET", "Increase max_bytes for this complete schema resource."
        )
    return value


def format_contract():
    return {
        "schema": "evidence-lane.powerbi-formats.v4",
        "lane_id": "power_bi",
        "intake": [".pbip", ".pbir", ".pbism", ".tmdl", ".bim", ".pbix", ".pbit", ".zip"],
        "project_companions": "Explicit project-relative list only; never automatic reference traversal; preserved in a deterministic ZIP.",
        "native_generation": [
            ".bim",
            "Explicit PBIP/PBIR ZIP with UTF-8 definitions and optional binary resources",
        ],
        "native_editing": [
            "Exact BIM/TMDL and schema-defined project/report changes",
            "Hash-bound member add/replace/delete with unchanged unselected members",
            "Explicit StaticResources bytes; no rendering or execution",
        ],
        "native_export": "exact_snapshot_bytes; admitted folder sets export as ZIP and every member is separately readable",
        "tom_package": "Microsoft.AnalysisServices",
        "tom_version": "19.114.12",
        "report_editing": "Pinned Microsoft PBIR JSON schema versions; legacy report.json is read-only",
        "schema_bundle": "contracts/powerbi-schemas/manifest.json; no runtime network resolution",
        "pbix_pbit_writing": False,
        "pbix_to_pbip_conversion": False,
        "model_metadata": "TOM deserialization of complete TMDL groups and BIM/TMSL definitions",
        "compressed_model_rows": "PBIXRay 0.15.5 with separate Python 3.13.15, bounded stored row samples",
        "dax_execution": False,
        "report_rendering": False,
        "report_schema_validation": True,
        "live_connections_opened": False,
        "automatic_external_file_reads": False,
        "native_operation_platforms": ["Windows"],
        "budgets": {
            "max_file_bytes": 8_388_608,
            "max_explicit_zip_file_bytes": 16_777_216,
            "max_total_bytes": 16_777_216,
            "max_files": 256,
            "max_items": 50_000,
            "max_admitted_project_files": 128,
            "max_rows_per_table": 1000,
            "max_sample_cells": 100_000,
            "worker_output_bytes": 50_331_648,
            "native_process_memory_bytes": 1_073_741_824,
        },
        "natural_files": ["exact_original_format_or_admitted_project_ZIP"],
        "graph_files": ["powerbi.mmd", "powerbi.dot", "powerbi.pointer.json"],
    }


class PowerBiResult(Contract):
    project_id: str
    lane_id: Literal["power_bi"] = "power_bi"
    operation: str
    result: dict[str, JsonValue]


def result(store, operation, body):
    value = PowerBiResult(project_id=store.project_id, operation=operation, result=body)
    if len(canonical_json_bytes(value.model_dump(mode="json"))) > 2_097_152:
        raise LaneError("POWERBI_OUTPUT_BUDGET", "Select a smaller powerbi result.")
    return value


def _relative(value):
    from .powerbi_parsers import member_name

    return member_name(value)


def _bytes(path, limit=16_777_216):
    reject_links(path, Path(path.anchor))
    before = path.stat()
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    after = path.stat()
    if len(content) > limit:
        raise LaneError("POWERBI_FILE_BYTE_BUDGET", "The selected powerbi exceeds its byte budget.")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise LaneError(
            "POWERBI_SOURCE_CHANGED", "The selected Power BI file changed while it was being read."
        )
    return content


def _lane(store):
    try:
        return store.lane("power_bi")
    except LaneError as error:
        if error.code != "LANE_NOT_INITIALIZED":
            raise
        return None


def _calls(execution, remaining):
    execution.guard.check()
    if execution.guard.calls + remaining > execution.guard.task.budget.max_tool_calls:
        raise LaneError(
            "DELTA_TOOL_BUDGET",
            "The powerbi operation must leave room for its required verification.",
        )


def current_snapshot(store, powerbi_id):
    lane = _lane(store)
    if lane is None:
        return None
    read_compatibility(lane, POWERBI_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='powerbi_current'"
        ).fetchone():
            return None
        row = connection.execute(
            "SELECT snapshot_id FROM powerbi_current WHERE powerbi_id=?", (powerbi_id,)
        ).fetchone()
    return row[0] if row else None


def read_snapshot(store, snapshot_id):
    lane = _lane(store)
    if lane is None:
        raise LaneError("POWERBI_SNAPSHOT_MISSING", "Index or generate the selected powerbi first.")
    read_compatibility(lane, POWERBI_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='powerbi_version'"
        ).fetchone():
            raise LaneError(
                "POWERBI_SNAPSHOT_MISSING", "Index or generate the selected powerbi first."
            )
        row = connection.execute(
            "SELECT * FROM powerbi_version WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
    if row is None:
        raise LaneError(
            "POWERBI_SNAPSHOT_MISSING", "Select an exact powerbi snapshot from this POWERBI lane."
        )
    manifest = json.loads(lane.read_object(row["manifest_object"]))
    if (
        digest(canonical_json_bytes(manifest)) != snapshot_id
        or manifest["project_id"] != store.project_id
        or manifest["lane_id"] != "power_bi"
        or any(
            manifest[key] != row[key]
            for key in (
                "powerbi_id",
                "generation",
                "previous_snapshot",
                "raw_object",
                "facts_object",
                "parser_contract",
            )
        )
    ):
        raise LaneError(
            "POWERBI_SNAPSHOT_INTEGRITY", "The powerbi manifest differs from its indexed identity."
        )
    facts = json.loads(lane.read_object(manifest["facts_object"]))
    return manifest, facts


def _worker(execution, operation, arguments):
    response = execution.submit(operation, arguments).result()
    if response["status"] != "ok":
        raise LaneError(
            response.get("code", "POWERBI_WORKER_FAILED"),
            "The selected powerbi worker did not complete.",
        )
    body = json.loads(json_text(response["result"]))
    content = base64.b64decode(body.pop("content_base64"), validate=True)
    if (
        digest(content) != body["sha256"]
        or len(content) != body["bytes"]
        or body["filename"] != arguments["logical_name"]
    ):
        raise LaneError(
            "POWERBI_WORKER_BINDING",
            "The powerbi worker returned different bytes or a different powerbi.",
        )
    return content, body


def _natural(lane, snapshot_id, name, content):
    name = _relative(name)
    if "/" in name:
        raise LaneError(
            "POWERBI_OUTPUT_NAME_INVALID",
            "A lane-owned natural artifact requires a simple filename.",
        )
    target = lane.files / "natural" / snapshot_id / name
    reject_links(target, lane.folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    reject_links(target, lane.folder)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if _bytes(target, 16_777_216) != content:
            raise LaneError(
                "POWERBI_ARTIFACT_CHANGED",
                "An existing natural artifact differs from its immutable bytes.",
            ) from None
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    if _bytes(target, 16_777_216) != content:
        raise LaneError(
            "POWERBI_ARTIFACT_WRITE_FAILED",
            "The natural powerbi artifact failed byte verification.",
        )
    return str(target)


def publish(
    context,
    *,
    powerbi_id,
    logical_name,
    source_path,
    origin,
    previous,
    content,
    parsed,
    operation,
    source_documents=None,
    source_observation_route_id=None,
    max_file_bytes=None,
    max_export_bytes=None,
    intake=False,
    inputs=None,
):
    execution, store = context.execution, context.execution.store
    if current_snapshot(store, powerbi_id) != previous:
        raise LaneError(
            "POWERBI_SNAPSHOT_CHANGED", "Bind this operation to the exact current powerbi snapshot."
        )
    prior = read_snapshot(store, previous)[0] if previous else None
    selection = (execution.guard.source_route.selection.model_dump(mode='json') if execution.guard.source_route
                 else prior.get('source_route') if prior and not intake else None)
    observation = (source_observation_route_id or (selection['route_id'] if selection else None)
                   or (prior.get('source_observation_route_id') if prior and not intake else None))
    if source_observation_route_id and selection:
        selection = {**selection, 'route_id': source_observation_route_id}
    generation, stamp = (prior["generation"] + 1 if prior else 1), now()
    facts, contract = parsed["facts"], _parser_contract()
    source_members = (
        {name: digest(raw) for name, raw in source_documents.items()}
        if source_documents is not None
        else prior.get("source_members", {})
        if prior
        else {}
    )
    manifest = {
        "schema": "evidence-lane.powerbi-snapshot.v4",
        "project_id": store.project_id,
        "lane_id": "power_bi",
        "powerbi_id": powerbi_id,
        "logical_name": logical_name,
        "source_path": source_path,
        "origin": origin,
        "generation": generation,
        "previous_snapshot": previous,
        "raw_object": digest(content),
        "facts_object": digest(canonical_json_bytes(facts)),
        "parser_contract": contract,
        "source_object": source_members.get(source_path),
        "source_members": source_members,
        "bytes": len(content),
        "created_at": stamp,
        "operation": operation,
        "tool_evidence": parsed["evidence"],
        "fidelity": facts["fidelity"],
        "source_bytes_mutated": False,
    }
    manifest.update(source_route=selection, source_observation_route_id=observation, inputs=inputs or [],
        capture_limits={'max_file_bytes': max_file_bytes or (prior.get('capture_limits', {}).get('max_file_bytes', 8_388_608) if prior else 8_388_608)})
    manifest['capture_limits']['max_export_bytes'] = (max_export_bytes or max_file_bytes
        or (prior.get('capture_limits', {}).get('max_export_bytes', 16_777_216) if prior else 16_777_216))
    snapshot_id = digest(canonical_json_bytes(manifest))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(["power_bi", "receipts"]):
        lane = store.lane("power_bi")
        apply_migrations(lane, POWERBI_MIGRATIONS, writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute(
                "SELECT snapshot_id FROM powerbi_current WHERE powerbi_id=?", (powerbi_id,)
            ).fetchone()
            if (live[0] if live else None) != previous:
                raise LaneError(
                    "POWERBI_SNAPSHOT_CHANGED", "The powerbi changed before publication."
                )
            lane.put_object(content)
            if source_documents is not None:
                for raw in source_documents.values():
                    lane.put_object(raw)
            lane.put_object(canonical_json_bytes(facts))
            manifest_object = lane.put_object(canonical_json_bytes(manifest))
            connection.execute(
                "INSERT OR IGNORE INTO powerbi_file VALUES(?,?,?,?,?)",
                (powerbi_id, logical_name, source_path, origin, stamp),
            )
            connection.execute(
                "INSERT INTO powerbi_version VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    powerbi_id,
                    generation,
                    previous,
                    manifest_object,
                    manifest["raw_object"],
                    manifest["facts_object"],
                    contract,
                    stamp,
                ),
            )
            connection.execute(
                "INSERT INTO powerbi_structure VALUES(?,?,?)",
                (snapshot_id, manifest["facts_object"], json_text(facts["fidelity"])),
            )
            for item in facts["items"]:
                connection.execute(
                    "INSERT INTO " + POWERBI_TABLES[item["kind"]] + " VALUES(?,?,?,?,?,?)",
                    (
                        snapshot_id,
                        item["item_id"],
                        item["kind"],
                        item["ordinal"],
                        item["part"],
                        json_text(item),
                    ),
                )
            for item_id, ordinal, text in _chunks(facts):
                chunk_id = digest(canonical_json_bytes([snapshot_id, item_id, ordinal]))
                obj = lane.put_object(text.encode("utf-8"))
                connection.execute(
                    "INSERT INTO powerbi_chunk VALUES(?,?,?,?,?)",
                    (snapshot_id, chunk_id, item_id, ordinal, obj),
                )
                connection.execute(
                    "INSERT INTO powerbi_chunk_fts VALUES(?,?,?)", (snapshot_id, chunk_id, text)
                )
            path = _natural(lane, snapshot_id, PurePosixPath(logical_name).name, content)
            connection.execute(
                "INSERT INTO powerbi_current VALUES(?,?) ON CONFLICT(powerbi_id) DO UPDATE SET snapshot_id=excluded.snapshot_id",
                (powerbi_id, snapshot_id),
            )
            store.append_receipt(
                "powerbi_snapshot",
                {
                    "snapshot_id": snapshot_id,
                    "powerbi_id": powerbi_id,
                    "job_id": execution.claim.job_id,
                    "operation": operation,
                    "source_bytes_mutated": False,
                },
            )
    return result(
        store,
        operation,
        {
            "snapshot_id": snapshot_id,
            "powerbi_id": powerbi_id,
            "generation": generation,
            "previous_snapshot": previous,
            "sha256": manifest["raw_object"],
            "bytes": len(content),
            "logical_name": logical_name,
            "natural_path": path,
            "source_bytes_mutated": False,
            "fidelity": facts["fidelity"],
            "limitations": facts["limitations"],
            "tool_evidence": parsed["evidence"],
        },
    )


def generate_powerbi(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    name = _relative(request.logical_name)
    powerbi_id = digest(canonical_json_bytes([store.project_id, "generated", name]))
    if current_snapshot(store, powerbi_id) != request.expected_snapshot:
        raise LaneError(
            "POWERBI_SNAPSHOT_CHANGED", "Generation requires the exact current powerbi identity."
        )
    content, parsed = _worker(execution, "powerbi_generate", request.model_dump(mode="json"))
    return publish(
        context,
        powerbi_id=powerbi_id,
        logical_name=name,
        source_path=None,
        origin="generated",
        previous=request.expected_snapshot,
        content=content,
        parsed=parsed,
        operation="powerbi_generate",
    )


def verify_powerbi(context, request, output):
    store, snapshot_id = context.store, output.result["snapshot_id"]
    manifest, facts = read_snapshot(store, snapshot_id)
    lane = store.lane("power_bi")
    raw = lane.read_object(manifest["raw_object"])
    valid = len(raw) == manifest["bytes"] and manifest["raw_object"] == output.result["sha256"]
    valid &= parse_powerbi(manifest["logical_name"], raw, **facts["parse_options"]) == facts
    valid &= _bytes(Path(output.result["natural_path"])) == raw
    with lane.connection(read_only=True) as connection:
        for table in sorted(set(POWERBI_TABLES.values())):
            rows = connection.execute(
                "SELECT * FROM " + table + " WHERE snapshot_id=? ORDER BY item_id", (snapshot_id,)
            ).fetchall()
            expected = sorted(
                [item for item in facts["items"] if POWERBI_TABLES[item["kind"]] == table],
                key=lambda item: item["item_id"],
            )
            valid &= [json.loads(row["payload_json"]) for row in rows] == expected
            valid &= all(
                (row["item_id"], row["kind"], row["ordinal"], row["part"])
                == (item["item_id"], item["kind"], item["ordinal"], item["part"])
                for row, item in zip(rows, expected)
            )
        expected = list(_chunks(facts))
        rows = connection.execute(
            "SELECT c.*,f.text_content,o.size_bytes AS registered_bytes FROM powerbi_chunk c JOIN powerbi_chunk_fts f "
            "ON c.chunk_id=f.chunk_id AND c.snapshot_id=f.snapshot_id "
            "LEFT JOIN objects o ON o.digest=c.text_object WHERE c.snapshot_id=?",
            (snapshot_id,),
        ).fetchall()
        valid &= len(rows) == len(expected)
        lookup = {(item_id, ordinal): text for item_id, ordinal, text in expected}
        for row in rows:
            text = lookup.get((row["item_id"], row["ordinal"]))
            valid &= _chunk_matches(lane, row, text) and row["chunk_id"] == digest(
                canonical_json_bytes([snapshot_id, row["item_id"], row["ordinal"]])
            )
    live = True
    if isinstance(request, PowerBiIndex):
        names = [
            _relative(request.filename),
            *[_relative(name) for name in request.companion_files],
        ]
        observed = []
        for name in names:
            content = _bytes(context.source_path(name), request.max_file_bytes)
            observed.append({"name": name, "sha256": digest(content), "bytes": len(content)})
        live = observed == manifest["tool_evidence"]["source_files"]
    checks = {"powerbi_snapshot_integrity": valid, "powerbi_source_hash_unchanged": live}
    return [
        {
            "check_id": name,
            "passed": checks[name],
            "evidence": {
                "snapshot_id": snapshot_id,
                "source_bytes_mutated": False,
                "layout_verified": False,
            },
        }
        for name in context.requested_checks
    ]


def current_powerbis(store, request=None):
    lane = _lane(store)
    if lane is None:
        return {"powerbis": [], "initialized": False}
    read_compatibility(lane, POWERBI_MIGRATIONS)
    active = active_selector_sql(lane)
    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='powerbi_current'"
        ).fetchone():
            return {"powerbis": [], "initialized": False}
        rows = connection.execute(
            "SELECT v.*,f.logical_name,f.source_path,f.origin FROM powerbi_current c "
            "JOIN powerbi_version v USING(snapshot_id) JOIN powerbi_file f ON f.powerbi_id=c.powerbi_id "
            f"WHERE {active} ORDER BY v.created_at DESC LIMIT 129"
        ).fetchall()
    return {
        "powerbis": [dict(row) for row in rows[:128]],
        "truncated": len(rows) > 128,
        "initialized": True,
        "source_currentness": "not_checked_by_metadata_read",
    }


def query_powerbi(store, request):
    with bounded_project_read(store.root, time.monotonic() + 5):
        manifest, facts = read_snapshot(store, request.snapshot_id)
        lane = store.lane("power_bi")
        items = {row["item_id"]: row for row in facts["items"]}
        if request.collection == "metadata":
            return {
                "snapshot_id": request.snapshot_id,
                "powerbi_id": manifest["powerbi_id"],
                "logical_name": manifest["logical_name"],
                "source_object": manifest["source_object"],
                "raw_object": manifest["raw_object"],
                "source_members": manifest["source_members"],
                "fidelity": facts["fidelity"],
                "features": facts["features"],
                "schema_evidence": facts["schema_evidence"],
                "limitations": facts["limitations"],
                "structure_counts": {
                    kind: sum(row["kind"] == kind for row in facts["items"])
                    for kind in sorted({row["kind"] for row in facts["items"]})
                },
                "source_bytes_mutated": False,
            }
        args = [request.snapshot_id]
        if request.collection == "text":
            tokens = re.findall(r"\w+", request.query or "", re.UNICODE)
            if not 1 <= len(tokens) <= 32:
                raise LaneError(
                    "POWERBI_QUERY_TERMS_REQUIRED",
                    "Use one to thirty-two literal text search terms.",
                )
            sql = (
                "SELECT c.*,f.text_content,o.size_bytes AS registered_bytes,bm25(powerbi_chunk_fts) AS rank FROM powerbi_chunk_fts f JOIN powerbi_chunk c "
                "ON f.snapshot_id=c.snapshot_id AND f.chunk_id=c.chunk_id "
                "LEFT JOIN objects o ON o.digest=c.text_object WHERE c.snapshot_id=? AND powerbi_chunk_fts MATCH ? "
                "ORDER BY rank,c.item_id,c.ordinal"
            )
            args.append((' OR ' if request.match_mode == 'any' else ' AND ').join('"' + token + '"' for token in tokens))
        else:
            sql = (
                "SELECT * FROM "
                + POWERBI_TABLES[request.collection]
                + " WHERE snapshot_id=? AND kind=?"
            )
            args.append(request.collection)
            if request.query:
                sql += " AND instr(lower(json_extract(payload_json,'$.text')),lower(?))>0"
                args.append(request.query)
            sql += " ORDER BY part,ordinal,item_id"
        with lane.connection(read_only=True) as connection:
            rows = connection.execute(
                sql + " LIMIT ? OFFSET ?", [*args, request.limit + 1, request.offset]
            ).fetchall()
        values, used, truncated = [], 0, False
        for row in rows:
            item = items.get(row["item_id"])
            if item is None:
                raise LaneError(
                    "POWERBI_QUERY_INTEGRITY",
                    "A query item is outside the selected immutable powerbi.",
                )
            if request.collection == "text":
                start = row["ordinal"] * 4096
                text = item["text"][start : start + 4096]
                if (
                    row["ordinal"] < 0
                    or not _chunk_matches(lane, row, text)
                    or row["chunk_id"]
                    != digest(
                        canonical_json_bytes([request.snapshot_id, row["item_id"], row["ordinal"]])
                    )
                ):
                    raise LaneError(
                        "POWERBI_QUERY_INTEGRITY",
                        "The indexed text differs from its immutable powerbi facts.",
                    )
                value = {
                    "item_id": item["item_id"],
                    "part": item["part"],
                    "item_ordinal": item["ordinal"],
                    "text": text,
                    "rank": row["rank"],
                    "chunk_ordinal": row["ordinal"],
                }
            else:
                if json.loads(row["payload_json"]) != item or (
                    row["kind"],
                    row["ordinal"],
                    row["part"],
                ) != (item["kind"], item["ordinal"], item["part"]):
                    raise LaneError(
                        "POWERBI_QUERY_INTEGRITY",
                        "The typed item differs from its immutable powerbi facts.",
                    )
                value = item
            size = len(canonical_json_bytes(value))
            if len(values) >= request.limit or used + size > request.max_bytes:
                truncated = True
                break
            values.append(value)
            used += size
        if truncated and not values:
            raise LaneError(
                "POWERBI_QUERY_ITEM_TOO_LARGE",
                "Increase max_bytes for the next complete powerbi item.",
            )
        return {
            "snapshot_id": request.snapshot_id,
            "powerbi_id": manifest["powerbi_id"],
            "rows": values,
            "next_offset": request.offset + len(values) if truncated else None,
            "truncated": truncated,
            "fidelity": facts["fidelity"],
            "source_bytes_mutated": False,
        }


def _export_parse_options(store, request):
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', _relative(request.filename)]))
    snapshot = current_snapshot(store, destination_id) or request.snapshot_id
    return read_snapshot(store, snapshot)[1]['parse_options']


def export_powerbi(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, _ = read_snapshot(store, request.snapshot_id)
    relative = _relative(request.filename)
    if (
        PurePosixPath(relative).suffix.lower()
        != PurePosixPath(manifest["logical_name"]).suffix.lower()
    ):
        raise LaneError(
            "POWERBI_EXPORT_FORMAT_MISMATCH",
            "Export uses the existing powerbi format; it is not conversion.",
        )
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, "write", path=path)
    reject_links(path, store.source_root)
    before_content = _bytes(path) if path.is_file() else None
    before = digest(before_content) if before_content is not None else None
    if path.exists() and not path.is_file() or before != request.expected_sha256:
        raise LaneError(
            "POWERBI_EXPORT_DESTINATION_CHANGED",
            "Bind export to the exact destination hash, or absence for a new file.",
        )
    if not path.parent.is_dir():
        raise LaneError(
            "POWERBI_EXPORT_PARENT_MISSING", "Select an existing granted destination directory."
        )
    lane, export_id = store.lane("power_bi"), str(uuid4())
    content = lane.read_object(manifest["raw_object"])
    if manifest['parser_contract'] != _parser_contract():
        raise LaneError('POWERBI_PARSER_CONTRACT_CHANGED', 'Refresh this development snapshot with the current parser before exporting.')
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', relative]))
    previous = current_snapshot(store, destination_id)
    destination_manifest = read_snapshot(store, previous)[0] if previous else None
    if destination_manifest and 'source_observation_route_id' not in destination_manifest:
        raise LaneError('POWERBI_REFRESH_PROVENANCE_REQUIRED', 'Refresh the destination snapshot with current source provenance before export.')
    limit = (destination_manifest or manifest).get('capture_limits', {}).get(
        'max_file_bytes' if destination_manifest else 'max_export_bytes', 16_777_216)
    if len(content) > limit:
        raise LaneError('POWERBI_FILE_BYTE_BUDGET', 'The proposed export exceeds the selected destination intake bound.')
    from .artifact_contract import LaneArtifacts
    artifacts = LaneArtifacts(execution.guard.engine, store)
    view_id = 'power_bi.structure'
    view_selection = artifacts.current_selection(view_id)
    from .source_routing import SourceMutationRefresh
    source_refresh = SourceMutationRefresh(context, [relative], lane_id='power_bi',
        action='powerbi_export', max_file_bytes=16_777_216, allow_create=True, allow_archives=True,
        parent_route_id=destination_manifest.get('source_observation_route_id') if destination_manifest else None)
    source_refresh.expected_after(path, before, content)
    _calls(execution, 3 + int(bool(view_selection and view_selection['formats'])))
    parsed_content, parsed = _worker(execution, 'powerbi_parse_content', {
        'filename': str(path), 'logical_name': relative, 'max_file_bytes': limit,
        'content_base64': base64.b64encode(content).decode('ascii'),
        'expected_sha256': manifest['raw_object'], 'parse_options': _export_parse_options(store, request)})
    execution.guard.observe(execution)
    if parsed_content != content or manifest['parser_contract'] != _parser_contract():
        raise LaneError('POWERBI_PARSER_CONTRACT_CHANGED', 'The proposed bytes or selected parser changed before export.')
    effect = execution.prepare_effect(
        "powerbi:" + export_id,
        "Export exact versioned powerbi bytes to one granted project filename.",
    )
    with execution.lease.coordinated_transaction(["power_bi", "receipts"]):
        with lane.transaction() as connection:
            if before_content is not None:
                lane.put_object(before_content)
            connection.execute(
                "INSERT INTO powerbi_export VALUES(?,?,?,?,?,?,?)",
                (
                    export_id,
                    request.snapshot_id,
                    relative,
                    before,
                    manifest["raw_object"],
                    effect,
                    now(),
                ),
            )
        store.append_receipt(
            "powerbi_export_prepared",
            {
                "export_id": export_id,
                "snapshot_id": request.snapshot_id,
                "destination": relative,
                "before_sha256": before,
                "after_sha256": manifest["raw_object"],
                "effect_id": effect,
            },
        )
    descriptor, temporary = tempfile.mkstemp(
        prefix=".evidence-lane-powerbi-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        execution._before_more_work()
        ProjectAccess(store).authorize(context.client_id, "write", path=path)
        reject_links(path, store.source_root)
        observed = digest(_bytes(path)) if path.is_file() else None
        if observed != before or path.exists() and not path.is_file():
            raise LaneError(
                "POWERBI_EXPORT_DESTINATION_CHANGED",
                "The destination changed immediately before export; reconcile this effect.",
            )
        if before is None:
            # Windows rename fails if a new destination appeared; unlike replace,
            # it cannot silently overwrite a concurrently created user file.
            if os.name == "nt":
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
                temporary.unlink()
        else:
            os.chmod(temporary, path.stat().st_mode)
            os.replace(temporary, path)
        if _bytes(path) != content:
            raise LaneError(
                "POWERBI_EXPORT_VERIFY_FAILED", "The written powerbi bytes were not confirmed."
            )
        with execution.lease.transaction("plan"):
            evidence = execution.plan_store.put_object(
                canonical_json_bytes(
                    {
                        "export_id": export_id,
                        "destination": relative,
                        "sha256": manifest["raw_object"],
                        "exact_bytes_verified": True,
                    }
                )
            )
            execution.confirm_effect(effect, evidence)
    finally:
        if temporary.exists():
            temporary.unlink()
    with execution.lease.coordinated_transaction(['sources', 'power_bi', 'receipts']):
        source_result = source_refresh.publish(path=path, before_sha256=before, replacement=content,
            mutation_id=export_id, effect_id=effect)
        indexed = publish(context, powerbi_id=destination_id, logical_name=relative,
            source_path=relative, origin='source', previous=previous, content=content, parsed=parsed,
            operation='powerbi_export', max_file_bytes=limit,
            source_documents={relative: content} if previous is None else None,
            source_observation_route_id=source_result['route_id'],
            inputs=[{'lane_id': 'power_bi', 'snapshot_id': request.snapshot_id, 'sha256': manifest['raw_object'], 'operation': 'export'}])
        view_result = artifacts.refresh_selected(view_id, view_selection, execution, actor_id=context.client_id)
        execution._before_more_work()
        final_capture = source_refresh.capture()
        from .source_authority import freeze_source_authority
        for spec in source_refresh.specs:
            freeze_source_authority(spec, capture=final_capture)
        if final_capture.identities != source_refresh.expected_after(path, before, content):
            raise LaneError('SOURCE_REFRESH_UNEXPECTED_CHANGE', 'A source changed before the refreshed lanes could publish.')
    return result(
        store,
        "powerbi_export",
        {
            "export_id": export_id,
            "snapshot_id": request.snapshot_id,
            "destination": relative,
            "before_sha256": before,
            "after_sha256": manifest["raw_object"],
            "effect_id": effect,
            "source_bytes_mutated": True,
            "source_index_refresh_required": False,
            "index_refresh": indexed.model_dump(mode="json"),
            "source_refresh": source_result, "view_refresh": view_result,
            "automatic_replay": False,
        },
    )


def verify_export(context, request, output, *, engine=None):
    manifest, _ = read_snapshot(context.store, request.snapshot_id)
    valid = digest(_bytes(context.source_path(request.filename))) == manifest['raw_object'] == output.result['after_sha256']
    lane = context.store.lane('power_bi')
    with lane.connection(read_only=True) as connection:
        exported = connection.execute('SELECT * FROM powerbi_export WHERE export_id=?', (output.result['export_id'],)).fetchone()
    with context.store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT * FROM jobs_effects WHERE effect_id=?', (output.result['effect_id'],)).fetchone()
    valid &= exported is not None and effect is not None and effect['job_id'] == context.job_id and effect['state'] == 'confirmed'
    if exported is not None and effect is not None:
        valid &= all(exported[key] == value for key, value in {'snapshot_id': request.snapshot_id,
            'destination': _relative(request.filename), 'before_sha256': request.expected_sha256,
            'after_sha256': manifest['raw_object'], 'effect_id': output.result['effect_id']}.items())
        valid &= json.loads(context.store.lane('plan').read_object(effect['evidence_object'])) == {
            'export_id': output.result['export_id'], 'destination': _relative(request.filename),
            'sha256': manifest['raw_object'], 'exact_bytes_verified': True}
    indexed = PowerBiResult.model_validate(output.result['index_refresh'])
    index_manifest, index_facts = read_snapshot(context.store, indexed.result['snapshot_id'])
    expected_id = digest(canonical_json_bytes([context.store.project_id, 'source', _relative(request.filename)]))
    valid &= (indexed.project_id == context.store.project_id and indexed.operation == 'powerbi_export'
        and indexed.lane_id == 'power_bi' and index_manifest['powerbi_id'] == expected_id
        and current_snapshot(context.store, expected_id) == indexed.result['snapshot_id']
        and index_manifest['logical_name'] == index_manifest['source_path'] == _relative(request.filename)
        and index_manifest['raw_object'] == manifest['raw_object'] and not output.result['source_index_refresh_required']
        and index_manifest['inputs'] == [{'lane_id': 'power_bi', 'snapshot_id': request.snapshot_id,
            'sha256': manifest['raw_object'], 'operation': 'export'}])
    valid &= all(row['passed'] for row in verify_powerbi(replace(context,
        requested_checks=('powerbi_snapshot_integrity',)), request, indexed))
    source = output.result['source_refresh']
    prior = read_snapshot(context.store, index_manifest['previous_snapshot'])[0] if index_manifest['previous_snapshot'] else None
    options_snapshot = index_manifest['previous_snapshot'] or request.snapshot_id
    valid &= index_facts['parse_options'] == read_snapshot(context.store, options_snapshot)[1]['parse_options']
    from .source_routing import verify_export_refresh
    valid &= (index_manifest['source_observation_route_id'] == source['route_id']
        and source['parent_route_id'] == (prior.get('source_observation_route_id') if prior else None)
        and verify_export_refresh(context, source, lane_id='power_bi',
            action='powerbi_export', mutation_id=output.result['export_id'],
            effect_id=output.result['effect_id'], max_file_bytes=16_777_216, allow_archives=True))
    if engine is None:
        valid = False
    else:
        from .artifact_contract import LaneArtifacts
        view_valid, graph_workers = LaneArtifacts(engine, context.store).verify_refreshed(
            'power_bi.structure', output.result['view_refresh'])
        valid &= view_valid and len(context.worker_evidence) == 1 + graph_workers and all(
            row['status'] == 'ok' for row in context.worker_evidence)
    return [{'check_id': name, 'passed': valid, 'evidence': {'export_id': output.result['export_id'],
        'after_sha256': manifest['raw_object']}} for name in context.requested_checks]

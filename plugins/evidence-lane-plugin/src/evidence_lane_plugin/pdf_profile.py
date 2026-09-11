"""Immutable PDF versions in the separate PDF/OCR lane, with journaled exports."""

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
from .pdf_contracts import (
    PdfEdit,
    PdfExport,
    PdfGenerate,
    PdfIndex,
    PdfQuery,
    PdfRead,
    PdfSelection,
)
from .pdf_parsers import digest
from .pdf_schema import PDF_MIGRATIONS, PDF_TABLES
from .projects import ProjectAccess
from .registry import ActionSpec, Contract, FetchRoute, SearchRoute, SourceMaterialization
from .selector_schema import active_selector_sql
from .storage import bounded_project_read, json_text, now, project_snapshot, reject_links
from .tool_routes import ToolRoute


def _parser_contract():
    files = (
        "pdf_parsers.py",
        "pdf_forms.py",
        "pdf_authoring.py",
        "pdf_workers.py",
        "pdf_child.py",
        "pdf_process.py",
        "pdf_contracts.py",
        "pdf_native.py",
    )
    return digest(
        canonical_json_bytes(
            {name: digest(Path(__file__).with_name(name).read_bytes()) for name in files}
        )
    )


def format_contract():
    return {
        "pdf": {
            "extensions": [".pdf"],
            "native_text_backends": ["pymupdf", "pdfplumber", "pypdf", "poppler"],
            "forms": "AcroForm_fields_and_page_widgets",
            "form_text_acceptance": "generated_appearance_font_coverage_and_field_max_length",
            "generation": "closed_ReportLab_layout",
            "edits": [
                "field_values",
                "metadata",
                "page_permutation",
                "rotation",
                "explicit_flatten",
                "explicit_unique_orphan_repair",
            ],
            "ocr": "separate_selected_page_evidence_RapidOCR_or_Tesseract",
            "render": "selected_PDFium_pages",
            "enrichment": "separate_completed_offline_Docling_projection",
            "limits": {
                "input_bytes": 16_777_216,
                "indexed_pages": 500,
                "selected_render_ocr_pages": 50,
                "enrich_pages": 50,
            },
            "unsupported": [
                "encrypted_input",
                "XFA_editing",
                "signature_cryptographic_validation",
                "embedded_actions",
                "remote_references",
            ],
            "incomplete_enrichment": "rejected_without_publication",
            "visual_and_ocr_accuracy": "requires_independent_review",
        }
    }


def _chunks(facts):
    for item in facts["items"]:
        if item["kind"] not in {"text_block", "table", "form_field", "annotation", "outline"}:
            continue
        for ordinal, start in enumerate(range(0, len(item["text"]), 4096)):
            yield item["item_id"], ordinal, item["text"][start : start + 4096]


class PdfResult(Contract):
    project_id: str
    lane_id: Literal["pdf_ocr"] = "pdf_ocr"
    operation: str
    result: dict[str, JsonValue]


def result(store, operation, body):
    value = PdfResult(project_id=store.project_id, operation=operation, result=body)
    if len(canonical_json_bytes(value.model_dump(mode="json"))) > 2_097_152:
        raise LaneError("PDF_OUTPUT_BUDGET", "Select a smaller pdf result.")
    return value


def _relative(value):
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        path.is_absolute()
        or ".." in path.parts
        or ":" in str(path)
        or "\x00" in str(path)
        or str(path) == "."
    ):
        raise LaneError("PDF_PATH_INVALID", "Select an exact project-relative pdf filename.")
    return path.as_posix()


def _bytes(path, limit=16_777_216):
    reject_links(path, Path(path.anchor))
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise LaneError("PDF_FILE_BYTE_BUDGET", "The selected pdf exceeds its byte budget.")
    return content


def _lane(store):
    try:
        return store.lane("pdf_ocr")
    except LaneError as error:
        if error.code != "LANE_NOT_INITIALIZED":
            raise
        return None


def _calls(execution, remaining):
    execution.guard.check()
    if execution.guard.calls + remaining > execution.guard.task.budget.max_tool_calls:
        raise LaneError(
            "DELTA_TOOL_BUDGET", "The pdf operation must leave room for its required verification."
        )


def current_snapshot(store, pdf_id):
    lane = _lane(store)
    if lane is None:
        return None
    read_compatibility(lane, PDF_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='pdf_current'"
        ).fetchone():
            return None
        row = connection.execute(
            "SELECT snapshot_id FROM pdf_current WHERE pdf_id=?", (pdf_id,)
        ).fetchone()
    return row[0] if row else None


def read_snapshot(store, snapshot_id):
    lane = _lane(store)
    if lane is None:
        raise LaneError("PDF_SNAPSHOT_MISSING", "Index or generate the selected pdf first.")
    read_compatibility(lane, PDF_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='pdf_version'"
        ).fetchone():
            raise LaneError("PDF_SNAPSHOT_MISSING", "Index or generate the selected pdf first.")
        row = connection.execute(
            "SELECT * FROM pdf_version WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
    if row is None:
        raise LaneError("PDF_SNAPSHOT_MISSING", "Select an exact pdf snapshot from this PDF lane.")
    manifest = json.loads(lane.read_object(row["manifest_object"]))
    if (
        digest(canonical_json_bytes(manifest)) != snapshot_id
        or manifest["project_id"] != store.project_id
        or manifest["lane_id"] != "pdf_ocr"
        or any(
            manifest[key] != row[key]
            for key in (
                "pdf_id",
                "generation",
                "previous_snapshot",
                "raw_object",
                "facts_object",
                "parser_contract",
            )
        )
    ):
        raise LaneError(
            "PDF_SNAPSHOT_INTEGRITY", "The pdf manifest differs from its indexed identity."
        )
    facts = json.loads(lane.read_object(manifest["facts_object"]))
    return manifest, facts


def _worker(execution, operation, arguments):
    response = execution.submit(operation, arguments).result()
    if response["status"] != "ok":
        raise LaneError(
            response.get("code", "PDF_WORKER_FAILED"), "The selected pdf worker did not complete."
        )
    body = json.loads(json_text(response["result"]))
    content = base64.b64decode(body.pop("content_base64"), validate=True)
    if (
        digest(content) != body["sha256"]
        or len(content) != body["bytes"]
        or body["filename"] != arguments["logical_name"]
    ):
        raise LaneError(
            "PDF_WORKER_BINDING", "The pdf worker returned different bytes or a different pdf."
        )
    return content, body


def _natural(lane, snapshot_id, name, content):
    name = _relative(name)
    if "/" in name:
        raise LaneError(
            "PDF_OUTPUT_NAME_INVALID", "A lane-owned natural artifact requires a simple filename."
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
                "PDF_ARTIFACT_CHANGED",
                "An existing natural artifact differs from its immutable bytes.",
            ) from None
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    if _bytes(target, 16_777_216) != content:
        raise LaneError(
            "PDF_ARTIFACT_WRITE_FAILED", "The natural pdf artifact failed byte verification."
        )
    return str(target)


def publish(
    context,
    *,
    pdf_id,
    logical_name,
    source_path,
    origin,
    previous,
    content,
    parsed,
    operation,
    source_content=None,
    source_observation_route_id=None,
    max_file_bytes=None,
    intake=False,
    inputs=None,
):
    execution, store = context.execution, context.execution.store
    if current_snapshot(store, pdf_id) != previous:
        raise LaneError(
            "PDF_SNAPSHOT_CHANGED", "Bind this operation to the exact current pdf snapshot."
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
    manifest = {
        "schema": "evidence-lane.pdf-snapshot.v4",
        "project_id": store.project_id,
        "lane_id": "pdf_ocr",
        "pdf_id": pdf_id,
        "logical_name": logical_name,
        "source_path": source_path,
        "origin": origin,
        "generation": generation,
        "previous_snapshot": previous,
        "raw_object": digest(content),
        "facts_object": digest(canonical_json_bytes(facts)),
        "parser_contract": contract,
        "source_object": digest(source_content)
        if source_content is not None
        else prior.get("source_object")
        if prior
        else digest(content)
        if source_path is not None
        else None,
        "bytes": len(content),
        "created_at": stamp,
        "operation": operation,
        "tool_evidence": {**parsed["evidence"], "process": parsed.get("process_evidence")},
        "fidelity": facts["fidelity"],
        "source_bytes_mutated": False,
    }
    manifest.update(source_route=selection, source_observation_route_id=observation, inputs=inputs or [],
        capture_limits={'max_file_bytes': max_file_bytes or (prior.get('capture_limits', {}).get('max_file_bytes', 16_777_216) if prior else 16_777_216)})
    snapshot_id = digest(canonical_json_bytes(manifest))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(["pdf_ocr", "receipts"]):
        lane = store.lane("pdf_ocr")
        apply_migrations(lane, PDF_MIGRATIONS, writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute(
                "SELECT snapshot_id FROM pdf_current WHERE pdf_id=?", (pdf_id,)
            ).fetchone()
            if (live[0] if live else None) != previous:
                raise LaneError("PDF_SNAPSHOT_CHANGED", "The pdf changed before publication.")
            lane.put_object(content)
            if source_content is not None:
                lane.put_object(source_content)
            lane.put_object(canonical_json_bytes(facts))
            manifest_object = lane.put_object(canonical_json_bytes(manifest))
            connection.execute(
                "INSERT OR IGNORE INTO pdf_file VALUES(?,?,?,?,?)",
                (pdf_id, logical_name, source_path, origin, stamp),
            )
            connection.execute(
                "INSERT INTO pdf_version VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    pdf_id,
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
                "INSERT INTO pdf_structure VALUES(?,?,?)",
                (snapshot_id, manifest["facts_object"], json_text(facts["fidelity"])),
            )
            for item in facts["items"]:
                connection.execute(
                    "INSERT INTO " + PDF_TABLES[item["kind"]] + " VALUES(?,?,?,?,?,?)",
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
                    "INSERT INTO pdf_chunk VALUES(?,?,?,?,?)",
                    (snapshot_id, chunk_id, item_id, ordinal, obj),
                )
                connection.execute(
                    "INSERT INTO pdf_chunk_fts VALUES(?,?,?)", (snapshot_id, chunk_id, text)
                )
            path = _natural(lane, snapshot_id, PurePosixPath(logical_name).name, content)
            connection.execute(
                "INSERT INTO pdf_current VALUES(?,?) ON CONFLICT(pdf_id) DO UPDATE SET snapshot_id=excluded.snapshot_id",
                (pdf_id, snapshot_id),
            )
            store.append_receipt(
                "pdf_snapshot",
                {
                    "snapshot_id": snapshot_id,
                    "pdf_id": pdf_id,
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
            "pdf_id": pdf_id,
            "generation": generation,
            "previous_snapshot": previous,
            "sha256": manifest["raw_object"],
            "bytes": len(content),
            "logical_name": logical_name,
            "natural_path": path,
            "source_bytes_mutated": False,
            "fidelity": facts["fidelity"],
            "limitations": facts["limitations"],
            "tool_evidence": {**parsed["evidence"], "process": parsed.get("process_evidence")},
        },
    )


def generate_pdf(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    name = _relative(request.logical_name)
    pdf_id = digest(canonical_json_bytes([store.project_id, "generated", name]))
    if current_snapshot(store, pdf_id) != request.expected_snapshot:
        raise LaneError(
            "PDF_SNAPSHOT_CHANGED", "Generation requires the exact current pdf identity."
        )
    content, parsed = _worker(execution, "pdf_generate", request.model_dump(mode="json"))
    return publish(
        context,
        pdf_id=pdf_id,
        logical_name=name,
        source_path=None,
        origin="generated",
        previous=request.expected_snapshot,
        content=content,
        parsed=parsed,
        operation="pdf_generate",
    )


def index_pdf(context, request, *, backend="pymupdf"):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    relative = _relative(request.filename)
    if PurePosixPath(relative).suffix.lower() != ".pdf":
        raise LaneError("PDF_FORMAT_UNSUPPORTED", "Select a PDF source file.")
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, "read", path=path)
    pdf_id = digest(canonical_json_bytes([store.project_id, "source", relative]))
    if current_snapshot(store, pdf_id) != request.expected_snapshot:
        raise LaneError("PDF_SNAPSHOT_CHANGED", "Refresh requires the exact current PDF snapshot.")
    prior = read_snapshot(store, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior.get('source_route') is not None and execution.guard.source_route is None:
        raise LaneError('SOURCE_ROUTE_SELECTION_REQUIRED', 'Select the refreshed Sources route for this previously routed input.')
    content, parsed = _worker(
        execution,
        "pdf_parse_file",
        {
            "filename": str(path),
            "logical_name": relative,
            "max_file_bytes": request.max_file_bytes,
            "parse_options": {
                "max_pages": request.max_pages,
                "extract_tables": request.extract_tables,
                "native_backend": backend,
            },
        },
    )
    if _bytes(path, request.max_file_bytes) != content:
        raise LaneError("PDF_SOURCE_CHANGED", "The admitted PDF changed during extraction.")
    return publish(
        context,
        pdf_id=pdf_id,
        logical_name=relative,
        source_path=relative,
        origin="source",
        previous=request.expected_snapshot,
        content=content,
        parsed=parsed,
        operation="pdf_index",
        source_content=content,
        max_file_bytes=request.max_file_bytes, intake=True,
    )


def edit_pdf(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, facts = read_snapshot(store, request.snapshot_id)
    if (
        manifest["raw_object"] != request.expected_sha256
        or current_snapshot(store, manifest["pdf_id"]) != request.snapshot_id
    ):
        raise LaneError("PDF_EDIT_SNAPSHOT_CHANGED", "Edit the exact current PDF bytes.")
    content, parsed = _worker(
        execution,
        "pdf_edit",
        {
            "logical_name": manifest["logical_name"],
            "content_base64": base64.b64encode(
                store.lane("pdf_ocr").read_object(manifest["raw_object"])
            ).decode("ascii"),
            "expected_sha256": request.expected_sha256,
            "parse_options": facts["parse_options"],
            "request": request.model_dump(mode="json"),
        },
    )
    return publish(
        context,
        pdf_id=manifest["pdf_id"],
        logical_name=manifest["logical_name"],
        source_path=manifest["source_path"],
        origin=manifest["origin"],
        previous=request.snapshot_id,
        content=content,
        parsed=parsed,
        operation="pdf_edit",
    )


def verify_pdf(context, request, output):
    from .pdf_process import invoke_pdf

    store, snapshot_id = context.store, output.result["snapshot_id"]
    manifest, facts = read_snapshot(store, snapshot_id)
    lane = store.lane("pdf_ocr")
    raw = lane.read_object(manifest["raw_object"])
    valid = len(raw) == manifest["bytes"] and manifest["raw_object"] == output.result["sha256"]
    reparsed = invoke_pdf(
        "parse",
        {
            "logical_name": manifest["logical_name"],
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "expected_sha256": manifest["raw_object"],
            "parse_options": facts["parse_options"],
        },
    )
    valid &= reparsed["facts"] == facts and _bytes(Path(output.result["natural_path"])) == raw
    with lane.connection(read_only=True) as connection:
        for table in sorted(set(PDF_TABLES.values())):
            rows = connection.execute(
                "SELECT * FROM " + table + " WHERE snapshot_id=? ORDER BY item_id", (snapshot_id,)
            ).fetchall()
            expected = sorted(
                [item for item in facts["items"] if PDF_TABLES[item["kind"]] == table],
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
            "SELECT c.*,f.text_content,o.size_bytes AS registered_bytes FROM pdf_chunk c JOIN pdf_chunk_fts f "
            "ON c.chunk_id=f.chunk_id AND c.snapshot_id=f.snapshot_id LEFT JOIN objects o ON o.digest=c.text_object WHERE c.snapshot_id=?",
            (snapshot_id,),
        ).fetchall()
        valid &= len(rows) == len(expected)
        lookup = {(item_id, ordinal): text for item_id, ordinal, text in expected}
        for row in rows:
            valid &= _chunk_matches(
                lane, row, lookup.get((row["item_id"], row["ordinal"]))
            ) and row["chunk_id"] == digest(
                canonical_json_bytes([snapshot_id, row["item_id"], row["ordinal"]])
            )
    live = (
        not isinstance(request, PdfIndex)
        or _bytes(context.source_path(request.filename), request.max_file_bytes) == raw
    )
    checks = {"pdf_snapshot_integrity": valid, "pdf_source_hash_unchanged": live}
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


def index_pdf_plumber(context, request):
    return index_pdf(context, request, backend="pdfplumber")


def index_pdf_pypdf(context, request):
    return index_pdf(context, request, backend="pypdf")


def index_pdf_poppler(context, request):
    return index_pdf(context, request, backend="poppler")


def register_pdf_actions(engine):
    from .artifact_contract import SelectedViewRefresh

    def export_route(render, backend=None):
        def applicable(context, request):
            from .artifact_contract import LaneArtifacts
            store = engine.directory.open(context.project_id)
            view = LaneArtifacts(engine, store).current_selection('pdf_ocr.structure')
            return (bool(view and view['formats']) is render
                and (backend is None or _export_parse_options(store, request)['native_backend'] == backend))
        return applicable

    def verify_exported(context, request, output):
        return verify_export(context, request, output, engine=engine)

    for action, workflow in (("pdf_index", "source-intake"), ("pdf_refresh", "refresh")):
        engine.registry.register(
            ActionSpec(
                action,
                "Snapshot exact PDF bytes, page text, geometry and separate form/widget evidence.",
                PdfIndex,
                PdfResult,
                index_pdf,
                permission="write",
                mutates=True,
                requires_delta=True,
                profile="pdf_ocr",
                workflow=workflow,
                path_fields=("filename",),
                source_lanes=('pdf_ocr',),
                materialization=SourceMaterialization() if action == 'pdf_index' else None,
                worker_operations=("pdf_parse_file",),
                verification_checks=("pdf_snapshot_integrity", "pdf_source_hash_unchanged"),
                verifier=verify_pdf,
                tool_routes=(
                    ToolRoute(
                        action + ".native_pdf",
                        index_pdf,
                        ("Python", "pypdf", "PyMuPDF", "pdfplumber"),
                        argument_values=(("text_backend", ("auto", "pymupdf")),),
                    ),
                    ToolRoute(
                        action + ".pdfplumber",
                        index_pdf_plumber,
                        ("Python", "pypdf", "pdfplumber"),
                        reason="Attributed pdfplumber text and tables.",
                        argument_values=(("text_backend", ("auto", "pdfplumber")),),
                    ),
                    ToolRoute(
                        action + ".pypdf",
                        index_pdf_pypdf,
                        ("Python", "pypdf", "pdfplumber"),
                        reason="Retained pypdf text backend with separate geometric tables.",
                        argument_values=(("text_backend", ("auto", "pypdf")),),
                    ),
                    ToolRoute(
                        action + ".poppler",
                        index_pdf_poppler,
                        ("Python", "pypdf", "pdfplumber", "Poppler_pdftotext_pdfinfo"),
                        systems=("Windows",),
                        reason="Verified Poppler native text backend with exact page binding.",
                        argument_values=(("text_backend", ("auto", "poppler")),),
                    ),
                ),
            )
        )
    for action, model, handler, tools in (
        (
            "pdf_generate",
            PdfGenerate,
            generate_pdf,
            ("Python", "ReportLab", "pypdf", "PyMuPDF", "pdfplumber", "Pillow"),
        ),
        ("pdf_edit", PdfEdit, edit_pdf, ("Python", "pypdf", "PyMuPDF", "pdfplumber")),
    ):
        engine.registry.register(
            ActionSpec(
                action,
                "Publish an immutable PDF derivative and verify canonical field values and page widgets.",
                model,
                PdfResult,
                handler,
                permission="write",
                mutates=True,
                requires_delta=True,
                profile="pdf_ocr",
                workflow="build",
                worker_operations=(action,),
                verification_checks=("pdf_snapshot_integrity",),
                verifier=verify_pdf,
                tool_routes=(ToolRoute(action + ".native_pdf", handler, tools),),
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
            "pdf_current",
            PdfSelection,
            current_pdfs,
            "Read current PDF identities without refreshing source files.",
        ),
        (
            "pdf_query",
            PdfQuery,
            query_pdf,
            "Read bounded PDF structures or literal FTS5/BM25 matches from an exact version.",
        ),
        (
            "pdf_read",
            PdfRead,
            read_pdf,
            "Read a bounded byte range from exact PDF or original source bytes.",
        ),
    ):
        engine.registry.register(
            ActionSpec(
                action,
                description,
                model,
                PdfResult,
                query_handler(function, action),
                profile="pdf_ocr",
                workflow="source-intake",
                queryable_in_delta=True,
                cross_project_read=True,
                studio_read=True,
                read_migrations=PDF_MIGRATIONS,
                search=SearchRoute(('pdf_ocr',), 'rows', 'pdf_current', 'pdfs', 'text', 'any', rerank_text='text') if action == 'pdf_query' else None,
                fetch=FetchRoute(('pdf_ocr',), 'pdf') if action == 'pdf_read' else None,
            )
        )
    engine.registry.register(
        ActionSpec(
            "pdf_export",
            "Parse exact exported bytes and renew Sources, destination facts and existing lane views with the selected native parser.",
            PdfExport,
            PdfResult,
            export_pdf,
            permission="write",
            mutates=True,
            requires_delta=True,
            profile="pdf_ocr",
            workflow="build",
            path_fields=("filename",),
            verification_checks=("pdf_export_hash_verified",),
            verifier=verify_exported, worker_operations=("pdf_parse_bytes", "render_lane_view"),
            tool_routes=tuple(ToolRoute('pdf_export.' + backend + ('_view' if render else ''), export_pdf,
                (*tools, *(('LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx') if render else ())),
                view_refresh=SelectedViewRefresh(engine, 'pdf_ocr.structure'), systems=('Windows',) if backend == 'poppler' else ('Windows', 'Darwin', 'Linux'), applicable=export_route(render, backend),
                worker_operations=('pdf_parse_bytes', *(('render_lane_view',) if render else ())))
                for render in (False, True) for backend, tools in (
                    ('pymupdf', ('Python', 'pypdf', 'PyMuPDF', 'pdfplumber')),
                    ('pdfplumber', ('Python', 'pypdf', 'pdfplumber')),
                    ('pypdf', ('Python', 'pypdf', 'pdfplumber')),
                    ('poppler', ('Python', 'pypdf', 'pdfplumber', 'Poppler_pdftotext_pdfinfo')))),
        )
    )

    from .pdf_derivatives import register_pdf_derivatives

    register_pdf_derivatives(engine)
    from .pdf_views import register_pdf_views

    register_pdf_views(engine)
    from .pdf_enrichment import register_pdf_enrichment_actions

    register_pdf_enrichment_actions(engine)


def current_pdfs(store, request=None):
    lane = _lane(store)
    if lane is None:
        return {"pdfs": [], "initialized": False}
    read_compatibility(lane, PDF_MIGRATIONS)
    active = active_selector_sql(lane)
    with lane.connection(read_only=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='pdf_current'"
        ).fetchone():
            return {"pdfs": [], "initialized": False}
        rows = connection.execute(
            "SELECT v.*,f.logical_name,f.source_path,f.origin FROM pdf_current c "
            "JOIN pdf_version v USING(snapshot_id) JOIN pdf_file f ON f.pdf_id=c.pdf_id "
            f"WHERE {active} ORDER BY v.created_at DESC LIMIT 129"
        ).fetchall()
    return {
        "pdfs": [dict(row) for row in rows[:128]],
        "truncated": len(rows) > 128,
        "initialized": True,
        "source_currentness": "not_checked_by_metadata_read",
    }


def read_pdf(store, request):
    manifest, _ = read_snapshot(store, request.snapshot_id)
    selected = (
        manifest["source_object"]
        if request.representation == "original_source"
        else manifest["raw_object"]
    )
    if selected is None:
        raise LaneError(
            "PDF_ORIGINAL_SOURCE_ABSENT", "This generated pdf has no separate original source file."
        )
    raw = store.lane("pdf_ocr").read_object(selected)
    value = raw[request.offset : request.offset + request.max_bytes]
    end = request.offset + len(value)
    return {
        "snapshot_id": request.snapshot_id,
        "logical_name": manifest["source_path"]
        if request.representation == "original_source"
        else manifest["logical_name"],
        "sha256": selected,
        "representation": request.representation,
        "total_bytes": len(raw),
        "offset": request.offset,
        "content_base64": base64.b64encode(value).decode("ascii"),
        "next_offset": end if end < len(raw) else None,
        "source_bytes_mutated": False,
    }


def _export_parse_options(store, request):
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', _relative(request.filename)]))
    snapshot = current_snapshot(store, destination_id) or request.snapshot_id
    return read_snapshot(store, snapshot)[1]['parse_options']


def export_pdf(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, _ = read_snapshot(store, request.snapshot_id)
    relative = _relative(request.filename)
    if (
        PurePosixPath(relative).suffix.lower()
        != PurePosixPath(manifest["logical_name"]).suffix.lower()
    ):
        raise LaneError(
            "PDF_EXPORT_FORMAT_MISMATCH",
            "Export uses the existing pdf format; it is not conversion.",
        )
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, "write", path=path)
    reject_links(path, store.source_root)
    before_content = _bytes(path) if path.is_file() else None
    before = digest(before_content) if before_content is not None else None
    if path.exists() and not path.is_file() or before != request.expected_sha256:
        raise LaneError(
            "PDF_EXPORT_DESTINATION_CHANGED",
            "Bind export to the exact destination hash, or absence for a new file.",
        )
    if not path.parent.is_dir():
        raise LaneError(
            "PDF_EXPORT_PARENT_MISSING", "Select an existing granted destination directory."
        )
    lane, export_id = store.lane("pdf_ocr"), str(uuid4())
    content = lane.read_object(manifest["raw_object"])
    if manifest['parser_contract'] != _parser_contract():
        raise LaneError('PDF_PARSER_CONTRACT_CHANGED', 'Refresh this development snapshot with the current parser before exporting.')
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', relative]))
    previous = current_snapshot(store, destination_id)
    destination_manifest = read_snapshot(store, previous)[0] if previous else None
    if destination_manifest and 'source_observation_route_id' not in destination_manifest:
        raise LaneError('PDF_REFRESH_PROVENANCE_REQUIRED', 'Refresh the destination snapshot with current source provenance before export.')
    limit = (destination_manifest or manifest).get('capture_limits', {}).get('max_file_bytes', 16_777_216)
    if len(content) > limit:
        raise LaneError('PDF_FILE_BYTE_BUDGET', 'The proposed export exceeds the selected destination intake bound.')
    from .artifact_contract import LaneArtifacts
    artifacts = LaneArtifacts(execution.guard.engine, store)
    view_id = 'pdf_ocr.structure'
    view_selection = artifacts.current_selection(view_id)
    from .source_routing import SourceMutationRefresh
    source_refresh = SourceMutationRefresh(context, [relative], lane_id='pdf_ocr',
        action='pdf_export', max_file_bytes=16_777_216, allow_create=True,
        parent_route_id=destination_manifest.get('source_observation_route_id') if destination_manifest else None)
    source_refresh.expected_after(path, before, content)
    _calls(execution, 3 + int(bool(view_selection and view_selection['formats'])))
    parsed_content, parsed = _worker(execution, 'pdf_parse_bytes', {
        'filename': str(path), 'logical_name': relative, 'max_file_bytes': limit,
        'content_base64': base64.b64encode(content).decode('ascii'),
        'expected_sha256': manifest['raw_object'], 'parse_options': _export_parse_options(store, request)})
    execution.guard.observe(execution)
    if parsed_content != content or manifest['parser_contract'] != _parser_contract():
        raise LaneError('PDF_PARSER_CONTRACT_CHANGED', 'The proposed bytes or selected parser changed before export.')
    effect = execution.prepare_effect(
        "pdf:" + export_id, "Export exact versioned pdf bytes to one granted project filename."
    )
    with execution.lease.coordinated_transaction(["pdf_ocr", "receipts"]):
        with lane.transaction() as connection:
            if before_content is not None:
                lane.put_object(before_content)
            connection.execute(
                "INSERT INTO pdf_export VALUES(?,?,?,?,?,?,?)",
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
            "pdf_export_prepared",
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
        prefix=".evidence-lane-pdf-", suffix=".tmp", dir=path.parent
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
                "PDF_EXPORT_DESTINATION_CHANGED",
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
            raise LaneError("PDF_EXPORT_VERIFY_FAILED", "The written pdf bytes were not confirmed.")
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
    with execution.lease.coordinated_transaction(['sources', 'pdf_ocr', 'receipts']):
        source_result = source_refresh.publish(path=path, before_sha256=before, replacement=content,
            mutation_id=export_id, effect_id=effect)
        indexed = publish(context, pdf_id=destination_id, logical_name=relative,
            source_path=relative, origin='source', previous=previous, content=content, parsed=parsed,
            operation='pdf_export', max_file_bytes=limit,
            source_observation_route_id=source_result['route_id'],
            inputs=[{'lane_id': 'pdf_ocr', 'snapshot_id': request.snapshot_id, 'sha256': manifest['raw_object'], 'operation': 'export'}])
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
        "pdf_export",
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
    lane = context.store.lane('pdf_ocr')
    with lane.connection(read_only=True) as connection:
        exported = connection.execute('SELECT * FROM pdf_export WHERE export_id=?', (output.result['export_id'],)).fetchone()
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
    indexed = PdfResult.model_validate(output.result['index_refresh'])
    index_manifest, index_facts = read_snapshot(context.store, indexed.result['snapshot_id'])
    expected_id = digest(canonical_json_bytes([context.store.project_id, 'source', _relative(request.filename)]))
    valid &= (indexed.project_id == context.store.project_id and indexed.operation == 'pdf_export'
        and indexed.lane_id == 'pdf_ocr' and index_manifest['pdf_id'] == expected_id
        and current_snapshot(context.store, expected_id) == indexed.result['snapshot_id']
        and index_manifest['logical_name'] == index_manifest['source_path'] == _relative(request.filename)
        and index_manifest['raw_object'] == manifest['raw_object'] and not output.result['source_index_refresh_required']
        and index_manifest['inputs'] == [{'lane_id': 'pdf_ocr', 'snapshot_id': request.snapshot_id,
            'sha256': manifest['raw_object'], 'operation': 'export'}])
    valid &= all(row['passed'] for row in verify_pdf(replace(context,
        requested_checks=('pdf_snapshot_integrity',)), request, indexed))
    source = output.result['source_refresh']
    prior = read_snapshot(context.store, index_manifest['previous_snapshot'])[0] if index_manifest['previous_snapshot'] else None
    options_snapshot = index_manifest['previous_snapshot'] or request.snapshot_id
    valid &= index_facts['parse_options'] == read_snapshot(context.store, options_snapshot)[1]['parse_options']
    from .source_routing import verify_export_refresh
    valid &= (index_manifest['source_observation_route_id'] == source['route_id']
        and source['parent_route_id'] == (prior.get('source_observation_route_id') if prior else None)
        and verify_export_refresh(context, source, lane_id='pdf_ocr',
            action='pdf_export', mutation_id=output.result['export_id'],
            effect_id=output.result['effect_id'], max_file_bytes=16_777_216))
    if engine is None:
        valid = False
    else:
        from .artifact_contract import LaneArtifacts
        view_valid, graph_workers = LaneArtifacts(engine, context.store).verify_refreshed(
            'pdf_ocr.structure', output.result['view_refresh'])
        valid &= view_valid and len(context.worker_evidence) == 1 + graph_workers and all(
            row['status'] == 'ok' for row in context.worker_evidence)
    return [{'check_id': name, 'passed': valid, 'evidence': {'export_id': output.result['export_id'],
        'after_sha256': manifest['raw_object']}} for name in context.requested_checks]


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


def query_pdf(store, request):
    with bounded_project_read(store.root, time.monotonic() + 5):
        manifest, facts = read_snapshot(store, request.snapshot_id)
        lane = store.lane("pdf_ocr")
        if request.collection in {"ocr_run", "ocr_line", "review_region"}:
            from .pdf_derivatives import query_ocr

            return query_ocr(store, request)
        items = {row["item_id"]: row for row in facts["items"]}
        if request.collection == "metadata":
            return {
                "snapshot_id": request.snapshot_id,
                "pdf_id": manifest["pdf_id"],
                "logical_name": manifest["logical_name"],
                "source_object": manifest["source_object"],
                "raw_object": manifest["raw_object"],
                "fidelity": facts["fidelity"],
                "features": facts["features"],
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
                    "PDF_QUERY_TERMS_REQUIRED",
                    "Use one to thirty-two literal text search terms.",
                )
            sql = (
                "SELECT c.*,f.text_content,o.size_bytes AS registered_bytes,bm25(pdf_chunk_fts) AS rank FROM pdf_chunk_fts f JOIN pdf_chunk c "
                "ON f.snapshot_id=c.snapshot_id AND f.chunk_id=c.chunk_id "
                "LEFT JOIN objects o ON o.digest=c.text_object WHERE c.snapshot_id=? AND pdf_chunk_fts MATCH ? "
                "ORDER BY rank,c.item_id,c.ordinal"
            )
            args.append((' OR ' if request.match_mode == 'any' else ' AND ').join('"' + token + '"' for token in tokens))
            if request.page is not None:
                sql = sql.replace(
                    "ORDER BY rank",
                    "AND c.item_id IN (SELECT item_id FROM pdf_text_block WHERE snapshot_id=? AND part=? UNION SELECT item_id FROM pdf_table WHERE snapshot_id=? AND part=? UNION SELECT item_id FROM pdf_annotation WHERE snapshot_id=? AND part=? UNION SELECT item_id FROM pdf_outline WHERE snapshot_id=? AND part=?) ORDER BY rank",
                )
                args.extend([request.snapshot_id, f"page/{request.page}"] * 4)
        else:
            sql = (
                "SELECT * FROM "
                + PDF_TABLES[request.collection]
                + " WHERE snapshot_id=? AND kind=?"
            )
            args.append(request.collection)
            if request.page is not None:
                sql += " AND json_extract(payload_json,'$.page')=?"
                args.append(request.page)
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
                    "PDF_QUERY_INTEGRITY",
                    "A query item is outside the selected immutable pdf.",
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
                        "PDF_QUERY_INTEGRITY",
                        "The indexed text differs from its immutable pdf facts.",
                    )
                value = {
                    "item_id": item["item_id"],
                    "part": item["part"],
                    "item_ordinal": item["ordinal"],
                    "page": item["page"],
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
                        "PDF_QUERY_INTEGRITY",
                        "The typed item differs from its immutable pdf facts.",
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
                "PDF_QUERY_ITEM_TOO_LARGE",
                "Increase max_bytes for the next complete pdf item.",
            )
        return {
            "snapshot_id": request.snapshot_id,
            "pdf_id": manifest["pdf_id"],
            "rows": values,
            "next_offset": request.offset + len(values) if truncated else None,
            "truncated": truncated,
            "fidelity": facts["fidelity"],
            "source_bytes_mutated": False,
        }

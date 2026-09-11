"""Raster and OCR results reference exact PDF snapshots in their owning lane."""

from __future__ import annotations

import base64
import io
import json
import re

from .compute_routes import ComputeContract
from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations
from .pdf_contracts import PdfOcr, PdfOcrRead, PdfRender, PdfRenderRead
from .pdf_parsers import digest
from .pdf_profile import PdfResult, _calls, _natural, read_snapshot, result
from .pdf_schema import PDF_MIGRATIONS
from .registry import ActionSpec
from .storage import now, project_snapshot
from .tool_routes import ToolRoute


def manifest(store, identity, kind):
    lane = store.lane("pdf_ocr")
    with lane.connection(read_only=True) as connection:
        row = connection.execute(
            "SELECT * FROM "
            + ("pdf_render" if kind == "render" else "pdf_ocr_run")
            + " WHERE "
            + ("render_id" if kind == "render" else "ocr_id")
            + "=?",
            (identity,),
        ).fetchone()
    if row is None:
        raise LaneError("PDF_DERIVATIVE_MISSING", "Select a derivative from this PDF lane.")
    body = json.loads(lane.read_object(row["manifest_object"]))
    snapshot, _ = read_snapshot(store, row["snapshot_id"])
    if (
        digest(canonical_json_bytes(body)) != identity
        or body["project_id"] != store.project_id
        or body["lane_id"] != "pdf_ocr"
        or body["snapshot_id"] != row["snapshot_id"]
        or body["kind"] != kind
        or body["result"]["input_sha256"] != snapshot["raw_object"]
        or body["created_at"] != row["created_at"]
    ):
        raise LaneError(
            "PDF_DERIVATIVE_INTEGRITY", "The derivative differs from its exact PDF binding."
        )
    return body


def _derive(context, request, kind, *, backend=None):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    snapshot, _ = read_snapshot(store, request.snapshot_id)
    lane = store.lane("pdf_ocr")
    args = {
        **request.model_dump(mode="json"),
        "expected_sha256": snapshot["raw_object"],
        "content_base64": base64.b64encode(lane.read_object(snapshot["raw_object"])).decode(
            "ascii"
        ),
    }
    if backend:
        args["backend"] = backend
    computing = context.computation if kind == 'ocr' and backend == 'rapidocr' else None
    if kind == 'ocr' and backend == 'rapidocr' and computing is None:
        raise LaneError('COMPUTE_ADMISSION_REQUIRED', 'OCR requires engine-owned compute admission.')
    response = (computing or execution).submit("pdf_" + kind, args).result()
    if computing:
        computing.result(response)
    if response["status"] != "ok":
        raise LaneError(
            response.get("code", "PDF_DERIVATIVE_FAILED"),
            "The bounded PDF derivative worker did not complete.",
        )
    generated = json.loads(canonical_json_bytes(response["result"]))
    if generated["input_sha256"] != snapshot["raw_object"]:
        raise LaneError(
            "PDF_DERIVATIVE_BINDING", "The PDF worker returned a different source binding."
        )
    contents = {}
    if kind == "render":
        if [row["page"] for row in generated["files"]] != request.pages:
            raise LaneError(
                "PDF_RENDER_BINDING", "The renderer returned a different page selection."
            )
        for file in generated["files"]:
            raw = base64.b64decode(file.pop("content_base64"), validate=True)
            if (
                digest(raw) != file["sha256"]
                or len(raw) != file["bytes"]
                or file["filename"] != f"page-{file['page']}.png"
            ):
                raise LaneError("PDF_RENDER_BINDING", "A PDF page raster failed its byte binding.")
            contents[file["filename"]] = raw
    else:
        if [row["page"] for row in generated["pages"]] != request.pages:
            raise LaneError("PDF_OCR_BINDING", "OCR returned a different page selection.")
        selected = {row["page"] for row in generated["pages"] if row["ocr_selected"]}
        ids = set()
        for line in generated["lines"]:
            clean = dict(line)
            identity = clean.pop("line_id")
            if (
                identity in ids
                or digest(canonical_json_bytes(clean)) != identity
                or line["page"] not in selected
            ):
                raise LaneError(
                    "PDF_OCR_BINDING",
                    "An OCR line differs from its selected page and immutable payload.",
                )
            ids.add(identity)
        if generated["review_regions"] != [
            row["line_id"] for row in generated["lines"] if row["review_required"]
        ]:
            raise LaneError(
                "PDF_OCR_BINDING", "OCR review regions differ from the emitted confidence evidence."
            )
    body = {
        "schema": "evidence-lane.pdf-derivative.v4",
        "project_id": store.project_id,
        "lane_id": "pdf_ocr",
        "kind": kind,
        "snapshot_id": request.snapshot_id,
        "request": request.model_dump(mode="json"),
        "result": generated,
        "created_at": now(),
    }
    identity = digest(canonical_json_bytes(body))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(["pdf_ocr", "receipts"]):
        apply_migrations(lane, PDF_MIGRATIONS, writer=execution.lease)
        with lane.transaction() as connection:
            obj = lane.put_object(canonical_json_bytes(body))
            if kind == "render":
                for name, raw in contents.items():
                    lane.put_object(raw)
                    _natural(lane, identity, name, raw)
                connection.execute(
                    "INSERT INTO pdf_render VALUES(?,?,?,?)",
                    (identity, request.snapshot_id, obj, body["created_at"]),
                )
            else:
                connection.execute(
                    "INSERT INTO pdf_ocr_run VALUES(?,?,?,?)",
                    (identity, request.snapshot_id, obj, body["created_at"]),
                )
                for line in generated["lines"]:
                    connection.execute(
                        "INSERT INTO pdf_ocr_line VALUES(?,?,?,?,?)",
                        (
                            identity,
                            line["line_id"],
                            line["page"],
                            line["ordinal"],
                            canonical_json_bytes(line).decode(),
                        ),
                    )
                for line_id in generated["review_regions"]:
                    connection.execute(
                        "INSERT INTO pdf_review_region VALUES(?,?)", (identity, line_id)
                    )
                text = "\n\n".join(
                    f"Page {page}\n"
                    + "\n".join(row["text"] for row in generated["lines"] if row["page"] == page)
                    for page in request.pages
                    if page in selected
                )
                _natural(lane, identity, "ocr.txt", text.encode())
            store.append_receipt(
                "pdf_" + kind,
                {
                    "derivative_id": identity,
                    "snapshot_id": request.snapshot_id,
                    "job_id": execution.claim.job_id,
                    "source_bytes_mutated": False,
                },
            )
    return result(
        store,
        "pdf_" + kind,
        {
            kind + "_id": identity,
            "snapshot_id": request.snapshot_id,
            "pages": request.pages,
            "files": generated.get("files", []),
            "ocr_lines": len(generated.get("lines", [])),
            "review_regions": len(generated.get("review_regions", [])),
            "evidence": generated["evidence"],
            "source_bytes_mutated": False,
        },
    )


def render(context, request):
    return _derive(context, request, "render")


def ocr(context, request):
    return _derive(context, request, "ocr", backend="rapidocr")


def ocr_tesseract(context, request):
    return _derive(context, request, "ocr", backend="tesseract")


def read_render(store, request):
    body = manifest(store, request.render_id, "render")
    row = next((row for row in body["result"]["files"] if row["page"] == request.page), None)
    if row is None:
        raise LaneError("PDF_RENDER_PAGE_MISSING", "Select a page in this exact raster derivative.")
    raw = store.lane("pdf_ocr").read_object(row["sha256"])
    if len(raw) != row["bytes"]:
        raise LaneError(
            "PDF_RENDER_INTEGRITY", "The page raster differs from its recorded byte count."
        )
    chunk = raw[request.offset : request.offset + request.max_bytes]
    return {
        **row,
        "render_id": request.render_id,
        "offset": request.offset,
        "content_base64": base64.b64encode(chunk).decode("ascii"),
        "returned_bytes": len(chunk),
        "next_offset": request.offset + len(chunk)
        if request.offset + len(chunk) < len(raw)
        else None,
    }


def ocr_rows(store, identity):
    body = manifest(store, identity, "ocr")
    expected = body["result"]["lines"]
    with store.lane("pdf_ocr").connection(read_only=True) as connection:
        rows = connection.execute(
            "SELECT * FROM pdf_ocr_line WHERE ocr_id=? ORDER BY page,ordinal", (identity,)
        ).fetchall()
        regions = {
            row[0]
            for row in connection.execute(
                "SELECT line_id FROM pdf_review_region WHERE ocr_id=?", (identity,)
            )
        }
    if (
        [json.loads(row["payload_json"]) for row in rows]
        != sorted(expected, key=lambda row: (row["page"], row["ordinal"]))
        or any(
            (row["line_id"], row["page"], row["ordinal"])
            != (
                json.loads(row["payload_json"])["line_id"],
                json.loads(row["payload_json"])["page"],
                json.loads(row["payload_json"])["ordinal"],
            )
            for row in rows
        )
        or regions != set(body["result"]["review_regions"])
    ):
        raise LaneError(
            "PDF_OCR_INTEGRITY",
            "Indexed OCR lines or review regions differ from the immutable result.",
        )
    return body, expected


def _bounded(rows, request):
    result, used = [], 0
    for row in rows[request.offset : request.offset + request.limit]:
        size = len(canonical_json_bytes(row))
        if used + size > request.max_bytes:
            if not result:
                raise LaneError(
                    "PDF_QUERY_ITEM_TOO_LARGE", "Increase max_bytes for this complete OCR item."
                )
            break
        result.append(row)
        used += size
    end = request.offset + len(result)
    return {
        "rows": result,
        "next_offset": end if end < len(rows) else None,
        "truncated": end < len(rows),
    }


def read_ocr(store, request):
    body, rows = ocr_rows(store, request.ocr_id)
    rows = [row for row in rows if request.page is None or row["page"] == request.page]
    return {
        **_bounded(rows, request),
        "ocr_id": request.ocr_id,
        "snapshot_id": body["snapshot_id"],
        "pages": body["result"]["pages"],
        "evidence": body["result"]["evidence"],
    }


def query_ocr(store, request):
    with store.lane("pdf_ocr").connection(read_only=True) as connection:
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT ocr_id FROM pdf_ocr_run WHERE snapshot_id=? ORDER BY created_at,ocr_id LIMIT 129",
                (request.snapshot_id,),
            )
        ]
    if len(ids) > 128:
        raise LaneError(
            "PDF_OCR_QUERY_RUN_BUDGET", "Select an exact OCR result using pdf_ocr_read."
        )
    rows = []
    for identity in ids:
        body, lines = ocr_rows(store, identity)
        if request.collection == "ocr_run":
            rows.append(
                {
                    "ocr_id": identity,
                    "pages": body["result"]["pages"],
                    "evidence": body["result"]["evidence"],
                }
            )
        else:
            for line in lines:
                if request.page is not None and line["page"] != request.page:
                    continue
                if request.collection == "review_region" and not line["review_required"]:
                    continue
                if request.query and request.query.casefold() not in line["text"].casefold():
                    continue
                rows.append({"ocr_id": identity, **line})
    return {
        **_bounded(rows, request),
        "snapshot_id": request.snapshot_id,
        "native_text_preserved": True,
    }


def verify_derivative(context, request, output):
    from PIL import Image

    kind = "render" if isinstance(request, PdfRender) else "ocr"
    identity = output.result[kind + "_id"]
    body = manifest(context.store, identity, kind)
    valid = body["request"] == request.model_dump(mode="json")
    if kind == "render":
        for file in body["result"]["files"]:
            raw = context.store.lane("pdf_ocr").read_object(file["sha256"])
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                valid &= (
                    image.format == "PNG"
                    and image.size == (file["width"], file["height"])
                    and image.width * image.height <= request.max_pixels_per_page
                )
            valid &= len(raw) == file["bytes"]
    else:
        _, lines = ocr_rows(context.store, identity)
        valid &= (
            len(lines) == output.result["ocr_lines"]
            and body["result"]["evidence"]["native_text_preserved"]
        )
        valid &= all(
            bool(re.fullmatch(r"[a-f0-9]{64}", row["native_text_sha256"]))
            for row in body["result"]["pages"]
        )
    return [
        {
            "check_id": name,
            "passed": bool(valid),
            "evidence": {
                "derivative_id": identity,
                "source_bytes_mutated": False,
                "visual_or_ocr_accuracy_verified": False,
            },
        }
        for name in context.requested_checks
    ]


def register_pdf_derivatives(engine):
    for action, model, handler, routes in (
        (
            "pdf_render",
            PdfRender,
            render,
            (ToolRoute("pdf_render.pdfium", render, ("Python", "pypdfium2", "pypdf", "Pillow")),),
        ),
        (
            "pdf_ocr",
            PdfOcr,
            ocr,
            (
                ToolRoute(
                    "pdf_ocr.rapidocr",
                    ocr,
                    ("Python", "pypdf", "pypdfium2", "Pillow", "OpenCV", "RapidOCR_ONNX_Runtime"),
                    argument_values=(("language", ("eng",)),),
                    compute=ComputeContract('pdf_ocr', 'OCR_MEDIA', ('CPU', 'DIRECTML'), 2048, 'rapidocr_lines'),
                ),
                ToolRoute(
                    "pdf_ocr.tesseract",
                    ocr_tesseract,
                    ("Python", "pypdf", "pypdfium2", "Pillow", "pytesseract_Tesseract"),
                    systems=("Windows",),
                ),
            ),
        ),
    ):
        engine.registry.register(
            ActionSpec(
                action,
                "Publish bounded page derivatives tied to immutable PDF source bytes.",
                model,
                PdfResult,
                handler,
                permission="write",
                mutates=True,
                requires_delta=True,
                profile="pdf_ocr",
                workflow="manage-project-sources",
                worker_operations=(action,),
                verification_checks=(action + "_integrity",),
                verifier=verify_derivative,
                tool_routes=routes,
            )
        )
    for action, model, function in [
        ("pdf_render_read", PdfRenderRead, read_render),
        ("pdf_ocr_read", PdfOcrRead, read_ocr),
    ]:

        def make_handler(function, action):
            def handler(context, request):
                store = engine.directory.open(context.project_id)
                with project_snapshot(store.root):
                    return result(store, action, function(store, request))

            return handler

        engine.registry.register(
            ActionSpec(
                action,
                "Read a bounded exact PDF derivative without re-running its tools.",
                model,
                PdfResult,
                make_handler(function, action),
                profile="pdf_ocr",
                workflow="manage-project-sources",
                queryable_in_delta=True,
                cross_project_read=True,
                studio_read=True,
                read_migrations=PDF_MIGRATIONS,
            )
        )

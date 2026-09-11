"""Optional, attributed Docling projections of immutable document snapshots."""

from __future__ import annotations

import base64
import json

from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations
from .pdf_contracts import PdfEnrich, PdfEnrichmentRead
from .pdf_parsers import digest
from .pdf_profile import PdfResult, _calls, read_snapshot, result
from .pdf_schema import PDF_MIGRATIONS
from .registry import ActionSpec
from .storage import now, project_snapshot
from .tool_routes import ToolRoute


def enrich(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    snapshot, _ = read_snapshot(store, request.snapshot_id)
    lane = store.lane("pdf_ocr")
    response = execution.submit(
        "pdf_enrich",
        {
            "logical_name": snapshot["logical_name"],
            "content_base64": base64.b64encode(lane.read_object(snapshot["raw_object"])).decode(),
            "expected_sha256": snapshot["raw_object"],
            "max_output_bytes": request.max_output_bytes,
            "max_pages": request.max_pages,
        },
    ).result()
    if response["status"] != "ok":
        raise LaneError(
            response.get("code", "PDF_ENRICHMENT_FAILED"),
            "The selected rich PDF converter did not complete.",
        )
    converted = json.loads(json.dumps(response["result"]))
    process = converted.pop("process_evidence", None)
    receipt = converted.pop("receipt_sha256", None)
    if (
        digest(canonical_json_bytes(converted)) != receipt
        or converted["status"] != "complete"
        or converted["converter_status"] != "success"
        or converted["source_sha256"] != snapshot["raw_object"]
        or converted["source_mutated"]
        or converted["allow_model_download"]
        or not converted["model_assets_required"]
        or not converted["asset_identity"]
        or not process
    ):
        raise LaneError(
            "PDF_ENRICHMENT_BINDING",
            "The rich conversion differs from its requested bytes or completion contract.",
        )
    body = {
        "schema": "evidence-lane.pdf-enrichment.v4",
        "project_id": store.project_id,
        "lane_id": "pdf_ocr",
        "snapshot_id": request.snapshot_id,
        "process_evidence": process,
        "conversion": {**converted, "receipt_sha256": receipt},
        "created_at": now(),
    }
    identity = digest(canonical_json_bytes(body))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(["pdf_ocr", "receipts"]):
        apply_migrations(lane, PDF_MIGRATIONS, writer=execution.lease)
        with lane.transaction() as connection:
            obj = lane.put_object(canonical_json_bytes(body))
            connection.execute(
                "INSERT INTO docling_extraction VALUES(?,?,?,?)",
                (identity, request.snapshot_id, obj, body["created_at"]),
            )
            store.append_receipt(
                "pdf_enrichment",
                {
                    "enrichment_id": identity,
                    "snapshot_id": request.snapshot_id,
                    "engine": "Docling",
                    "status": "complete",
                    "source_bytes_mutated": False,
                },
            )
    return result(
        store,
        "pdf_enrich",
        {
            "enrichment_id": identity,
            "snapshot_id": request.snapshot_id,
            "engine": "Docling",
            "converter_status": converted["converter_status"],
            "markdown_sha256": converted["markdown_sha256"],
            "source_bytes_mutated": False,
            "model_assets_required": True,
            "native_layout_fidelity": "not_claimed",
        },
    )


def enrichment_manifest(store, identity):
    lane = store.lane("pdf_ocr")
    with lane.connection(read_only=True) as connection:
        row = connection.execute(
            "SELECT * FROM docling_extraction WHERE enrichment_id=?", (identity,)
        ).fetchone()
    if row is None:
        raise LaneError(
            "PDF_ENRICHMENT_MISSING", "Select an exact rich PDF conversion from this PDF/OCR lane."
        )
    body = json.loads(lane.read_object(row["manifest_object"]))
    if (
        digest(canonical_json_bytes(body)) != identity
        or body["project_id"] != store.project_id
        or body["lane_id"] != "pdf_ocr"
        or body["snapshot_id"] != row["snapshot_id"]
        or body["created_at"] != row["created_at"]
    ):
        raise LaneError(
            "PDF_ENRICHMENT_INTEGRITY", "The rich PDF manifest differs from its indexed identity."
        )
    snapshot, _ = read_snapshot(store, body["snapshot_id"])
    if snapshot["raw_object"] != body["conversion"]["source_sha256"]:
        raise LaneError(
            "PDF_ENRICHMENT_INTEGRITY", "The rich conversion belongs to different source bytes."
        )
    return body


def verify_enrichment(context, request, output):
    manifest = enrichment_manifest(context.store, output.result["enrichment_id"])
    return [
        {
            "check_id": name,
            "passed": manifest["snapshot_id"] == request.snapshot_id,
            "evidence": {
                "enrichment_id": output.result["enrichment_id"],
                "converter_status": manifest["conversion"]["converter_status"],
            },
        }
        for name in context.requested_checks
    ]


def register_pdf_enrichment_actions(engine):
    engine.registry.register(
        ActionSpec(
            "pdf_enrich",
            "Add a separate completed Docling projection to an exact PDF snapshot.",
            PdfEnrich,
            PdfResult,
            enrich,
            permission="write",
            mutates=True,
            requires_delta=True,
            profile="pdf_ocr",
            workflow="source-intake",
            worker_operations=("pdf_enrich",),
            verification_checks=("pdf_enrichment_integrity",),
            verifier=verify_enrichment,
            tool_routes=(
                ToolRoute(
                    "pdf_enrich.docling",
                    enrich,
                    ("Python", "Docling", "pypdf"),
                    systems=("Windows",),
                    reason="Separate offline PDF layout and table projection using the configured CPU worker.",
                ),
            ),
        )
    )

    def read(context, request):
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            body = enrichment_manifest(store, request.enrichment_id)
            conversion = body["conversion"]
            text = conversion["markdown"][request.offset : request.offset + request.max_characters]
            end = request.offset + len(text)
            structure = conversion["document"] if request.include_structure else None
            if (
                structure is not None
                and len(canonical_json_bytes(structure)) > request.max_structure_bytes
            ):
                raise LaneError(
                    "PDF_ENRICHMENT_READ_BUDGET",
                    "The structured projection exceeds this explicit result budget.",
                )
            return result(
                store,
                "pdf_enrichment_read",
                {
                    "enrichment_id": request.enrichment_id,
                    "snapshot_id": body["snapshot_id"],
                    "source_sha256": conversion["source_sha256"],
                    "markdown": text,
                    "offset": request.offset,
                    "next_offset": end if end < len(conversion["markdown"]) else None,
                    "complete_markdown_sha256": conversion["markdown_sha256"],
                    "converter_status": conversion["converter_status"],
                    "enrichment_manifest_object": digest(canonical_json_bytes(body)),
                    "document": structure,
                    "native_layout_fidelity": "not_claimed",
                },
            )

    engine.registry.register(
        ActionSpec(
            "pdf_enrichment_read",
            "Read a bounded Markdown excerpt and optional structured projection from an exact completed rich conversion.",
            PdfEnrichmentRead,
            PdfResult,
            read,
            profile="pdf_ocr",
            workflow="source-intake",
            queryable_in_delta=True,
            cross_project_read=True,
            studio_read=True,
            read_migrations=PDF_MIGRATIONS,
        )
    )

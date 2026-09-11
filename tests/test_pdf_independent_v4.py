"""Independent protocol, corruption, immutable export and conversion boundaries."""

import asyncio
import base64
import io
import json
import time

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.pdf_authoring import edit_pdf
from evidence_lane_plugin.pdf_contracts import PdfEdit
from evidence_lane_plugin.pdf_forms import inspect_forms
from evidence_lane_plugin.pdf_parsers import digest, open_reader
from evidence_lane_plugin.plan_runtime import PlanStore

from .pdf_fixtures import pdf_bytes
from .test_native_workflow_bindings import native
from .test_pdf_parsers_v4 import pdf_assets as pdf_assets  # noqa: PLC0414
from .test_pdf_profile_v4 import call, execute, plan
from .test_pdf_profile_v4 import pdf_system as pdf_system  # noqa: PLC0414


@pytest.mark.parametrize("entrypoint", ["adapter", "cli", "package"])
def test_real_stdio_pdf_intake_and_readonly_access(pdf_system, entrypoint):
    engine, store, _ = pdf_system
    plan(pdf_system, ["pdf_index"])
    original = (store.source_root / "fixture.pdf").read_bytes()

    async def run():
        async with native(
            engine.root,
            store.project_id,
            permissions=("read", "write", "tools"),
            entrypoint=entrypoint,
        ) as session:
            catalog = await session.list_tools()
            actual = {row.name for row in catalog.tools if row.name.startswith("pdf_")}
            assert actual == {
                "pdf_index",
                "pdf_refresh",
                "pdf_current",
                "pdf_query",
                "pdf_read",
                "pdf_generate",
                "pdf_edit",
                "pdf_export",
                "pdf_render",
                "pdf_render_read",
                "pdf_ocr",
                "pdf_ocr_read",
                "pdf_enrich",
                "pdf_enrichment_read",
            }
            task = PlanStore(store).task("pdf-0", expected_revision=1)
            admitted = await session.call_tool(
                "delta_enter",
                {
                    "project_id": store.project_id,
                    "expected_revision": 1,
                    "arguments": {
                        "task_id": task.definition.task_id,
                        "plan_revision": 1,
                        "contract_digest": task.contract_digest,
                        "action": "pdf_index",
                        "arguments": {"filename": "fixture.pdf"},
                    },
                },
            )
            body = admitted.structuredContent
            assert body["status"] == "queued", body
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    with store.lane("plan").connection(read_only=True) as connection:
                        row = dict(
                            connection.execute(
                                "SELECT * FROM delta_runs WHERE job_id=?", (body["job_id"],)
                            ).fetchone()
                        )
                except LaneError as error:
                    if error.code != 'PROJECT_RECOVERY_REQUIRED':
                        raise
                    await asyncio.sleep(.03)
                    continue
                if row["state"] in {"verified", "blocked"}:
                    break
                await asyncio.sleep(0.03)
            assert row["state"] == "verified", row
            indexed = json.loads(store.lane("plan").read_object(row["result_object"]))["result"][
                "result"
            ]
        before = {p: p.read_bytes() for p in store.root.rglob("*.sqlite*") if p.is_file()}
        async with native(engine.root, store.project_id, entrypoint=entrypoint) as session:
            read = await session.call_tool(
                "pdf_query",
                {
                    "project_id": store.project_id,
                    "arguments": {
                        "snapshot_id": indexed["snapshot_id"],
                        "collection": "form_field",
                    },
                },
            )
            body = read.structuredContent
            assert body["status"] == "ok", body
            assert {row["name"]: row["value"] for row in body["result"]["result"]["rows"]} == {
                "applicant": "Initial name",
                "consent": "/Off",
                "status": "Review",
            }
            context = await session.call_tool("client_context", {"arguments": {}})
            assert context.structuredContent["result"]["native_task_attestation"] == "not_provided"
            denied = await session.call_tool(
                "pdf_export",
                {
                    "project_id": store.project_id,
                    "arguments": {"snapshot_id": indexed["snapshot_id"], "filename": "denied.pdf"},
                },
            )
            assert denied.structuredContent["status"] != "ok"
        assert not (store.source_root / "denied.pdf").exists()
        assert before == {p: p.read_bytes() for p in store.root.rglob("*.sqlite*") if p.is_file()}
        assert (store.source_root / "fixture.pdf").read_bytes() == original

    with LocalEndpoint(engine):
        asyncio.run(run())


@pytest.mark.parametrize("tamper", ["typed", "fts"])
def test_pdf_queries_reject_corruption(pdf_system, tamper):
    engine, store, _ = pdf_system
    plan(pdf_system, ["pdf_index"])
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    with (
        engine.project_work.mutation(store) as lease,
        lease.coordinated_transaction(["pdf_ocr"]),
        store.lane("pdf_ocr").transaction() as connection,
    ):
        if tamper == "typed":
            connection.execute(
                "UPDATE pdf_form_field SET payload_json=json_set(payload_json,'$.value','forged')"
            )
        else:
            connection.execute("UPDATE pdf_chunk_fts SET text_content='counterfeit'")
    response = call(
        pdf_system,
        "pdf_query",
        {
            "snapshot_id": indexed["snapshot_id"],
            **(
                {"collection": "form_field"}
                if tamper == "typed"
                else {"collection": "text", "query": "counterfeit"}
            ),
        },
    )
    assert response.error.code == "PDF_QUERY_INTEGRITY"


def test_corrupt_object_registration_is_rejected_before_publication(pdf_system):
    engine, store, _ = pdf_system
    plan(pdf_system, ["pdf_index"])
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    before = call(pdf_system, "pdf_current").result
    with (
        pytest.raises(LaneError) as rejected,
        engine.project_work.mutation(store) as lease,
        lease.coordinated_transaction(["pdf_ocr"]),
        store.lane("pdf_ocr").transaction() as connection,
    ):
        connection.execute(
            "UPDATE objects SET size_bytes=size_bytes+1 WHERE digest IN (SELECT text_object FROM pdf_chunk)"
        )
    assert rejected.value.code == "OBJECT_INTEGRITY_FAILED"
    assert call(pdf_system, "pdf_current").result == before
    response = call(
        pdf_system,
        "pdf_query",
        {"snapshot_id": indexed["snapshot_id"], "collection": "text", "query": "searchable"},
    )
    assert response.status == "ok" and response.result["result"]["rows"], response.error


@pytest.mark.parametrize("tamper", ["line", "review_region"])
def test_pdf_ocr_rejects_corrupted_derivative_rows(pdf_system, tamper):
    engine, store, _ = pdf_system
    plan(pdf_system, ["pdf_index", "pdf_ocr"])
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    converted = execute(
        pdf_system,
        "pdf_ocr",
        {"snapshot_id": indexed["snapshot_id"], "pages": [2], "min_confidence": 1.0},
        index=1,
    )
    assert converted["review_regions"] > 0
    with (
        engine.project_work.mutation(store) as lease,
        lease.coordinated_transaction(["pdf_ocr"]),
        store.lane("pdf_ocr").transaction() as connection,
    ):
        if tamper == "line":
            connection.execute(
                "UPDATE pdf_ocr_line SET payload_json=json_set(payload_json,'$.text','forged')"
            )
        else:
            connection.execute("DELETE FROM pdf_review_region")
    response = call(pdf_system, "pdf_ocr_read", {"ocr_id": converted["ocr_id"]})
    assert response.error.code == "PDF_OCR_INTEGRITY"


@pytest.mark.parametrize(
    "boundary", ["stale_edit", "stale_refresh", "occupied_export", "missing_export"]
)
def test_stale_pdf_and_destination_bindings_preserve_bytes(pdf_system, boundary):
    _, store, _ = pdf_system
    action = {
        "stale_edit": "pdf_edit",
        "stale_refresh": "pdf_refresh",
        "occupied_export": "pdf_export",
        "missing_export": "pdf_export",
    }[boundary]
    plan(pdf_system, ["pdf_index", action])
    source = store.source_root / "fixture.pdf"
    original = source.read_bytes()
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    before = call(pdf_system, "pdf_current").result
    if boundary == "stale_edit":
        args = {
            "snapshot_id": indexed["snapshot_id"],
            "expected_sha256": "0" * 64,
            "fields": {"applicant": "Incorrectly admitted"},
        }
        code = "PDF_EDIT_SNAPSHOT_CHANGED"
    elif boundary == "stale_refresh":
        args = {"filename": "fixture.pdf", "expected_snapshot": "0" * 64}
        code = "PDF_SNAPSHOT_CHANGED"
    else:
        args = {
            "snapshot_id": indexed["snapshot_id"],
            "filename": "fixture.pdf" if boundary == "occupied_export" else "missing.pdf",
        }
        if boundary == "missing_export":
            args["expected_sha256"] = digest(original)
        code = "PDF_EXPORT_DESTINATION_CHANGED"
    execute(pdf_system, action, args, index=1, failure=code)
    assert call(pdf_system, "pdf_current").result == before
    assert source.read_bytes() == original and not (store.source_root / "missing.pdf").exists()
    with store.lane("pdf_ocr").connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM pdf_export").fetchone()[0] == 0


def test_replacement_export_preserves_previous_destination_object(pdf_system):
    _, store, _ = pdf_system
    plan(pdf_system, ["pdf_index", "pdf_edit", "pdf_export"])
    source = store.source_root / "fixture.pdf"
    original = source.read_bytes()
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    edited = execute(
        pdf_system,
        "pdf_edit",
        {
            "snapshot_id": indexed["snapshot_id"],
            "expected_sha256": indexed["sha256"],
            "fields": {"applicant": "New value"},
        },
        index=1,
    )
    exported = execute(
        pdf_system,
        "pdf_export",
        {
            "snapshot_id": edited["snapshot_id"],
            "filename": "fixture.pdf",
            "expected_sha256": digest(original),
        },
        index=2,
    )
    assert exported["before_sha256"] == digest(original)
    assert exported["after_sha256"] == digest(source.read_bytes()) == edited["sha256"]
    assert store.lane("pdf_ocr").read_object(exported["before_sha256"]) == original


def test_bounded_bytes_reassemble_original_without_read_mutations(pdf_system):
    _, store, _ = pdf_system
    plan(pdf_system, ["pdf_index"])
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    before = {p: p.read_bytes() for p in store.root.rglob("*.sqlite*") if p.is_file()}
    content, offset = bytearray(), 0
    while True:
        response = call(
            pdf_system,
            "pdf_read",
            {
                "snapshot_id": indexed["snapshot_id"],
                "representation": "original_source",
                "offset": offset,
                "max_bytes": 1024,
            },
        )
        assert response.status == "ok", response.error
        body = response.result["result"]
        content.extend(base64.b64decode(body["content_base64"]))
        offset = body["next_offset"]
        if offset is None:
            break
    assert bytes(content) == (store.source_root / "fixture.pdf").read_bytes()
    assert before == {p: p.read_bytes() for p in store.root.rglob("*.sqlite*") if p.is_file()}


def test_independent_radio_and_multi_choice_preserve_widget_ownership():
    from reportlab.pdfgen.canvas import Canvas

    output = io.BytesIO()
    canvas = Canvas(output)
    canvas.drawString(50, 780, "Independent multi-widget form")
    for index, value in enumerate(["A", "B"]):
        canvas.acroForm.radio(
            name="choice",
            value=value,
            selected=index == 0,
            x=50 + index * 100,
            y=700,
            buttonStyle="circle",
        )
    canvas.acroForm.listbox(
        name="regions",
        options=["North", "South", "West"],
        value=["North"],
        fieldFlags="multiSelect",
        x=50,
        y=520,
    )
    canvas.showPage()
    canvas.save()
    raw = output.getvalue()
    edited, proof = edit_pdf(
        raw,
        PdfEdit(
            snapshot_id="a" * 64,
            expected_sha256=digest(raw),
            fields={"choice": "/B", "regions": ["South", "West"]},
        ),
    )
    forms = inspect_forms(open_reader(edited))
    fields = {row["name"]: row for row in forms["fields"] if row["terminal"]}
    assert fields["choice"]["value"] == "/B"
    assert fields["regions"]["value"] == ["South", "West"]
    assert len(forms["widgets"]) == 3 and all(not row["orphan"] for row in forms["widgets"])
    assert all(row["appearance_present"] for row in forms["widgets"])
    assert proof["canonical_fields_verified"]


@pytest.mark.parametrize("value", ["Asha Rao", "Jos\u00e9", "\u20ac100"])
def test_field_font_coverage_preserves_supported_text(value):
    import pymupdf

    raw = pdf_bytes()
    edited, proof = edit_pdf(
        raw, PdfEdit(snapshot_id="b" * 64, expected_sha256=digest(raw), fields={"applicant": value})
    )
    assert open_reader(edited).get_fields()["applicant"]["/V"] == value
    with pymupdf.open(stream=edited, filetype="pdf") as document:
        assert value in document[0].get_text()
    assert proof["updated_appearance_font_coverage_verified"]


@pytest.mark.parametrize("flatten", [False, True])
def test_unsupported_field_font_cannot_be_accepted_with_question_marks(flatten):
    raw = pdf_bytes()
    with pytest.raises(LaneError, check=lambda error: error.code == "PDF_FORM_FONT_COVERAGE"):
        edit_pdf(
            raw,
            PdfEdit(
                snapshot_id="b" * 64,
                expected_sha256=digest(raw),
                fields={"applicant": "\u65e5\u672c\u8a9e"},
                flatten=flatten,
            ),
        )
    assert open_reader(raw).get_fields()["applicant"]["/V"] == "Initial name"


def test_comb_field_cannot_silently_truncate_the_requested_value():
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, NumberObject

    writer = PdfWriter(clone_from=open_reader(pdf_bytes()))
    field = writer.root_object["/AcroForm"]["/Fields"][0].get_object()
    field[NameObject("/MaxLen")] = NumberObject(4)
    field[NameObject("/Ff")] = NumberObject(1 << 24)
    output = io.BytesIO()
    writer.write(output)
    raw = output.getvalue()
    with pytest.raises(LaneError, check=lambda error: error.code == "PDF_FORM_TEXT_LENGTH"):
        edit_pdf(
            raw,
            PdfEdit(
                snapshot_id="b" * 64, expected_sha256=digest(raw), fields={"applicant": "12345"}
            ),
        )


def test_font_rejection_does_not_publish_an_engine_derivative(pdf_system):
    plan(pdf_system, ["pdf_index", "pdf_edit"])
    indexed = execute(pdf_system, "pdf_index", {"filename": "fixture.pdf"})
    before = call(pdf_system, "pdf_current").result
    execute(
        pdf_system,
        "pdf_edit",
        {
            "snapshot_id": indexed["snapshot_id"],
            "expected_sha256": indexed["sha256"],
            "fields": {"applicant": "\u65e5\u672c\u8a9e"},
        },
        index=1,
        failure="PDF_FORM_FONT_COVERAGE",
    )
    assert call(pdf_system, "pdf_current").result == before

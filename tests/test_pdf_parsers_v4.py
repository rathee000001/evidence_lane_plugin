"""Native PDF, forms and actual offline OCR behavior with independent assertions."""

import base64
import io
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.pdf_authoring import edit_pdf
from evidence_lane_plugin.pdf_contracts import PdfEdit
from evidence_lane_plugin.pdf_forms import inspect_forms
from evidence_lane_plugin.pdf_parsers import digest, open_reader, parse_pdf
from evidence_lane_plugin.pdf_workers import ocr, render

from .pdf_fixtures import pdf_bytes


@pytest.fixture
def pdf_assets(monkeypatch):
    root = Path(__file__).resolve().parents[1] / ".work/qualification/pdf-installation"
    assert (root / "toolchains/asset-installation.v4.json").is_file(), (
        "Prepare the isolated pinned OCR assets first."
    )
    monkeypatch.setenv("EVIDENCE_LANE_STUDIO_ROOT", str(root))
    return root


def test_pdf_native_structure_has_separate_forms_widgets_and_geometric_table():
    raw = pdf_bytes(mixed=True)
    facts = parse_pdf(raw)
    assert facts["features"]["pages"] == 2
    pages = [row for row in facts["items"] if row["kind"] == "page"]
    assert (
        "Native page remains searchable" in pages[0]["text"] and pages[1]["native_characters"] == 0
    )
    table = next(row for row in facts["items"] if row["kind"] == "table")
    assert table["rows"] == [["Measure", "Value"], ["Samples", "24"], ["Status", "Ready"]]
    fields = facts["features"]["forms"]["fields"]
    widgets = facts["features"]["forms"]["widgets"]
    assert len(fields) == len(widgets) == 3 and all(not row["orphan"] for row in widgets)
    assert {row["name"]: row["value"] for row in fields} == {
        "applicant": "Initial name",
        "consent": "/Off",
        "status": "Review",
    }
    assert facts["native_evidence"]["source_sha256"] == digest(raw)


@pytest.mark.parametrize("backend", ["pymupdf", "pypdf", "pdfplumber"])
def test_explicit_native_text_backends_preserve_page_text(backend):
    facts = parse_pdf(pdf_bytes(), native_backend=backend, extract_tables=False)
    assert any("Native page remains searchable" in row["text"] for row in facts["items"])
    assert facts["native_evidence"]["backend"] == backend


def test_form_fill_flatten_and_page_reorder_keep_values_and_original_bytes():
    raw = pdf_bytes(mixed=True)
    request = PdfEdit(
        snapshot_id="a" * 64,
        expected_sha256=digest(raw),
        fields={"applicant": "Asha Rao", "consent": True, "status": "Ready"},
        page_order=[2, 1],
        rotation=90,
    )
    edited, evidence = edit_pdf(raw, request)
    reader = open_reader(edited)
    assert reader.pages[0].extract_text() == "" and "Native page" in reader.pages[1].extract_text()
    assert [page.rotation for page in reader.pages] == [90, 90]
    forms = inspect_forms(reader)
    assert all(widget["page"] == 2 for widget in forms["widgets"])
    assert {row["name"]: row["value"] for row in forms["fields"]} == {
        "applicant": "Asha Rao",
        "consent": "/Yes",
        "status": "Ready",
    }
    assert evidence["canonical_fields_verified"] and evidence["page_widgets_verified"]
    flat, _ = edit_pdf(
        edited, PdfEdit(snapshot_id="a" * 64, expected_sha256=digest(edited), flatten=True)
    )
    flat_reader = open_reader(flat)
    assert "/AcroForm" not in flat_reader.root_object
    assert not inspect_forms(flat_reader)["widgets"]
    assert "Asha Rao" in flat_reader.pages[1].extract_text()
    assert inspect_forms(open_reader(raw))["fields"][0]["value"] == "Initial name"


def test_ambiguous_orphan_is_rejected_and_unique_named_widget_is_repaired():
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=open_reader(pdf_bytes()))
    writer.root_object.pop("/AcroForm")
    output = io.BytesIO()
    writer.write(output)
    orphaned = output.getvalue()
    assert all(row["orphan"] for row in inspect_forms(open_reader(orphaned))["widgets"])
    with pytest.raises(LaneError, check=lambda error: error.code == "PDF_FORM_GRAPH_INCOMPLETE"):
        edit_pdf(
            orphaned,
            PdfEdit(
                snapshot_id="a" * 64, expected_sha256=digest(orphaned), fields={"applicant": "Asha"}
            ),
        )
    repaired, evidence = edit_pdf(
        orphaned,
        PdfEdit(snapshot_id="a" * 64, expected_sha256=digest(orphaned), repair_orphan_widgets=True),
    )
    assert evidence["orphan_widgets_repaired"] == 3 and all(
        not row["orphan"] for row in inspect_forms(open_reader(repaired))["widgets"]
    )


def test_actual_mixed_page_ocr_keeps_native_page_and_reads_only_scan(pdf_assets):
    raw = pdf_bytes(mixed=True)
    result = ocr(
        {
            "content_base64": base64.b64encode(raw).decode(),
            "expected_sha256": digest(raw),
            "pages": [1, 2],
            "mode": "text_poor_pages",
            "native_character_threshold": 10,
            "dpi": 160,
            "language": "eng",
            "min_confidence": 0.95,
            "max_pixels_per_page": 12_000_000,
        }
    )
    assert [row["ocr_selected"] for row in result["pages"]] == [False, True]
    assert (
        result["evidence"]["native_text_preserved"] and not result["evidence"]["models_downloaded"]
    )
    assert {row["page"] for row in result["lines"]} == {2}
    text = " ".join(row["text"] for row in result["lines"])
    assert "4827" in text and "125" in text and "SCANNED PAGE TWO" in text
    assert all(row["polygon_points"] and 0 <= row["confidence"] <= 1 for row in result["lines"])
    assert result["process_evidence"]["memory_limit_mechanism"] == "Windows_Job_Object"
    assert "Native page remains searchable" in open_reader(raw).pages[0].extract_text()


def test_pixel_limit_and_encrypted_source_fail_before_native_processing():
    from pypdf import PdfWriter

    raw = pdf_bytes()
    with pytest.raises(LaneError, check=lambda error: error.code == "PDF_RENDER_PIXEL_BUDGET"):
        render(
            {
                "content_base64": base64.b64encode(raw).decode(),
                "expected_sha256": digest(raw),
                "pages": [1],
                "dpi": 300,
                "include_annotations": True,
                "max_pixels_per_page": 1000,
            }
        )
    writer = PdfWriter(clone_from=open_reader(raw))
    writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(LaneError, check=lambda error: error.code == "PDF_ENCRYPTED_INPUT"):
        parse_pdf(output.getvalue())


def test_actual_native_tesseract_reads_scan_and_poppler_preserves_blank_page(pdf_assets):
    from evidence_lane_plugin.pdf_workers import parse_bytes

    raw = pdf_bytes(mixed=True)
    arguments = {"content_base64": base64.b64encode(raw).decode(), "expected_sha256": digest(raw)}
    parsed = parse_bytes(
        {
            **arguments,
            "logical_name": "fixture.pdf",
            "parse_options": {"max_pages": 2, "extract_tables": True, "native_backend": "poppler"},
        }
    )
    facts = parsed["facts"]
    pages = [row for row in facts["items"] if row["kind"] == "page"]
    assert (
        len(pages) == 2
        and "Native page remains searchable" in pages[0]["text"]
        and not pages[1]["text"].strip()
    )
    assert facts["native_evidence"]["native_runtime"]["tools"][0]["executable_sha256"]
    response = ocr(
        {
            **arguments,
            "backend": "tesseract",
            "pages": [1, 2],
            "mode": "text_poor_pages",
            "native_character_threshold": 10,
            "dpi": 160,
            "language": "eng",
            "min_confidence": 0.95,
            "max_pixels_per_page": 12_000_000,
        }
    )
    text = " ".join(row["text"] for row in response["lines"])
    assert "4827" in text and "125" in text and "SCANNED PAGE TWO" in text
    assert [row["ocr_selected"] for row in response["pages"]] == [False, True]
    assert response["evidence"]["runtime_sha256"] and response["evidence"]["engine"] == "Tesseract"

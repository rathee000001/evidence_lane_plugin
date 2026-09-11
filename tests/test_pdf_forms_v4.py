"""Adversarial PDF form graphs and immutable derivative policy."""

import io

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.pdf_authoring import edit_pdf
from evidence_lane_plugin.pdf_contracts import PdfEdit
from evidence_lane_plugin.pdf_forms import inspect_forms
from evidence_lane_plugin.pdf_parsers import digest, open_reader
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from .pdf_fixtures import pdf_bytes


def mutated(change):
    writer = PdfWriter(clone_from=open_reader(pdf_bytes()))
    change(writer)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def request(raw, **kwargs):
    return PdfEdit(snapshot_id="b" * 64, expected_sha256=digest(raw), **kwargs)


def test_need_appearances_boolean_and_orphan_repair_with_fill():
    raw = mutated(
        lambda writer: writer.root_object["/AcroForm"].__setitem__(
            NameObject("/NeedAppearances"), BooleanObject(False)
        )
    )
    assert inspect_forms(open_reader(raw))["need_appearances"] is False
    orphaned = mutated(lambda writer: writer.root_object.pop("/AcroForm"))
    edited, _ = edit_pdf(
        orphaned,
        request(
            orphaned,
            repair_orphan_widgets=True,
            fields={"applicant": "Recovered name", "consent": True, "status": "Ready"},
        ),
    )
    result = inspect_forms(open_reader(edited))
    assert {x["name"]: x["value"] for x in result["fields"]} == {
        "applicant": "Recovered name",
        "consent": "/Yes",
        "status": "Ready",
    }
    assert all(x["appearance_present"] and not x["orphan"] for x in result["widgets"])
    assert not result["need_appearances"]


def test_xfa_and_signed_derivative_are_explicit_and_separate():
    raw = mutated(
        lambda writer: writer.root_object["/AcroForm"].__setitem__(
            NameObject("/XFA"), TextStringObject("fixture")
        )
    )
    with pytest.raises(LaneError, check=lambda e: e.code == "PDF_XFA_EDIT_UNSUPPORTED"):
        edit_pdf(raw, request(raw, metadata={"/Title": "Revised"}))

    def signature(writer):
        field = writer.root_object["/AcroForm"]["/Fields"][0].get_object()
        field[NameObject("/FT")] = NameObject("/Sig")
        field[NameObject("/V")] = DictionaryObject(
            {
                NameObject("/ByteRange"): ArrayObject(
                    [NumberObject(0), NumberObject(1), NumberObject(2), NumberObject(3)]
                )
            }
        )

    raw = mutated(signature)
    assert inspect_forms(open_reader(raw))["signed"]
    with pytest.raises(
        LaneError, check=lambda e: e.code == "PDF_SIGNED_DERIVATIVE_REQUIRES_SELECTION"
    ):
        edit_pdf(raw, request(raw, metadata={"/Title": "Derivative"}))
    edited, evidence = edit_pdf(
        raw, request(raw, metadata={"/Title": "Derivative"}, allow_signed_derivative=True)
    )
    assert open_reader(edited).metadata.title == "Derivative"
    assert evidence["signed_source"] and evidence["signature_preserved_as_valid"] is False


@pytest.mark.parametrize("kind", ["duplicate", "cycle", "missing_widget", "orphan_duplicate"])
def test_invalid_form_ownership_fails_closed(kind):
    def change(writer):
        fields = writer.root_object["/AcroForm"]["/Fields"]
        if kind == "duplicate":
            fields[1].get_object()[NameObject("/T")] = TextStringObject("applicant")
        elif kind == "cycle":
            fields[0].get_object()[NameObject("/Kids")] = ArrayObject([fields[0]])
        elif kind == "missing_widget":
            writer.pages[0]["/Annots"].pop(0)
        else:
            fields[1].get_object()[NameObject("/T")] = TextStringObject("applicant")
            writer.root_object.pop("/AcroForm")

    raw = mutated(change)
    codes = {
        "duplicate": "PDF_FORM_DUPLICATE_NAME",
        "cycle": "PDF_FORM_TREE_AMBIGUOUS",
        "missing_widget": "PDF_FORM_GRAPH_INCOMPLETE",
        "orphan_duplicate": "PDF_ORPHAN_REPAIR_AMBIGUOUS",
    }
    with pytest.raises(LaneError, check=lambda e: e.code == codes[kind]):
        edit_pdf(
            raw,
            request(
                raw,
                metadata={"/Title": "Rejected"},
                repair_orphan_widgets=kind == "orphan_duplicate",
            ),
        )


@pytest.mark.parametrize(
    "fields,code",
    [
        ({"missing": "x"}, "PDF_FORM_FIELD_UNKNOWN"),
        ({"status": "Absent"}, "PDF_FORM_CHOICE_VALUE_INVALID"),
        ({"consent": "/Unrecognized"}, "PDF_FORM_BUTTON_VALUE_INVALID"),
    ],
)
def test_unknown_or_invalid_field_values_are_rejected(fields, code):
    raw = pdf_bytes()
    with pytest.raises(LaneError, check=lambda e: e.code == code):
        edit_pdf(raw, request(raw, fields=fields))

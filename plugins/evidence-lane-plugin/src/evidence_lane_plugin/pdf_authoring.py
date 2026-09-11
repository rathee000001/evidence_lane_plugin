"""Closed PDF authoring and immutable form/page derivatives with appearance checks."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from xml.sax.saxutils import escape

from .pdf_forms import deref, fail, inspect_forms, reference, repair_orphans
from .pdf_parsers import MAX_BYTES, open_reader


def generate_pdf(request):
    import reportlab
    from PIL import Image
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph, Table, TableStyle

    font_name = "EvidenceLaneVera"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont(font_name, str(Path(reportlab.__file__).parent / "fonts/Vera.ttf"))
        )
    supported = pdfmetrics.getFont(font_name).face.charToGlyph

    def supported_text(text, *, form=False):
        if any(
            (ord(char) not in supported if not form else ord(char) > 255)
            for char in text
            if char not in "\n\r\t"
        ):
            fail("AUTHOR_FONT_COVERAGE")
        return escape(text).replace("\n", "<br/>")

    output = io.BytesIO()
    canvas = Canvas(output, pageCompression=1, invariant=1)
    canvas.setTitle(request.title)
    canvas.setAuthor(request.author)
    for page in request.pages:
        canvas.setPageSize((page.width, page.height))
        for item in page.elements:
            x, y = item.x, page.height - item.y - item.height
            color = HexColor(item.color)
            canvas.saveState()
            canvas.setFillColor(color)
            canvas.setStrokeColor(color)
            if item.background:
                canvas.setFillColor(HexColor(item.background))
                canvas.rect(x, y, item.width, item.height, stroke=0, fill=1)
                canvas.setFillColor(color)
            if item.kind in {"text", "paragraph", "table"}:
                style = ParagraphStyle(
                    "element",
                    fontName=font_name,
                    fontSize=item.font_size,
                    leading=item.font_size * 1.25,
                    textColor=color,
                )
                if item.kind == "table":
                    rows = [
                        [Paragraph(supported_text(cell), style) for cell in row]
                        for row in item.rows
                    ]
                    flowable = Table(
                        rows, colWidths=[item.width / len(rows[0])] * len(rows[0]), hAlign="LEFT"
                    )
                    flowable.setStyle(
                        TableStyle(
                            [
                                ("GRID", (0, 0), (-1, -1), 0.5, color),
                                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                ("TOPPADDING", (0, 0), (-1, -1), 5),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                            ]
                        )
                    )
                else:
                    flowable = Paragraph(supported_text(item.text), style)
                width, height = flowable.wrap(item.width, item.height)
                if width > item.width + 0.01 or height > item.height + 0.01:
                    fail("AUTHOR_CONTENT_OVERFLOW")
                flowable.drawOn(canvas, x, y + item.height - height)
            elif item.kind == "rectangle":
                canvas.rect(x, y, item.width, item.height, fill=int(bool(item.background)))
            elif item.kind == "image":
                raw = base64.b64decode(item.image_base64, validate=True)
                if len(raw) > 8_388_608:
                    fail("AUTHOR_IMAGE_BUDGET")
                with Image.open(io.BytesIO(raw)) as image:
                    if (
                        image.format not in {"PNG", "JPEG"}
                        or image.width * image.height > 25_000_000
                    ):
                        fail("AUTHOR_IMAGE_BUDGET")
                    image.load()
                    canvas.drawImage(
                        ImageReader(image),
                        x,
                        y,
                        width=item.width,
                        height=item.height,
                        preserveAspectRatio=True,
                        anchor="c",
                        mask="auto",
                    )
            elif item.kind == "form_checkbox":
                if abs(item.width - item.height) > 0.01:
                    fail("AUTHOR_CHECKBOX_SQUARE")
                canvas.acroForm.checkbox(
                    name=item.field_name,
                    x=x,
                    y=y,
                    size=item.width,
                    checked=item.checked,
                    textColor=color,
                    borderColor=color,
                    fieldFlags="",
                    forceBorder=True,
                )
            else:
                supported_text(item.text, form=True)
                common = {
                    "name": item.field_name,
                    "value": item.text,
                    "x": x,
                    "y": y,
                    "width": item.width,
                    "height": item.height,
                    "fontName": "Helvetica",
                    "fontSize": item.font_size,
                    "textColor": color,
                    "borderColor": color,
                    "forceBorder": True,
                }
                if item.kind == "form_text":
                    if item.font_size * 1.4 > item.height:
                        fail("AUTHOR_CONTENT_OVERFLOW")
                    canvas.acroForm.textfield(
                        **common, fieldFlags="multiline" if "\n" in item.text else ""
                    )
                elif item.kind == "form_choice":
                    for option in item.options:
                        supported_text(option, form=True)
                    canvas.acroForm.choice(**common, options=item.options, fieldFlags="combo")
            canvas.restoreState()
        canvas.showPage()
    canvas.save()
    raw = output.getvalue()
    if len(raw) > MAX_BYTES:
        fail("AUTHOR_OUTPUT_BUDGET")
    reopened = open_reader(raw, len(request.pages))
    forms = inspect_forms(reopened)
    requested = [
        item for page in request.pages for item in page.elements if item.kind.startswith("form_")
    ]
    canonical = {row["name"]: row for row in forms["fields"] if row["terminal"]}
    if set(canonical) != {item.field_name for item in requested} or len(forms["widgets"]) != len(
        requested
    ):
        fail("AUTHOR_FORM_VERIFICATION")
    for item in requested:
        expected = (
            ("/Yes" if item.checked else "/Off") if item.kind == "form_checkbox" else item.text
        )
        if canonical[item.field_name]["value"] != expected:
            fail("AUTHOR_FORM_VERIFICATION")
    if any(row["orphan"] or not row["appearance_present"] for row in forms["widgets"]):
        fail("AUTHOR_FORM_VERIFICATION")
    return raw, {
        "engine": "ReportLab",
        "interactive": bool(requested),
        "canonical_fields_verified": True,
        "page_widgets_verified": True,
        "appearances_present": True,
        "layout_review": "required",
        "font_coverage": "embedded_Vera_for_content_WinAnsi_Helvetica_for_forms",
    }


def _update_form_values(writer, values, *, flatten=False):
    # The pinned pypdf 6.15 adapter verifies the font actually emitted in each
    # appearance. A correct canonical /V and a nonempty /AP are not enough:
    # pypdf can otherwise replace unsupported glyphs with question marks.
    from pypdf._font import Font
    from pypdf.generic import ContentStream

    before = inspect_forms(writer)
    fields = {row["name"]: row for row in before["fields"] if row["terminal"]}
    for name, value in values.items():
        field = fields[name]
        if (
            field["field_type"] == "/Tx"
            and field["max_length"] is not None
            and (field["max_length"] < 0 or len(value) > field["max_length"])
        ):
            fail("FORM_TEXT_LENGTH")
    writer.update_page_form_field_values(None, values, auto_regenerate=False, flatten=flatten)
    widgets = {row["reference"]: row for row in inspect_forms(writer)["widgets"]}
    for page in writer.pages:
        for raw in deref(page.get("/Annots", [])):
            row = widgets.get(reference(raw))
            if not row or row["name"] not in values or row["field_type"] not in {"/Tx", "/Ch"}:
                continue
            value = values[row["name"]]
            texts = value if isinstance(value, list) else [value]
            if row["field_type"] == "/Ch":
                labels = {
                    option[0]: option[1]
                    for option in fields[row["name"]]["options"]
                    if isinstance(option, list) and len(option) == 2
                }
                texts = [labels.get(text, text) for text in texts]
            text = "".join(texts).replace("\n", "").replace("\r", "").replace("\t", "")
            normal = deref(deref(deref(raw).get("/AP", {})).get("/N"))
            if normal is None or not hasattr(normal, "get_data"):
                fail("FORM_APPEARANCE_FONT_UNVERIFIED")
            resources = deref(deref(normal.get("/Resources", {})).get("/Font", {}))
            selected = {
                str(operands[0])
                for operands, operator in ContentStream(normal, writer).operations
                if operator == b"Tf" and operands
            }
            if not selected or any(name not in resources for name in selected):
                fail("FORM_APPEARANCE_FONT_UNVERIFIED")
            if not any(
                Font.from_font_resource(resources[name]).can_encode(text) for name in selected
            ):
                fail("FORM_FONT_COVERAGE")


def edit_pdf(content, request):
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, NameObject

    reader = open_reader(content)
    before = inspect_forms(reader)
    if before["xfa"]:
        fail("XFA_EDIT_UNSUPPORTED")
    if before["signed"] and not request.allow_signed_derivative:
        fail("SIGNED_DERIVATIVE_REQUIRES_SELECTION")
    writer = PdfWriter(clone_from=reader)
    repaired = repair_orphans(writer) if request.repair_orphan_widgets else 0
    inspection = inspect_forms(writer)
    if inspection["missing_page_widgets"] or any(row["orphan"] for row in inspection["widgets"]):
        fail("FORM_GRAPH_INCOMPLETE")
    fields = {row["name"]: row for row in inspection["fields"] if row["terminal"]}
    if set(request.fields) - set(fields):
        fail("FORM_FIELD_UNKNOWN")
    updates = {}
    for name, value in request.fields.items():
        field = fields[name]
        if field["flags"] & 1:
            fail("FORM_FIELD_READ_ONLY")
        if field["field_type"] == "/Btn":
            if field["flags"] & (1 << 16):
                fail("FORM_PUSHBUTTON_UNSUPPORTED")
            states = {
                state
                for row in inspection["widgets"]
                if row["name"] == name
                for state in row["appearance_states"]
                if state != "/Off"
            }
            if isinstance(value, bool):
                if field["flags"] & (1 << 15) or len(states) != 1:
                    fail("FORM_BUTTON_VALUE_AMBIGUOUS")
                value = next(iter(states)) if value else "/Off"
            if not isinstance(value, str) or value not in states | {"/Off"}:
                fail("FORM_BUTTON_VALUE_INVALID")
        elif field["field_type"] == "/Tx":
            if not isinstance(value, str):
                fail("FORM_TEXT_VALUE_INVALID")
        elif field["field_type"] == "/Ch":
            options = {row[0] if isinstance(row, list) else row for row in field["options"]}
            if not isinstance(value, (str, list)) or (
                isinstance(value, list) and not field["flags"] & (1 << 21)
            ):
                fail("FORM_CHOICE_VALUE_INVALID")
            if set(value if isinstance(value, list) else [value]) - options:
                fail("FORM_CHOICE_VALUE_INVALID")
        else:
            fail("FORM_FIELD_TYPE_UNSUPPORTED")
        updates[name] = value
    if updates:
        _update_form_values(writer, updates)
    if request.metadata:
        writer.add_metadata(request.metadata)
    if request.page_order is not None:
        if set(request.page_order) != set(range(1, len(writer.pages) + 1)):
            fail("PAGE_ORDER_MUST_PRESERVE_ALL_PAGES")
        # Reorder references in the existing page tree: forms/links retain exact
        # page-object ownership. Do not clone pages and silently lose field kids.
        pages_root = deref(writer.root_object["/Pages"])
        pages = list(writer.pages)
        pages_root[NameObject("/Kids")] = ArrayObject(
            [pages[i - 1].indirect_reference for i in request.page_order]
        )
        for page in pages:
            page[NameObject("/Parent")] = pages_root.indirect_reference
        writer.flattened_pages = [pages[i - 1] for i in request.page_order]
    if request.rotation:
        for page in writer.pages:
            page.rotate(request.rotation)
    if request.flatten:
        # Every retained form value is painted, including fields not edited in
        # this operation. Widget annotations and the canonical tree are removed.
        current = inspect_forms(writer)
        flatten_values = {
            row["name"]: row["value"]
            for row in current["fields"]
            if row["terminal"]
            and row["field_type"] in {"/Tx", "/Ch", "/Btn"}
            and row["value"] is not None
        }
        if any(
            row["field_type"] not in {"/Tx", "/Ch", "/Btn"}
            for row in current["fields"]
            if row["terminal"]
        ):
            fail("FLATTEN_FIELD_TYPE_UNSUPPORTED")
        if flatten_values:
            _update_form_values(writer, flatten_values, flatten=True)
        writer.remove_annotations(subtypes="/Widget")
        writer.root_object.pop("/AcroForm", None)
    output = io.BytesIO()
    writer.write(output)
    raw = output.getvalue()
    if len(raw) > MAX_BYTES:
        fail("AUTHOR_OUTPUT_BUDGET")
    reopened = open_reader(raw)
    after = inspect_forms(reopened)
    canonical = {row["name"]: row for row in after["fields"] if row["terminal"]}
    if request.flatten:
        if after["fields"] or after["widgets"] or "/AcroForm" in reopened.root_object:
            fail("FORM_EDIT_VERIFICATION")
    else:
        if set(canonical) != set(fields) or len(after["widgets"]) != len(inspection["widgets"]):
            fail("FORM_EDIT_VERIFICATION")
        for name, field in fields.items():
            if canonical[name]["value"] != updates.get(name, field["value"]):
                fail("FORM_EDIT_VERIFICATION")
        if after["missing_page_widgets"] or any(
            row["orphan"] or (row["name"] in updates and not row["appearance_present"])
            for row in after["widgets"]
        ):
            fail("FORM_EDIT_VERIFICATION")
        for widget in after["widgets"]:
            if widget["field_type"] == "/Btn" and widget["name"] in updates:
                selected = updates[widget["name"]]
                expected = selected if selected in widget["appearance_states"] else "/Off"
                if widget["appearance_state"] != expected:
                    fail("FORM_EDIT_VERIFICATION")
    if any(
        (reopened.metadata or {}).get(name) != value for name, value in request.metadata.items()
    ):
        fail("METADATA_EDIT_VERIFICATION")
    return raw, {
        "engine": "pypdf",
        "fields_updated": sorted(updates),
        "orphan_widgets_repaired": repaired,
        "interactive": bool(after["widgets"]),
        "flattened": request.flatten,
        "canonical_fields_verified": True,
        "page_widgets_verified": True,
        "updated_appearance_font_coverage_verified": True,
        "source_bytes_mutated": False,
        "signed_source": before["signed"],
        "signature_preserved_as_valid": False,
        "layout_review": "required",
    }

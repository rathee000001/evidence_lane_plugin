"""Native PDF evidence with separate canonical forms, widgets and page text."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import math

from .errors import LaneError
from .hashing import canonical_json_bytes
from .pdf_forms import deref, inspect_forms, scalar

MAX_BYTES = 16_777_216
KINDS = (
    "page",
    "text_block",
    "text_line",
    "image",
    "table",
    "link",
    "annotation",
    "form_field",
    "widget",
    "outline",
    "attachment",
)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def fail(code):
    raise LaneError(
        "PDF_" + code, "The selected PDF does not satisfy its format and resource contract."
    )


def open_reader(content, max_pages=500):
    from pypdf import PdfReader

    if not 1 <= len(content) <= MAX_BYTES or not content[:1024].lstrip().startswith(b"%PDF-"):
        fail("INPUT_INVALID")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            fail("ENCRYPTED_INPUT")
        if not 1 <= len(reader.pages) <= max_pages:
            fail("PAGE_BUDGET")
        return reader
    except LaneError:
        raise
    except Exception:  # noqa: BLE001 - do not expose vendor diagnostics or PDF content
        fail("PARSE_FAILED")


def _box(value):
    values = [round(float(item), 5) for item in value]
    if not all(math.isfinite(item) and abs(item) <= 1_000_000 for item in values):
        fail("GEOMETRY_INVALID")
    return values


def parse_pdf(content, *, max_pages=100, extract_tables=True, native_backend="pymupdf"):
    reader = open_reader(content, max_pages)
    forms = inspect_forms(reader)
    items, total_bytes = [], 0

    def add(kind, ordinal, page=0, text="", **payload):
        nonlocal total_bytes
        if kind not in KINDS or len(items) >= 30_000 or len(text) > 1_000_000:
            fail("STRUCTURE_BUDGET")
        part = f"page/{page}" if page else "document"
        row = {
            "kind": kind,
            "ordinal": ordinal,
            "part": part,
            "page": page,
            "text": text,
            **payload,
        }
        row["item_id"] = digest(canonical_json_bytes([kind, ordinal, part]))
        total_bytes += len(canonical_json_bytes(row))
        if total_bytes > 8_388_608:
            fail("STRUCTURE_BUDGET")
        items.append(row)

    if native_backend not in {"pymupdf", "pypdf", "pdfplumber", "poppler"}:
        fail("BACKEND_INVALID")
    page_texts = {}
    if native_backend == "pymupdf":
        import pymupdf

        with pymupdf.open(stream=content, filetype="pdf") as document:
            if len(document) != len(reader.pages):
                fail("PAGE_COUNT_DISAGREEMENT")
            for index, page in enumerate(document, 1):
                text = page.get_text("text", sort=True)
                page_texts[index] = text
                add(
                    "page",
                    index,
                    index,
                    text,
                    width=page.rect.width,
                    height=page.rect.height,
                    rotation=page.rotation,
                    coordinate_space="unrotated_top_left_pdf_points",
                    native_characters=len(text.strip()),
                    mediabox=_box(page.mediabox),
                    cropbox=_box(page.cropbox),
                )
                # Text-only flags prevent inflating every embedded image into memory.
                layout = page.get_text(
                    "dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
                )
                line_ordinal = 0
                for block_ordinal, block in enumerate(layout["blocks"]):
                    if block["type"] != 0:
                        continue
                    block_text = "\n".join(
                        "".join(span["text"] for span in line["spans"]) for line in block["lines"]
                    )
                    add(
                        "text_block",
                        block_ordinal,
                        index,
                        block_text,
                        bbox=_box(block["bbox"]),
                        backend=native_backend,
                    )
                    for line in block["lines"]:
                        text = "".join(span["text"] for span in line["spans"])
                        add(
                            "text_line",
                            line_ordinal,
                            index,
                            text,
                            bbox=_box(line["bbox"]),
                            block_ordinal=block_ordinal,
                            direction=_box(line["dir"]),
                            spans=[
                                {
                                    "text": span["text"],
                                    "font": span["font"],
                                    "size": span["size"],
                                    "bbox": _box(span["bbox"]),
                                }
                                for span in line["spans"]
                            ],
                        )
                        line_ordinal += 1
                for ordinal, image in enumerate(page.get_image_info(hashes=False, xrefs=False)):
                    add(
                        "image",
                        ordinal,
                        index,
                        bbox=_box(image["bbox"]),
                        width=image["width"],
                        height=image["height"],
                        colorspace=image["colorspace"],
                        extracted=False,
                    )
                for ordinal, link in enumerate(page.get_links()):
                    add(
                        "link",
                        ordinal,
                        index,
                        str(link.get("uri", "")),
                        bbox=_box(link["from"]),
                        target_page=link.get("page"),
                        uri=link.get("uri"),
                        kind_code=link["kind"],
                        followed=False,
                    )
                for ordinal, annot in enumerate(page.annots() or []):
                    add(
                        "annotation",
                        ordinal,
                        index,
                        annot.info.get("content", ""),
                        annotation_type=annot.type[1],
                        bbox=_box(annot.rect),
                        info=annot.info,
                    )
            for ordinal, entry in enumerate(document.get_toc(simple=True)):
                add(
                    "outline",
                    ordinal,
                    max(0, entry[2]),
                    entry[1],
                    depth=entry[0],
                    destination_page=entry[2],
                )
            for ordinal, name in enumerate(document.embfile_names()):
                if ordinal >= 1000:
                    fail("ATTACHMENT_BUDGET")
                info = document.embfile_info(name)
                add(
                    "attachment", ordinal, text=name, metadata=info, extracted=False, executed=False
                )
    else:
        plumber = None
        native_details = None
        if native_backend == "poppler":
            from .pdf_native import poppler_text

            poppler_pages, native_details = poppler_text(content, len(reader.pages))
        if native_backend == "pdfplumber":
            import pdfplumber

            plumber = pdfplumber.open(io.BytesIO(content))
        try:
            for index, page in enumerate(reader.pages, 1):
                text = (
                    poppler_pages[index - 1]
                    if native_backend == "poppler"
                    else plumber.pages[index - 1].extract_text()
                    if plumber
                    else page.extract_text()
                ) or ""
                page_texts[index] = text
                add(
                    "page",
                    index,
                    index,
                    text,
                    width=float(page.mediabox.width),
                    height=float(page.mediabox.height),
                    rotation=page.rotation,
                    native_characters=len(text.strip()),
                    coordinate_space="native_text_only",
                )
                if text:
                    add("text_block", 0, index, text, bbox=None, backend=native_backend)
        finally:
            if plumber:
                plumber.close()
    if extract_tables:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(content)) as document:
            for number, page in enumerate(document.pages, 1):
                for ordinal, table in enumerate(page.find_tables()):
                    if ordinal >= 100:
                        fail("TABLE_BUDGET")
                    rows = table.extract()
                    if len(rows) > 2000 or any(len(row) > 200 for row in rows):
                        fail("TABLE_BUDGET")
                    add(
                        "table",
                        ordinal,
                        number,
                        "\n".join("\t".join(cell or "" for cell in row) for row in rows),
                        bbox=_box(table.bbox),
                        rows=rows,
                        method="pdfplumber_geometry",
                        semantic_table_verified=False,
                    )
    for ordinal, row in enumerate(forms["fields"]):
        add("form_field", ordinal, text=row["name"], **row)
    for ordinal, row in enumerate(forms["widgets"]):
        row = dict(row)
        add("widget", ordinal, row.pop("page"), text=row["name"], **row)
    metadata = {str(key): scalar(value) for key, value in (reader.metadata or {}).items()}
    root = reader.root_object
    names = deref(root.get("/Names", {}))
    features = {
        "pages": len(reader.pages),
        "metadata": metadata,
        "forms": forms,
        "active_document_actions": "/OpenAction" in root or "/AA" in root or "/JavaScript" in names,
        "tagged": "/StructTreeRoot" in root,
        "pdf_version": reader.pdf_header,
    }
    versions = {
        name: importlib.metadata.version(name)
        for name in dict.fromkeys(
            [
                "pypdf",
                *([native_backend] if native_backend != "poppler" else []),
                *(["pdfplumber"] if extract_tables else []),
            ]
        )
    }
    result = {
        "schema": "evidence-lane.pdf-facts.v4",
        "items": items,
        "features": features,
        "native_evidence": {
            "backend": native_backend,
            "versions": versions,
            "source_sha256": digest(content),
            **({"native_runtime": native_details} if native_backend == "poppler" else {}),
        },
        "parse_options": {
            "max_pages": max_pages,
            "extract_tables": extract_tables,
            "native_backend": native_backend,
        },
        "fidelity": {
            "native_text": True,
            "geometry": native_backend == "pymupdf",
            "ocr_performed": False,
            "forms_canonical_and_widgets_inspected": True,
            "layout_verified": False,
            "source_bytes_preserved": True,
            "actions_executed": False,
        },
        "limitations": [
            "Native text order follows the selected parser; scanned pages require explicit page OCR.",
            "Tables are geometric extraction hypotheses, not spreadsheet cells.",
            "Signatures are detected but their cryptographic validity is not evaluated.",
            "Attachment contents and document actions are never executed or followed.",
        ],
    }
    if len(canonical_json_bytes(result)) > 12_582_912:
        fail("STRUCTURE_BUDGET")
    # The facts contract is plain finite JSON; vendor objects cannot escape it.
    return json.loads(canonical_json_bytes(result))

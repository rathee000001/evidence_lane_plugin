"""PDF source, native/OCR evidence, interactive form and derivative contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .registry import Contract

DIGEST = r"^[0-9a-f]{64}$"


class PdfSelection(Contract):
    lane_id: Literal["pdf_ocr"] = "pdf_ocr"


class PdfIndex(PdfSelection):
    text_backend: Literal["auto", "pymupdf", "pdfplumber", "pypdf", "poppler"] = "auto"
    filename: str = Field(min_length=1, max_length=1000)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    max_file_bytes: int = Field(default=8_388_608, ge=1, le=16_777_216)
    max_pages: int = Field(default=100, ge=1, le=500)
    extract_tables: bool = True


class PdfSnapshot(PdfSelection):
    snapshot_id: str = Field(pattern=DIGEST)


class PdfEnrich(PdfSnapshot):
    max_pages: int = Field(default=25, ge=1, le=50)
    max_output_bytes: int = Field(default=1_048_576, ge=4096, le=2_097_152)


class PdfEnrichmentRead(PdfSelection):
    enrichment_id: str = Field(pattern=DIGEST)
    offset: int = Field(default=0, ge=0, le=2_097_152)
    max_characters: int = Field(default=32_768, ge=1, le=65_536)
    include_structure: bool = False
    max_structure_bytes: int = Field(default=131_072, ge=4096, le=524_288)


class PdfQuery(PdfSnapshot):
    match_mode: Literal['all', 'any'] = 'all'
    collection: Literal[
        "metadata",
        "text",
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
        "ocr_run",
        "ocr_line",
        "review_region",
    ] = "text"
    query: str | None = Field(default=None, min_length=1, max_length=500)
    page: int | None = Field(default=None, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class PdfRead(PdfSnapshot):
    representation: Literal["pdf", "original_source"] = "pdf"
    offset: int = Field(default=0, ge=0, le=16_777_216)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)


class PdfExport(PdfSnapshot):
    filename: str = Field(min_length=1, max_length=1000)
    expected_sha256: str | None = Field(default=None, pattern=DIGEST)


class PdfPageSelection(PdfSnapshot):
    pages: list[int] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def distinct_pages(self):
        if any(type(page) is not int or not 1 <= page <= 500 for page in self.pages):
            raise ValueError("Select one-based page numbers within the admitted PDF bound.")
        if len(set(self.pages)) != len(self.pages):
            raise ValueError("Each selected page must be distinct.")
        return self


class PdfRender(PdfPageSelection):
    dpi: int = Field(default=144, ge=36, le=300)
    include_annotations: bool = True
    max_pixels_per_page: int = Field(default=12_000_000, ge=1000, le=25_000_000)


class PdfRenderRead(PdfSelection):
    render_id: str = Field(pattern=DIGEST)
    page: int = Field(ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=16_777_216)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)


class PdfOcr(PdfPageSelection):
    mode: Literal["text_poor_pages", "all_selected_pages"] = "text_poor_pages"
    native_character_threshold: int = Field(default=10, ge=0, le=1000)
    dpi: int = Field(default=144, ge=72, le=300)
    language: str = Field(default="eng", pattern=r"^[a-z]{3}(?:\+[a-z]{3}){0,3}$")
    min_confidence: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    max_pixels_per_page: int = Field(default=12_000_000, ge=1000, le=25_000_000)


class PdfOcrRead(PdfSelection):
    ocr_id: str = Field(pattern=DIGEST)
    page: int | None = Field(default=None, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=50, ge=1, le=200)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class PdfRectangle(Contract):
    x: float = Field(ge=0, le=2000, allow_inf_nan=False)
    y: float = Field(ge=0, le=2000, allow_inf_nan=False)
    width: float = Field(gt=0, le=2000, allow_inf_nan=False)
    height: float = Field(gt=0, le=2000, allow_inf_nan=False)


class PdfElement(PdfRectangle):
    kind: Literal[
        "text",
        "paragraph",
        "table",
        "image",
        "rectangle",
        "form_text",
        "form_checkbox",
        "form_choice",
    ]
    text: str = Field(default="", max_length=100_000)
    font_size: float = Field(default=11, ge=4, le=144, allow_inf_nan=False)
    color: str = Field(default="#17202A", pattern=r"^#[0-9a-fA-F]{6}$")
    background: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    rows: list[list[str]] | None = Field(default=None, min_length=1, max_length=100)
    image_base64: str | None = Field(default=None, min_length=1, max_length=11_184_812)
    field_name: str | None = Field(default=None, min_length=1, max_length=180)
    options: list[str] = Field(default_factory=list, max_length=100)
    checked: bool = False

    @model_validator(mode="after")
    def typed_content(self):
        if self.kind == "table":
            if not self.rows or not self.rows[0] or len(self.rows[0]) > 30:
                raise ValueError("A table requires one to thirty columns.")
            if any(len(row) != len(self.rows[0]) for row in self.rows):
                raise ValueError("All table rows must have the same column count.")
            if sum(len(cell) for row in self.rows for cell in row) > 100_000:
                raise ValueError("Table content exceeds its text bound.")
        elif self.rows is not None:
            raise ValueError("Only a table element accepts rows.")
        if (self.kind == "image") != (self.image_base64 is not None):
            raise ValueError("An image element requires exact embedded image bytes.")
        if self.kind.startswith("form_") != (self.field_name is not None):
            raise ValueError("Only interactive form elements require field_name.")
        if self.kind == "form_choice":
            if (
                not self.options
                or len(set(self.options)) != len(self.options)
                or self.text not in self.options
            ):
                raise ValueError(
                    "A choice requires distinct options and an admitted initial value."
                )
        elif self.options:
            raise ValueError("Only form choices accept options.")
        if self.checked and self.kind != "form_checkbox":
            raise ValueError("Only a checkbox accepts checked.")
        return self


class PdfPage(Contract):
    width: float = Field(default=595.276, ge=72, le=2000, allow_inf_nan=False)
    height: float = Field(default=841.89, ge=72, le=2000, allow_inf_nan=False)
    elements: list[PdfElement] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def content_inside_page(self):
        if any(
            item.x + item.width > self.width or item.y + item.height > self.height
            for item in self.elements
        ):
            raise ValueError(
                "Every element rectangle must fit within its page, in top-left PDF points."
            )
        return self


class PdfGenerate(PdfSelection):
    logical_name: str = Field(pattern=r"^[^/\\:]+\.pdf$", max_length=180)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    title: str = Field(default="", max_length=1000)
    author: str = Field(default="", max_length=1000)
    pages: list[PdfPage] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def complete_document(self):
        elements = [element for page in self.pages for element in page.elements]
        names = [element.field_name for element in elements if element.field_name is not None]
        if len(elements) > 2000 or len(names) != len(set(names)):
            raise ValueError("Use at most 2000 elements and distinct interactive field names.")
        return self


class PdfEdit(PdfSnapshot):
    expected_sha256: str = Field(pattern=DIGEST)
    fields: dict[str, str | bool | list[str]] = Field(default_factory=dict, max_length=200)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=6)
    page_order: list[int] | None = Field(default=None, min_length=1, max_length=500)
    rotation: Literal[0, 90, 180, 270] = 0
    flatten: bool = False
    repair_orphan_widgets: bool = False
    allow_signed_derivative: bool = False

    @model_validator(mode="after")
    def bounded_changes(self):
        if not (
            self.fields
            or self.metadata
            or self.page_order is not None
            or self.rotation
            or self.flatten
            or self.repair_orphan_widgets
        ):
            raise ValueError("Select a concrete PDF change.")
        if set(self.metadata) - {
            "/Title",
            "/Author",
            "/Subject",
            "/Keywords",
            "/Creator",
            "/Producer",
        }:
            raise ValueError("Select standard PDF document metadata fields.")
        if any(len(value) > 10_000 for value in self.metadata.values()):
            raise ValueError("A metadata field exceeds its text bound.")
        if self.page_order is not None and (
            len(set(self.page_order)) != len(self.page_order)
            or any(type(page) is not int or not 1 <= page <= 500 for page in self.page_order)
        ):
            raise ValueError("Select distinct admitted one-based page numbers.")
        for name, value in self.fields.items():
            if not 1 <= len(name) <= 500 or isinstance(value, str) and len(value) > 100_000:
                raise ValueError("Use bounded form field names and values.")
            if isinstance(value, list) and (
                len(value) > 100 or any(len(item) > 1000 for item in value)
            ):
                raise ValueError("Use a bounded list of selected form options.")
        return self

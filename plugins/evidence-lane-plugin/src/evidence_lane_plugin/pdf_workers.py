"""Registered PDF workers invoke closed, memory-limited codec children."""

import base64
from pathlib import Path

from .pdf_parsers import MAX_BYTES, digest, fail, parse_pdf
from .storage import reject_links


def source_bytes(path, limit):
    path = Path(path)
    reject_links(path, Path(path.anchor))
    before = path.stat()
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    after = path.stat()
    if len(content) > limit:
        fail("INPUT_BUDGET")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        fail("SOURCE_CHANGED")
    return content


def encoded(name, content, *, options, evidence):
    if len(content) > MAX_BYTES:
        fail("OUTPUT_BUDGET")
    facts = parse_pdf(content, **options)
    return {
        "filename": name,
        "sha256": digest(content),
        "bytes": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "facts": facts,
        "evidence": {"source_bytes_mutated": False, "native": facts["native_evidence"], **evidence},
    }


def perform(operation, arguments):
    if operation == "generate":
        from .pdf_authoring import generate_pdf
        from .pdf_contracts import PdfGenerate

        request = PdfGenerate.model_validate(arguments)
        raw, evidence = generate_pdf(request)
        return encoded(
            request.logical_name,
            raw,
            options={"max_pages": 100, "extract_tables": True},
            evidence=evidence,
        )
    content = base64.b64decode(arguments["content_base64"], validate=True)
    if digest(content) != arguments["expected_sha256"]:
        fail("INPUT_CHANGED")
    if operation == "parse":
        return encoded(
            arguments["logical_name"],
            content,
            options=arguments["parse_options"],
            evidence={"operation": "native_pdf_snapshot"},
        )
    if operation == "edit":
        from .pdf_authoring import edit_pdf
        from .pdf_contracts import PdfEdit

        request = PdfEdit.model_validate(arguments["request"])
        content, evidence = edit_pdf(content, request)
        return encoded(
            arguments["logical_name"],
            content,
            options=arguments["parse_options"],
            evidence=evidence,
        )
    if operation == "render":
        from .pdf_rendering import render_pdf

        return render_pdf(content, arguments)
    if operation == "ocr":
        from .pdf_ocr import ocr_pdf

        return ocr_pdf(content, arguments)
    if operation == "enrich":
        import tempfile

        from .document_toolchain import DoclingRequest, extract_with_docling
        from .pdf_parsers import open_reader

        if len(open_reader(content).pages) > arguments["max_pages"]:
            fail("PAGE_BUDGET")
        with tempfile.TemporaryDirectory(prefix="evidence-lane-pdf-enrich-") as folder:
            source = Path(folder) / "document.pdf"
            source.write_bytes(content)
            try:
                return extract_with_docling(
                    DoclingRequest(
                        source_path=source,
                        host_profile="STUDIO_WORKER",
                        max_file_bytes=8_388_608,
                        max_output_bytes=arguments["max_output_bytes"],
                        max_pages=arguments["max_pages"],
                    )
                )
            except ValueError as error:
                if str(error) in {
                    "DOCLING_INPUT_BYTE_BUDGET",
                    "DOCLING_OUTPUT_BYTE_BUDGET",
                    "DOCLING_SOURCE_CHANGED",
                    "DOCLING_MODELS_CHANGED",
                    "DOCLING_CONVERSION_NOT_COMPLETE",
                }:
                    fail(str(error))
                raise
    fail("OPERATION_INVALID")


def parse_file(arguments):
    from .pdf_process import invoke_pdf

    raw = source_bytes(arguments["filename"], arguments["max_file_bytes"])
    return invoke_pdf(
        "parse",
        {
            "logical_name": arguments["logical_name"],
            "expected_sha256": digest(raw),
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "parse_options": arguments["parse_options"],
        },
    )


def parse_bytes(arguments):
    from .pdf_process import invoke_pdf

    return invoke_pdf("parse", arguments)


def generate(arguments):
    from .pdf_process import invoke_pdf

    return invoke_pdf("generate", arguments)


def edit(arguments):
    from .pdf_process import invoke_pdf

    return invoke_pdf("edit", arguments)


def render(arguments):
    from .pdf_process import invoke_pdf

    return invoke_pdf("render", arguments)


def ocr(arguments):
    from .pdf_process import invoke_pdf

    return invoke_pdf("ocr", arguments, timeout_seconds=180)


def enrich(arguments):
    from .pdf_process import invoke_pdf

    return invoke_pdf("enrich", arguments, timeout_seconds=240)


def pdf_worker_operations():
    from .workers import WorkerOperation

    return tuple(
        WorkerOperation(
            "pdf_" + name,
            __name__,
            function,
            path_fields=("filename",) if name == "parse_file" else (),
            max_output_bytes=67_108_864,
            max_input_bytes=33_554_432,
            error_codes=(
                "PDF_ENCRYPTED_INPUT",
                "PDF_PAGE_BUDGET",
                "PDF_RENDER_PIXEL_BUDGET",
                "PDF_INPUT_INVALID",
                "PDF_INPUT_CHANGED",
                "PDF_FORM_FIELD_UNKNOWN",
                "PDF_FORM_FONT_COVERAGE",
                "PDF_FORM_APPEARANCE_FONT_UNVERIFIED",
                "PDF_FORM_TEXT_LENGTH",
                "PDF_FORM_TREE_AMBIGUOUS",
                "PDF_SIGNED_DERIVATIVE_REQUIRES_SELECTION",
                "PDF_XFA_EDIT_UNSUPPORTED",
                "PDF_FORM_GRAPH_INCOMPLETE",
                "PDF_ORPHAN_REPAIR_AMBIGUOUS",
                "PDF_OPERATION_TIMEOUT",
                "PDF_STRUCTURE_BUDGET",
                "PDF_AUTHOR_CONTENT_OVERFLOW",
                "PDF_OWNER_UNAVAILABLE",
                "PDF_OCR_MODELS_UNAVAILABLE",
                "PDF_OCR_LANGUAGE_UNSUPPORTED",
                "PDF_OCR_PROVIDER_UNAVAILABLE",
                "PDF_NATIVE_OPERATION_FAILED",
                "PDF_DOCLING_CONVERSION_NOT_COMPLETE",
                "PDF_DOCLING_OUTPUT_BYTE_BUDGET",
            ),
        )
        for name, function in [
            ("parse_file", "parse_file"),
            ("parse_bytes", "parse_bytes"),
            ("generate", "generate"),
            ("edit", "edit"),
            ("render", "render"),
            ("ocr", "ocr"),
            ("enrich", "enrich"),
        ]
    )

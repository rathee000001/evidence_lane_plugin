"""PDFium raster derivatives from selected exact PDF pages; no viewer actions."""

import base64
import io
import math

from .pdf_parsers import digest, fail, open_reader


def render_pdf(content, arguments):
    import pypdfium2 as pdfium

    pages = arguments["pages"]
    reader = open_reader(content)
    if (
        len(set(pages)) != len(pages)
        or not pages
        or any(not 1 <= page <= len(reader.pages) for page in pages)
    ):
        fail("PAGE_SELECTION_INVALID")
    files, used = [], 0
    with pdfium.PdfDocument(content) as document:
        if arguments["include_annotations"]:
            document.init_forms()
        for page_number in pages:
            page = document[page_number - 1]
            bitmap = None
            try:
                width, height = page.get_size()
                scale = arguments["dpi"] / 72
                if not all(math.isfinite(item) and item > 0 for item in [width, height]):
                    fail("GEOMETRY_INVALID")
                expected_width, expected_height = (
                    math.ceil(width * scale),
                    math.ceil(height * scale),
                )
                if expected_width * expected_height > arguments["max_pixels_per_page"]:
                    fail("RENDER_PIXEL_BUDGET")
                bitmap = page.render(
                    scale=scale,
                    may_draw_forms=arguments["include_annotations"],
                    draw_annots=arguments["include_annotations"],
                )
                image = bitmap.to_pil()
                output = io.BytesIO()
                try:
                    if image.width * image.height > arguments["max_pixels_per_page"]:
                        fail("RENDER_PIXEL_BUDGET")
                    image.save(output, format="PNG")
                    raw = output.getvalue()
                    used += len(raw)
                    if used > 16_777_216:
                        fail("RENDER_OUTPUT_BUDGET")
                    files.append(
                        {
                            "filename": f"page-{page_number}.png",
                            "page": page_number,
                            "sha256": digest(raw),
                            "bytes": len(raw),
                            "width": image.width,
                            "height": image.height,
                            "content_base64": base64.b64encode(raw).decode("ascii"),
                        }
                    )
                finally:
                    image.close()
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
    return {
        "input_sha256": digest(content),
        "files": files,
        "evidence": {
            "engine": "pypdfium2",
            "dpi": arguments["dpi"],
            "include_annotations": arguments["include_annotations"],
            "forms_initialized": arguments["include_annotations"],
            "visual_review": "required",
            "actions_executed": False,
            "source_bytes_mutated": False,
        },
    }

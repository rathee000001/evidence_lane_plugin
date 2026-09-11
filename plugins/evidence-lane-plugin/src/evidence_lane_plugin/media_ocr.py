"""Offline OCR for explicit raster frames, separate from source metadata."""

import io
import math

from .hashing import canonical_json_bytes
from .media_parsers import _pixel_budget, digest, empty_frame_reviews, fail, open_raster
from .pdf_ocr import rapid_engine, rapid_lines, tesseract_lines
from .shared_tool_assets import resolve_shared_asset


def ocr_image(content, request, backend="rapidocr", compute=None):
    from PIL import ImageOps

    if backend not in {"rapidocr", "tesseract"}:
        fail("OCR_BACKEND_INVALID")
    lines, frames = [], []
    with open_raster(content, max_pixels=request.max_pixels_per_frame) as source:
        count = getattr(source, "n_frames", 1)
        if any(frame > count for frame in request.frames):
            fail("FRAME_SELECTION_INVALID")
        engine, evidence = rapid_engine(request.language, compute) if backend == "rapidocr" else (None, {})
        for frame in request.frames:
            source.seek(frame - 1)
            _pixel_budget(source, request.max_pixels_per_frame)
            image = ImageOps.exif_transpose(source).convert("RGB")
            try:
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                png = buffer.getvalue()
                found = rapid_lines(engine, png) if backend == "rapidocr" else None
                if backend == "tesseract":
                    found, evidence = tesseract_lines(png, request.language)
                for ordinal, line in enumerate(found):
                    confidence, points = line["confidence"], line["polygon_pixels"]
                    if (
                        not math.isfinite(confidence)
                        or not 0 <= confidence <= 1
                        or len(line["text"]) > 100000
                        or len(lines) >= 20000
                    ):
                        fail("OCR_RESULT_BUDGET")
                    if len(points) != 4 or any(
                        len(point) != 2
                        or not all(math.isfinite(float(v)) for v in point)
                        or not -1 <= point[0] <= image.width + 1
                        or not -1 <= point[1] <= image.height + 1
                        for point in points
                    ):
                        fail("OCR_GEOMETRY_INVALID")
                    line.update(
                        frame=frame,
                        ordinal=ordinal,
                        raster_sha256=digest(png),
                        coordinate_basis="exif_oriented_frame_pixels",
                        review_required=confidence < request.min_confidence,
                    )
                    line["line_id"] = digest(canonical_json_bytes(line))
                    lines.append(line)
                frames.append(
                    {
                        "frame": frame,
                        "width": image.width,
                        "height": image.height,
                        "raster_sha256": digest(png),
                        "lines": len(found),
                    }
                )
            finally:
                image.close()
    identity = "rapidocr_models" if backend == "rapidocr" else "tesseract_languages"
    if resolve_shared_asset(identity)[1]["files_sha256"] != evidence["models_sha256"]:
        fail("OCR_MODELS_CHANGED")
    return {
        "compute": evidence.get('compute', {'selected_provider': 'CPU', 'execution_state': 'executed'}),
        "input_sha256": digest(content),
        "frames": frames,
        "lines": lines,
        "review_regions": [row["line_id"] for row in lines if row["review_required"]],
        "review_frames": empty_frame_reviews(frames),
        "options": request.model_dump(mode="json"),
        "evidence": {
            **evidence,
            "preprocessing": "OpenCV_CLAHE" if backend == "rapidocr" else "Tesseract_internal",
            "source_bytes_mutated": False,
            "confidence_is_accuracy_proof": False,
        },
    }

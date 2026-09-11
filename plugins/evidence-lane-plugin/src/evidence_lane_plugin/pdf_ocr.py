"""Per-page offline OCR evidence; native PDF text is never replaced."""

from __future__ import annotations

import base64
import csv
import importlib.metadata
import io
import math

from .hashing import canonical_json_bytes
from .pdf_parsers import digest, fail, open_reader
from .pdf_rendering import render_pdf
from .shared_tool_assets import resolve_shared_asset

MODEL_NAMES = {
    "detector": "PP-OCRv6_det_small.onnx",
    "classifier": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "recognizer": "PP-OCRv6_rec_small.onnx",
}


def rapid_engine(language, compute=None):
    if compute and compute.get('selected_provider') != 'CPU':
        from .ocr_compute import ProviderOcr

        engine = ProviderOcr(language, compute)
        return engine, engine.evidence
    if language != "eng":
        fail("OCR_LANGUAGE_UNSUPPORTED")
    from rapidocr import RapidOCR

    folder, asset = resolve_shared_asset("rapidocr_models")
    names = {row["path"] for row in asset["files"]}
    if not set(MODEL_NAMES.values()) <= names:
        fail("OCR_MODELS_UNAVAILABLE")
    engine = RapidOCR(
        params={
            "Global.log_level": "critical",
            "Global.model_root_dir": str(folder),
            "Det.model_path": str(folder / MODEL_NAMES["detector"]),
            "Cls.model_path": str(folder / MODEL_NAMES["classifier"]),
            "Rec.model_path": str(folder / MODEL_NAMES["recognizer"]),
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.use_cuda": False,
            "EngineConfig.onnxruntime.use_dml": False,
            "EngineConfig.onnxruntime.use_cann": False,
            "EngineConfig.onnxruntime.use_coreml": False,
        }
    )
    providers = [
        part.session.session.get_providers()
        for part in [engine.text_det, engine.text_cls, engine.text_rec]
    ]
    if any(row != ["CPUExecutionProvider"] for row in providers):
        fail("OCR_PROVIDER_UNAVAILABLE")
    return engine, {
        "engine": "RapidOCR",
        "version": importlib.metadata.version("rapidocr"),
        "onnxruntime": importlib.metadata.version("onnxruntime"),
        "models_sha256": asset["files_sha256"],
        "provider": "CPUExecutionProvider",
        "acceleration_claimed": False,
        "models_downloaded": False,
        "compute": {"selected_provider": "CPU", "execution_state": "executed",
            "execution_basis": "all_rapidocr_sessions_explicit_CPUExecutionProvider"},
    }


def rapid_lines(engine, png):
    from .ocr_compute import ProviderOcr

    if isinstance(engine, ProviderOcr):
        return engine.lines(png)
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        fail("OCR_RASTER_INVALID")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    output = engine(gray, text_score=0.0)
    if output.txts is None:
        return []
    if (
        len(output.txts) > 5000
        or len(output.txts) != len(output.scores)
        or len(output.txts) != len(output.boxes)
    ):
        fail("OCR_LINE_BUDGET")
    return [
        {"text": text, "confidence": float(score), "polygon_pixels": polygon.tolist()}
        for text, score, polygon in zip(output.txts, output.scores, output.boxes, strict=True)
    ]


def tesseract_lines(png, language):
    from .installation_layout import studio_installation
    from .shared_native_tools import NativeInvocationRequest, run_native_tool

    _, runtime = resolve_shared_asset("tesseract_runtime")
    folder, asset = resolve_shared_asset("tesseract_languages")
    names = {row["path"] for row in asset["files"]}
    if any(lang + ".traineddata" not in names for lang in language.split("+")):
        fail("OCR_LANGUAGE_UNSUPPORTED")
    response = run_native_tool(
        NativeInvocationRequest(
            tool_id="tesseract",
            arguments=[
                "stdin",
                "stdout",
                "--tessdata-dir",
                str(folder),
                "-l",
                language,
                "--psm",
                "3",
                "--oem",
                "1",
                "-c",
                "tessedit_create_tsv=1",
            ],
            input_bytes=png,
            timeout_seconds=60,
            max_output_bytes=2_097_152,
            host_profile="CODEX_DESKTOP",
        ),
        runtime_root=studio_installation().active_root,
    )
    if response["status"] != "PASS":
        fail("OCR_NATIVE_FAILED")
    if resolve_shared_asset("tesseract_runtime")[1]["files_sha256"] != runtime["files_sha256"]:
        fail("OCR_RUNTIME_CHANGED")
    lines = []
    for row in csv.DictReader(io.StringIO(response["stdout"]), delimiter="\t"):
        if row["level"] != "5" or not row["text"].strip():
            continue
        if len(lines) >= 5000:
            fail("OCR_LINE_BUDGET")
        x, y, w, h = (int(row[key]) for key in ["left", "top", "width", "height"])
        lines.append(
            {
                "text": row["text"],
                "confidence": float(row["conf"]) / 100,
                "polygon_pixels": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                "block": int(row["block_num"]),
                "paragraph": int(row["par_num"]),
                "line": int(row["line_num"]),
            }
        )
    return lines, {
        "engine": "Tesseract",
        "version": response["version"],
        "executable_sha256": response["executable_sha256"],
        "models_sha256": asset["files_sha256"],
        "runtime_sha256": runtime["files_sha256"],
        "native_receipt_sha256": response["receipt_sha256"],
        "provider": "CPU",
        "models_downloaded": False,
    }


def ocr_pdf(content, arguments):
    reader = open_reader(content)
    pages = arguments["pages"]
    if (
        len(set(pages)) != len(pages)
        or not pages
        or any(not 1 <= page <= len(reader.pages) for page in pages)
    ):
        fail("PAGE_SELECTION_INVALID")
    native = {page: (reader.pages[page - 1].extract_text() or "") for page in pages}
    selected = [
        page
        for page in pages
        if arguments["mode"] == "all_selected_pages"
        or len(native[page].strip()) < arguments["native_character_threshold"]
    ]
    backend = arguments.get("backend", "rapidocr")
    if backend not in {"rapidocr", "tesseract"}:
        fail("BACKEND_INVALID")
    lines, evidence = [], {"engine": backend, "invoked": False, "models_downloaded": False}
    if selected:
        rendered = render_pdf(
            content, {**arguments, "pages": selected, "include_annotations": True}
        )
        engine, rapid_evidence = (
            rapid_engine(arguments["language"], arguments.get('_compute')) if backend == "rapidocr" else (None, None)
        )
        for file in rendered["files"]:
            png = base64.b64decode(file["content_base64"], validate=True)
            if backend == "rapidocr":
                found, evidence = rapid_lines(engine, png), rapid_evidence
            else:
                found, evidence = tesseract_lines(png, arguments["language"])
            for ordinal, line in enumerate(found):
                if (
                    not 0 <= line["confidence"] <= 1
                    or not math.isfinite(line["confidence"])
                    or len(line["text"]) > 100_000
                ):
                    fail("OCR_RESULT_INVALID")
                points = line["polygon_pixels"]
                if len(points) != 4 or any(
                    len(point) != 2 or not all(math.isfinite(float(x)) for x in point)
                    for point in points
                ):
                    fail("OCR_RESULT_INVALID")
                if any(
                    not -1 <= point[0] <= file["width"] + 1
                    or not -1 <= point[1] <= file["height"] + 1
                    for point in points
                ):
                    fail("OCR_RESULT_INVALID")
                scale = 72 / arguments["dpi"]
                line.update(
                    page=file["page"],
                    ordinal=ordinal,
                    raster_sha256=file["sha256"],
                    polygon_points=[[round(x * scale, 4), round(y * scale, 4)] for x, y in points],
                    review_required=line["confidence"] < arguments["min_confidence"],
                )
                line["line_id"] = digest(canonical_json_bytes(line))
                lines.append(line)
        _, verified_after = resolve_shared_asset(
            "rapidocr_models" if backend == "rapidocr" else "tesseract_languages"
        )
        if verified_after["files_sha256"] != evidence["models_sha256"]:
            fail("OCR_MODELS_CHANGED")
        evidence = {**evidence, "invoked": True}
    result = {
        "compute": (evidence.get('compute', {'selected_provider': 'CPU', 'execution_state': 'executed'})
            if selected else {'selected_provider': None, 'execution_state': 'not_required',
                'reason': 'native_text_satisfied_selected_pages'}),
        "schema": "evidence-lane.pdf-ocr-result.v4",
        "input_sha256": digest(content),
        "pages": [
            {
                "page": page,
                "native_characters": len(native[page].strip()),
                "native_text_sha256": digest(native[page].encode()),
                "ocr_selected": page in selected,
                "ocr_lines": sum(row["page"] == page for row in lines),
            }
            for page in pages
        ],
        "lines": lines,
        "review_regions": [row["line_id"] for row in lines if row["review_required"]],
        "options": {
            key: arguments[key]
            for key in ("mode", "native_character_threshold", "dpi", "language", "min_confidence")
        },
        "evidence": {
            **evidence,
            "preprocessing": "OpenCV_CLAHE" if backend == "rapidocr" else "Tesseract_internal",
            "native_text_preserved": True,
            "source_bytes_mutated": False,
            "accuracy_verified": False,
            "word_or_line_granularity": "word" if backend == "tesseract" else "line",
            "confidence_is_calibrated_probability": False,
            "coordinate_space": "rendered_page_top_left_points",
        },
    }
    if len(canonical_json_bytes(result)) > 8_388_608:
        fail("OCR_OUTPUT_BUDGET")
    return result

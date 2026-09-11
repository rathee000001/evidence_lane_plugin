"""Image/media codecs run in owned processes under closed engine operations."""

import base64
from pathlib import Path

from .errors import LaneError
from .media_parsers import MAX_BYTES, digest, fail, parse_media
from .storage import reject_links


def source_bytes(path, limit):
    path = Path(path)
    reject_links(path, Path(path.anchor))
    before = path.stat()
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    after = path.stat()
    if len(raw) > min(limit, MAX_BYTES):
        fail("INPUT_BUDGET")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        fail("SOURCE_CHANGED")
    return raw


def encoded(name, content, *, options, evidence):
    if len(content) > MAX_BYTES:
        fail("OUTPUT_BUDGET")
    facts = parse_media(name, content, **options)
    return {
        "filename": name,
        "sha256": digest(content),
        "bytes": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "facts": facts,
        "evidence": {"source_bytes_mutated": False, "native": facts["native_evidence"], **evidence},
    }


def _perform(operation, arguments):
    content = base64.b64decode(arguments["content_base64"], validate=True)
    if len(content) > MAX_BYTES or digest(content) != arguments["expected_sha256"]:
        fail("INPUT_CHANGED")
    if operation == "parse":
        return encoded(
            arguments["logical_name"],
            content,
            options=arguments["parse_options"],
            evidence={"operation": "native_media_snapshot"},
        )
    if operation == "transform":
        from .media_contracts import MediaTransform
        from .media_transform import transform_image

        request = MediaTransform.model_validate(arguments["request"])
        raw, evidence = transform_image(content, request)
        return encoded(
            request.logical_name,
            raw,
            options={"max_frames": 1, "max_pixels_per_frame": request.max_pixels},
            evidence=evidence,
        )
    if operation == "ocr":
        from .media_contracts import MediaOcr
        from .media_ocr import ocr_image

        return ocr_image(
            content, MediaOcr.model_validate(arguments["request"]), arguments["backend"], arguments.get('_compute')
        )
    if operation == "extract":
        from .media_contracts import MediaExtract
        from .media_native import extract_media

        return extract_media(
            arguments["logical_name"], content, MediaExtract.model_validate(arguments["request"])
        )
    fail("OPERATION_INVALID")


def perform(operation, arguments):
    try:
        return _perform(operation, arguments)
    except LaneError as error:
        # The image and PDF lanes share stateless OCR adapters, not stored
        # results or lane authority. Translate that adapter's error namespace.
        if error.code.startswith("PDF_"):
            fail(error.code.removeprefix("PDF_"))
        raise


def parse_file(arguments):
    from .media_process import invoke_media

    raw = source_bytes(arguments["filename"], arguments["max_file_bytes"])
    return invoke_media(
        "parse",
        {
            "logical_name": arguments["logical_name"],
            "expected_sha256": digest(raw),
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "parse_options": arguments["parse_options"],
        },
    )


def parse_bytes(arguments):
    from .media_process import invoke_media

    return invoke_media("parse", arguments)


def transform(arguments):
    from .media_process import invoke_media

    return invoke_media("transform", arguments)


def ocr(arguments):
    from .media_process import invoke_media

    return invoke_media("ocr", arguments, timeout_seconds=180)


def extract(arguments):
    from .media_process import invoke_media

    return invoke_media("extract", arguments, timeout_seconds=90)


def media_worker_operations():
    from .workers import WorkerOperation

    errors = (
        "INPUT_CHANGED",
        "INPUT_BUDGET",
        "FRAME_BUDGET",
        "FRAME_SELECTION_INVALID",
        "ANIMATED_FRAME_SELECTION_REQUIRED",
        "PIXEL_BUDGET",
        "FORMAT_UNSUPPORTED",
        "FORMAT_EXTENSION_MISMATCH",
        "RASTER_INVALID",
        "SVG_XML_INVALID",
        "SVG_STRUCTURE_BUDGET",
        "CROP_OUTSIDE_IMAGE",
        "JPEG_ALPHA_BACKGROUND_REQUIRED",
        "COLOR_PROFILE_CONVERSION_UNSUPPORTED",
        "OCR_LANGUAGE_UNSUPPORTED",
        "OCR_MODELS_UNAVAILABLE",
        "OCR_PROVIDER_UNAVAILABLE",
        "OPERATION_TIMEOUT",
        "OWNER_UNAVAILABLE",
        "NATIVE_OPERATION_FAILED",
        "EXTRACTION_OUTPUT_BUDGET",
    )
    return tuple(
        WorkerOperation(
            "media_" + name,
            __name__,
            name,
            path_fields=("filename",) if name == "parse_file" else (),
            max_output_bytes=67_108_864,
            max_input_bytes=33_554_432,
            error_codes=tuple("MEDIA_" + code for code in errors),
        )
        for name in ["parse_file", "parse_bytes", "transform", "ocr", "extract"]
    )

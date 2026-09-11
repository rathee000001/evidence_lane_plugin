"""Bounded raster/SVG/media facts, always subordinate to exact source bytes."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import math
import warnings
from pathlib import PurePosixPath

from .errors import LaneError
from .hashing import canonical_json_bytes

MAX_BYTES = 16_777_216
RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
MEDIA_EXTENSIONS = {
    ".avi",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}
EXTENSIONS = RASTER_EXTENSIONS | MEDIA_EXTENSIONS | {".svg"}
RASTER_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".bmp": "BMP",
    ".gif": "GIF",
    ".webp": "WEBP",
}
KINDS = ("frame", "exif", "svg_element", "svg_text", "stream", "tag")


def fail(code):
    raise LaneError("MEDIA_" + code, "The selected media does not satisfy this bounded operation.")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def empty_frame_reviews(frames):
    return [
        {"frame": row["frame"], "reason": "OCR_EMPTY", "width": row["width"],
         "height": row["height"], "raster_sha256": row["raster_sha256"],
         "coordinate_basis": "exif_oriented_frame_pixels", "review_required": True}
        for row in frames if row["lines"] == 0
    ]


def _scalar(value, depth=0):
    if depth > 4:
        fail("METADATA_DEPTH_BUDGET")
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": digest(value)}
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 100000:
            fail("METADATA_TEXT_BUDGET")
        return value
    if isinstance(value, dict):
        if len(value) > 512:
            fail("METADATA_ITEM_BUDGET")
        return {str(key): _scalar(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 512:
            fail("METADATA_ITEM_BUDGET")
        return [_scalar(item, depth + 1) for item in value]
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except ValueError, TypeError:
        return {"type": type(value).__name__}


def _item(kind, ordinal, part, **values):
    item = {"kind": kind, "ordinal": ordinal, "part": part, **values}
    item["item_id"] = digest(canonical_json_bytes(item))
    return item


def _pixel_budget(image, limit):
    if image.width < 1 or image.height < 1 or image.width * image.height > limit:
        fail("PIXEL_BUDGET")


def open_raster(content, *, max_frames=500, max_pixels=25_000_000):
    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(content))
        if image.format not in set(RASTER_FORMATS.values()):
            image.close()
            fail("RASTER_FORMAT_UNSUPPORTED")
        _pixel_budget(image, max_pixels)
        if not 1 <= getattr(image, "n_frames", 1) <= max_frames:
            image.close()
            fail("FRAME_BUDGET")
        return image
    except LaneError:
        raise
    except Exception:  # noqa: BLE001 - vendor/input strings do not escape the codec
        fail("RASTER_INVALID")


def _raster(content, extension, options):
    from PIL import ExifTags

    items = []
    with open_raster(
        content, max_frames=options["max_frames"], max_pixels=options["max_pixels_per_frame"]
    ) as image:
        if image.format != RASTER_FORMATS[extension]:
            fail("FORMAT_EXTENSION_MISMATCH")
        metadata = {
            "kind": "raster",
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "frames": getattr(image, "n_frames", 1),
            "info": _scalar(image.info),
            "frame_coordinates": "raw_decoded_pixels_before_exif_orientation",
        }
        exif = image.getexif()
        for key, value in exif.items():
            name = ExifTags.TAGS.get(key, str(key))
            item = _scalar(value)
            if key in {34665, 34853}:
                item = _scalar(exif.get_ifd(key))
            items.append(
                _item(
                    "exif",
                    len(items),
                    "exif:" + str(key),
                    tag=key,
                    name=name,
                    value=item,
                    text=name + " " + str(item),
                )
            )
        for index in range(metadata["frames"]):
            image.seek(index)
            _pixel_budget(image, options["max_pixels_per_frame"])
            image.load()
            items.append(
                _item(
                    "frame",
                    index,
                    f"frame:{index + 1}",
                    frame=index + 1,
                    width=image.width,
                    height=image.height,
                    mode=image.mode,
                    duration_ms=_scalar(image.info.get("duration")),
                    disposal=_scalar(getattr(image, "disposal_method", None)),
                    text=f"Frame {index + 1}: {image.width} x {image.height} {image.mode}",
                )
            )
    return metadata, items, {"engine": "Pillow", "version": importlib.metadata.version("pillow")}


def _svg(content):
    from defusedxml import ElementTree

    if len(content) > 4_194_304:
        fail("SVG_BYTE_BUDGET")
    try:
        root = ElementTree.fromstring(
            content, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except Exception:  # noqa: BLE001
        fail("SVG_XML_INVALID")
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        fail("SVG_ROOT_INVALID")
    metadata = {
        "kind": "vector",
        "format": "SVG",
        "width": root.get("width"),
        "height": root.get("height"),
        "viewBox": root.get("viewBox"),
        "rendered": False,
        "scripts_executed": False,
        "references_followed": False,
    }
    items, active, references = [], False, 0
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if len(items) >= 10000 or depth > 64 or len(node.attrib) > 100:
            fail("SVG_STRUCTURE_BUDGET")
        tag = node.tag.rsplit("}", 1)[-1]
        active |= tag in {"script", "foreignObject"} or any(
            key.lower().startswith("on") for key in node.attrib
        )
        attrs = dict(node.attrib)
        if sum(len(key) + len(value) for key, value in attrs.items()) > 100000:
            fail("SVG_ATTRIBUTE_BUDGET")
        references += sum(key.rsplit("}", 1)[-1] == "href" for key in node.attrib)
        text = "".join(node.itertext()).strip() if tag == "text" else ""
        if len(text) > 100000:
            fail("SVG_TEXT_BUDGET")
        item = _item(
            "svg_text" if tag == "text" else "svg_element",
            len(items),
            "svg:" + str(len(items)),
            tag=tag,
            attributes=attrs,
            depth=depth,
            text=text if tag == "text" else tag,
        )
        items.append(item)
        stack.extend((child, depth + 1) for child in reversed(node))
    metadata.update(active_content_present=active, reference_count=references)
    return (
        metadata,
        items,
        {"engine": "defusedxml", "version": importlib.metadata.version("defusedxml")},
    )


def parse_media(filename, content, *, max_frames=100, max_pixels_per_frame=12_000_000):
    if not content or len(content) > MAX_BYTES:
        fail("FILE_BYTE_BUDGET")
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in EXTENSIONS:
        fail("FORMAT_UNSUPPORTED")
    options = {"max_frames": max_frames, "max_pixels_per_frame": max_pixels_per_frame}
    if extension in RASTER_EXTENSIONS:
        metadata, items, native = _raster(content, extension, options)
    elif extension == ".svg":
        metadata, items, native = _svg(content)
    else:
        from .media_native import probe_media

        metadata, parts, native = probe_media(extension, content)
        items = [
            _item(row.pop("kind"), index, row.pop("part"), **row) for index, row in enumerate(parts)
        ]
    facts = {
        "schema": "evidence-lane.media-facts.v4",
        "metadata": metadata,
        "items": items,
        "counts": {kind: sum(row["kind"] == kind for row in items) for kind in KINDS},
        "native_evidence": {**native, "source_sha256": digest(content)},
        "parse_options": options,
        "fidelity": {
            "original_bytes_preserved": True,
            "ocr_performed": False,
            "vector_rendering_verified": False,
            "visual_review": "required",
            "external_resources_followed": False,
        },
        "limitations": [
            "SVG is inspected as passive structure; no vector renderer is invoked.",
            "Container metadata is reported by FFmpeg; full stream decoding and exact source timestamps are separate.",
            "OCR and visual fidelity require separate operations and review.",
        ],
    }
    if len(canonical_json_bytes(facts)) > 4_194_304:
        fail("STRUCTURE_BYTE_BUDGET")
    return facts

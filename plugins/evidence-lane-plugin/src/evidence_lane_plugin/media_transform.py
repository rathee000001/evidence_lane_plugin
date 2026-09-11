"""Closed raster transformations with explicit frame, alpha and metadata semantics."""

import io
import math
from pathlib import PurePosixPath

from .media_parsers import MAX_BYTES, _pixel_budget, digest, fail, open_raster


def transform_image(content, request):
    from PIL import Image, ImageColor, ImageOps, PngImagePlugin

    extensions = {
        "PNG": {".png"},
        "JPEG": {".jpg", ".jpeg"},
        "WEBP": {".webp"},
        "TIFF": {".tif", ".tiff"},
    }
    if PurePosixPath(request.logical_name).suffix.lower() not in extensions[request.output_format]:
        fail("OUTPUT_FORMAT_MISMATCH")
    with open_raster(content, max_pixels=request.max_pixels) as source:
        frames = getattr(source, "n_frames", 1)
        if frames > 1 and request.frame is None:
            fail("ANIMATED_FRAME_SELECTION_REQUIRED")
        frame = request.frame or 1
        if not 1 <= frame <= frames:
            fail("FRAME_SELECTION_INVALID")
        source.seek(frame - 1)
        _pixel_budget(source, request.max_pixels)
        source.load()
        info = dict(source.info)
        original_mode = source.mode
        image = ImageOps.exif_transpose(source) if request.apply_exif_orientation else source.copy()
    try:
        exif = image.getexif()
        if request.crop:
            box = request.crop
            if box.right > image.width or box.bottom > image.height:
                fail("CROP_OUTSIDE_IMAGE")
            image = image.crop((box.left, box.top, box.right, box.bottom))
        if request.width is not None:
            image = image.resize((request.width, request.height), Image.Resampling.LANCZOS)
        if request.rotate_clockwise:
            image = image.transpose(
                {
                    90: Image.Transpose.ROTATE_270,
                    180: Image.Transpose.ROTATE_180,
                    270: Image.Transpose.ROTATE_90,
                }[request.rotate_clockwise]
            )
        if request.flip != "none":
            image = ImageOps.mirror(image) if request.flip == "horizontal" else ImageOps.flip(image)
        if request.color_mode != "preserve":
            if (
                info.get("icc_profile")
                and request.metadata == "preserve_supported"
                and (request.color_mode == "L" or original_mode == "CMYK")
            ):
                fail("COLOR_PROFILE_CONVERSION_UNSUPPORTED")
            image = image.convert(request.color_mode)
        alpha_removed = False
        if request.output_format == "JPEG":
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            if has_alpha:
                if request.jpeg_background is None:
                    fail("JPEG_ALPHA_BACKGROUND_REQUIRED")
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, ImageColor.getrgb(request.jpeg_background))
                background.paste(rgba, mask=rgba.getchannel("A"))
                rgba.close()
                image = background
                alpha_removed = True
            elif image.mode not in {"RGB", "L", "CMYK"}:
                image = image.convert("RGB")
        elif request.output_format == "WEBP" and image.mode not in {"RGB", "RGBA"}:
            if (
                info.get("icc_profile")
                and image.mode == "CMYK"
                and request.metadata == "preserve_supported"
            ):
                fail("COLOR_PROFILE_CONVERSION_UNSUPPORTED")
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        if request.output_format != "JPEG" and "transparency" in image.info:
            image = image.convert("RGBA")
        _pixel_budget(image, request.max_pixels)
        kwargs, retained = {}, []
        if request.metadata == "preserve_supported":
            if exif:
                if request.apply_exif_orientation:
                    exif[274] = 1
                for key, value in [
                    (256, image.width),
                    (257, image.height),
                    (40962, image.width),
                    (40963, image.height),
                ]:
                    if key in exif:
                        exif[key] = value
                kwargs["exif"] = exif.tobytes()
                retained.append("EXIF")
            if info.get("icc_profile"):
                kwargs["icc_profile"] = info["icc_profile"]
                retained.append("ICC")
            dpi = info.get("dpi")
            if (
                request.output_format in {"JPEG", "PNG", "TIFF"}
                and isinstance(dpi, (tuple, list))
                and len(dpi) == 2
                and all(math.isfinite(float(v)) and v > 0 for v in dpi)
            ):
                kwargs["dpi"] = dpi
                retained.append("DPI")
            if request.output_format == "PNG":
                text = PngImagePlugin.PngInfo()
                for key, value in info.items():
                    if isinstance(value, str) and key not in {
                        "exif",
                        "icc_profile",
                        "transparency",
                    }:
                        text.add_itxt(key, value)
                kwargs["pnginfo"] = text
                retained.append("PNG_text")
        # Explicitly rebuild the output metadata from supported fields. In
        # particular, metadata='strip' must not leak Pillow's inherited info.
        if request.metadata == "strip":
            # TIFF may keep source tags outside Image.info. Build clean pixel
            # storage so an inherited IFD cannot reach the writer.
            clean = Image.frombytes(image.mode, image.size, image.tobytes())
            if image.mode == "P" and image.palette is not None:
                clean.putpalette(image.palette)
            image.close()
            image = clean
        image.info.clear()
        if request.output_format in {"JPEG", "WEBP"}:
            kwargs["quality"] = request.quality
        if request.output_format == "WEBP":
            kwargs["lossless"] = request.webp_lossless
        if request.output_format == "TIFF":
            kwargs["compression"] = "tiff_deflate"
        output = io.BytesIO()
        image.save(output, format=request.output_format, **kwargs)
        raw = output.getvalue()
        if len(raw) > MAX_BYTES:
            fail("TRANSFORM_OUTPUT_BUDGET")
        lossless = (
            request.output_format in {"PNG", "TIFF"}
            or request.output_format == "WEBP"
            and request.webp_lossless
        )
        with Image.open(io.BytesIO(raw)) as reopened:
            reopened.load()
            if (
                reopened.format != request.output_format
                or reopened.size != image.size
                or getattr(reopened, "n_frames", 1) != 1
            ):
                fail("TRANSFORM_VERIFICATION")
            if lossless:
                if reopened.mode == image.mode and image.mode not in {"P", "1"}:
                    equal = reopened.tobytes() == image.tobytes()
                else:
                    equal = reopened.convert("RGBA").tobytes() == image.convert("RGBA").tobytes()
                if not equal:
                    fail("TRANSFORM_PIXEL_MISMATCH")
            # TIFF exposes required decoding tags through getexif(). These
            # describe the newly encoded pixels; descriptive/source EXIF and
            # profile data must still be absent.
            structural = {256, 257, 258, 259, 262, 273, 277, 278, 279, 284, 317, 320, 338, 339} if request.output_format == "TIFF" else set()
            if request.metadata == "strip" and (
                set(reopened.getexif()) - structural or reopened.info.get("icc_profile")
            ):
                fail("METADATA_STRIP_VERIFICATION")
        return raw, {
            "engine": "Pillow",
            "frame": frame,
            "source_frames": frames,
            "apply_exif_orientation": request.apply_exif_orientation,
            "coordinate_order": [
                "exif_orientation",
                "crop",
                "resize",
                "rotate_clockwise",
                "flip",
                "color_mode",
            ],
            "output_size": list(image.size),
            "output_mode": image.mode,
            "lossless_pixels_verified": lossless,
            "alpha_composited": alpha_removed,
            "metadata_policy": request.metadata,
            "metadata_retained": retained,
            "source_sha256": digest(content),
            "source_bytes_mutated": False,
            "visual_review": "required",
            "complete_metadata_roundtrip_claimed": False,
        }
    finally:
        image.close()

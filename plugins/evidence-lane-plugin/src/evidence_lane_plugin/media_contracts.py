"""Closed image/media operations; clients cannot supply codecs or filter programs."""

from typing import Literal

from pydantic import Field, model_validator

from .registry import Contract


class MediaSelection(Contract):
    lane_id: Literal["images_ocr"] = "images_ocr"


class MediaIndex(MediaSelection):
    filename: str = Field(min_length=1, max_length=2048)
    expected_snapshot: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    max_file_bytes: int = Field(default=8_388_608, ge=1, le=16_777_216)
    max_frames: int = Field(default=100, ge=1, le=500)
    max_pixels_per_frame: int = Field(default=12_000_000, ge=1, le=25_000_000)


class MediaQuery(MediaSelection):
    match_mode: Literal['all', 'any'] = 'all'
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    collection: Literal[
        "metadata",
        "text",
        "frame",
        "exif",
        "svg_element",
        "svg_text",
        "stream",
        "tag",
        "ocr_run",
        "ocr_line",
        "review_region",
    ] = "metadata"
    query: str | None = Field(default=None, max_length=2000)
    frame: int | None = Field(default=None, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=30, ge=1, le=100)
    max_bytes: int = Field(default=131_072, ge=1024, le=524_288)


class MediaRead(MediaSelection):
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    representation: Literal["media", "original_source"] = "media"
    offset: int = Field(default=0, ge=0, le=16_777_216)
    max_bytes: int = Field(default=262_144, ge=1, le=524_288)


class MediaExport(MediaSelection):
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str = Field(min_length=1, max_length=2048)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class RasterCrop(Contract):
    left: int = Field(ge=0, le=25000)
    top: int = Field(ge=0, le=25000)
    right: int = Field(ge=1, le=25000)
    bottom: int = Field(ge=1, le=25000)

    @model_validator(mode="after")
    def ordered(self):
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Crop bounds must be ordered.")
        return self


class MediaTransform(MediaSelection):
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    logical_name: str = Field(min_length=1, max_length=180)
    output_format: Literal["PNG", "JPEG", "WEBP", "TIFF"] = "PNG"
    frame: int | None = Field(default=None, ge=1, le=500)
    apply_exif_orientation: bool = True
    crop: RasterCrop | None = None
    width: int | None = Field(default=None, ge=1, le=10000)
    height: int | None = Field(default=None, ge=1, le=10000)
    rotate_clockwise: Literal[0, 90, 180, 270] = 0
    flip: Literal["none", "horizontal", "vertical"] = "none"
    color_mode: Literal["preserve", "RGB", "RGBA", "L"] = "preserve"
    jpeg_background: str | None = Field(default=None, pattern=r"^#[a-fA-F0-9]{6}$")
    metadata: Literal["preserve_supported", "strip"] = "preserve_supported"
    quality: int = Field(default=92, ge=1, le=100)
    webp_lossless: bool = True
    max_pixels: int = Field(default=12_000_000, ge=1, le=25_000_000)

    @model_validator(mode="after")
    def bounded_resize(self):
        if (self.width is None) != (self.height is None):
            raise ValueError("Resize requires explicit width and height.")
        if self.width is not None and self.width * self.height > self.max_pixels:
            raise ValueError("Resize exceeds the pixel budget.")
        return self


class MediaOcr(MediaSelection):
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    frames: list[int] = Field(default_factory=lambda: [1], min_length=1, max_length=25)
    language: str = Field(default="eng", pattern=r"^[a-z]{3}(\+[a-z]{3}){0,3}$")
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    max_pixels_per_frame: int = Field(default=12_000_000, ge=1, le=25_000_000)

    @model_validator(mode="after")
    def distinct_frames(self):
        if len(set(self.frames)) != len(self.frames) or any(not 1 <= x <= 500 for x in self.frames):
            raise ValueError("Select distinct one-based frames.")
        return self


class MediaOcrRead(MediaSelection):
    ocr_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    frame: int | None = Field(default=None, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=30, ge=1, le=100)
    max_bytes: int = Field(default=131072, ge=1024, le=524288)


class MediaExtract(MediaSelection):
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["video_frame", "audio_segment"]
    start_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    duration_seconds: float = Field(default=5, gt=0, le=120, allow_inf_nan=False)
    stream: int = Field(default=0, ge=0, le=31)
    max_width: int = Field(default=1920, ge=1, le=4096)
    max_height: int = Field(default=1080, ge=1, le=4096)
    sample_rate: Literal[8000, 16000, 22050, 44100, 48000] = 16000
    channels: Literal[1, 2] = 1


class MediaExtractionRead(MediaSelection):
    extraction_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    offset: int = Field(default=0, ge=0, le=16_777_216)
    max_bytes: int = Field(default=262144, ge=1, le=524288)

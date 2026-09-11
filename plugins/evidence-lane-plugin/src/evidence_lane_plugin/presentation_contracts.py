"""Bounded editable presentation requests, each fixed to the PPT lane."""
from typing import Literal

from pydantic import Field, model_validator

from .hashing import canonical_json_bytes
from .registry import Contract

DIGEST = r'^[0-9a-f]{64}$'


class PresentationSelection(Contract):
    lane_id: Literal['ppt'] = 'ppt'


class PresentationIndex(PresentationSelection):
    filename: str = Field(min_length=1, max_length=1000)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=8_388_608)


class PresentationSnapshot(PresentationSelection):
    snapshot_id: str = Field(pattern=DIGEST)


class PresentationQuery(PresentationSnapshot):
    match_mode: Literal['all', 'any'] = 'all'
    collection: Literal['metadata', 'text', 'slide', 'notes', 'shape', 'text_block', 'table', 'image', 'relationship', 'chart'] = 'text'
    query: str | None = Field(default=None, min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class PresentationRead(PresentationSnapshot):
    representation: Literal['presentation', 'original_source'] = 'presentation'
    offset: int = Field(default=0, ge=0, le=8_388_608)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)


class SlideObject(Contract):
    kind: Literal['text', 'rectangle', 'ellipse', 'table', 'image']
    name: str = Field(min_length=1, max_length=160)
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    width: float = Field(gt=0, le=100)
    height: float = Field(gt=0, le=100)
    text: str = Field(default='', max_length=16_384)
    font_points: int = Field(default=24, ge=8, le=96)
    color: str = Field(default='172333', pattern=r'^[0-9A-Fa-f]{6}$')
    fill: str | None = Field(default=None, pattern=r'^[0-9A-Fa-f]{6}$')
    bold: bool = False
    rows: list[list[str]] = Field(default_factory=list, max_length=100)
    image_base64: str | None = Field(default=None, max_length=1_048_576)
    image_format: Literal['png', 'jpeg'] = 'png'
    description: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def valid_shape(self):
        if self.kind == 'table':
            if (not self.rows or not self.rows[0] or len(self.rows[0]) > 20
                    or any(len(row) != len(self.rows[0]) for row in self.rows)
                    or sum(len(cell.encode()) for row in self.rows for cell in row) > 131_072):
                raise ValueError('Use a bounded rectangular slide table')
        elif self.rows:
            raise ValueError('Only a table object accepts rows')
        if (self.kind == 'image') != (self.image_base64 is not None):
            raise ValueError('Image objects require embedded PNG or JPEG bytes')
        return self


class Slide(Contract):
    name: str = Field(min_length=1, max_length=160)
    background: str = Field(default='FFFFFF', pattern=r'^[0-9A-Fa-f]{6}$')
    notes: str = Field(default='', max_length=16_384)
    objects: list[SlideObject] = Field(min_length=1, max_length=100)


class PresentationGenerate(PresentationSelection):
    logical_name: str = Field(min_length=1, max_length=160, pattern=r'^[^/\\:]+\.pptx$')
    title: str = Field(min_length=1, max_length=200)
    width_inches: float = Field(default=13.333333, ge=1, le=56)
    height_inches: float = Field(default=7.5, ge=1, le=56)
    slides: list[Slide] = Field(min_length=1, max_length=100)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)

    @model_validator(mode='after')
    def bounds(self):
        if len(canonical_json_bytes(self.model_dump(mode='json'))) > 2_097_152:
            raise ValueError('Select a smaller presentation generation batch')
        for slide in self.slides:
            if len({obj.name for obj in slide.objects}) != len(slide.objects):
                raise ValueError('Object names must be unique on each slide')
            if any(obj.x + obj.width > self.width_inches or obj.y + obj.height > self.height_inches for obj in slide.objects):
                raise ValueError('Objects must fit within the declared slide dimensions in inches')
        return self


class PresentationReplacement(Contract):
    part: str = Field(pattern=r'^ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml$')
    paragraph_index: int = Field(ge=0, le=8191)
    expected_text: str = Field(max_length=32_768)
    replacement_text: str = Field(max_length=32_768)


class PresentationEdit(PresentationSnapshot):
    expected_sha256: str = Field(pattern=DIGEST)
    replacements: list[PresentationReplacement] = Field(default_factory=list, max_length=32)
    slide_order: list[str] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def changes(self):
        if not self.replacements and self.slide_order is None:
            raise ValueError('Select exact text replacements or a complete slide order')
        if len(canonical_json_bytes(self.model_dump(mode='json'))) > 262_144:
            raise ValueError('Select a smaller presentation edit batch')
        return self


class PresentationRender(PresentationSnapshot):
    max_pages: int = Field(default=20, ge=1, le=100)
    dpi: int = Field(default=110, ge=72, le=150)
    timeout_seconds: int = Field(default=45, ge=5, le=120)
    max_output_bytes: int = Field(default=8_388_608, ge=65_536, le=16_777_216)


class PresentationRenderRead(PresentationSelection):
    render_id: str = Field(pattern=DIGEST)


class PresentationExport(PresentationSnapshot):
    filename: str = Field(min_length=1, max_length=1000)
    expected_sha256: str | None = Field(default=None, pattern=DIGEST)

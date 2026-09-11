"""Web source operations; network grants are checked by the owning engine."""
from typing import Literal

from pydantic import Field, field_validator

from .registry import Contract
from .research_web_content import safe_url

DIGEST = r'^[0-9a-f]{64}$'


class Capture(Contract):
    lane_id: Literal['research'] = 'research'
    url: str = Field(min_length=1, max_length=8192)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    transport: Literal['auto', 'httpx', 'requests'] = 'auto'
    media: Literal['auto', 'html', 'text', 'opaque'] = 'auto'
    encoding: Literal['utf-8', 'utf-8-sig', 'windows-1252', 'iso-8859-1', 'utf-16'] = 'utf-8'
    max_bytes: int = Field(default=1_048_576, ge=1024, le=8_388_608)
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    max_redirects: int = Field(default=5, ge=0, le=8)

    @field_validator('url')
    @classmethod
    def validate_url(cls, value):
        return safe_url(value)


class Extract(Contract):
    lane_id: Literal['research'] = 'research'
    snapshot_id: str = Field(pattern=DIGEST)
    expected_snapshot: str = Field(pattern=DIGEST)
    extractor: Literal['auto', 'trafilatura', 'readability', 'beautifulsoup', 'markdownify', 'html2text', 'stdlib'] = 'auto'
    encoding: Literal['utf-8', 'utf-8-sig', 'windows-1252', 'iso-8859-1', 'utf-16'] = 'utf-8'

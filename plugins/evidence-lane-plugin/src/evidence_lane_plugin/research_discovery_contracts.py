"""Bounded provider-search contracts, separate from page capture."""
from typing import Literal

from pydantic import Field, model_validator

from .registry import Contract


class Discover(Contract):
    lane_id: Literal['research'] = 'research'
    query: str = Field(min_length=1, max_length=500)
    backends: list[Literal['brave', 'duckduckgo', 'google', 'mojeek', 'startpage', 'wikipedia', 'yahoo']] = Field(
        default_factory=lambda: ['duckduckgo', 'brave'], min_length=1, max_length=4)
    region: str = Field(default='us-en', pattern=r'^[a-z]{2}-[a-z]{2}$')
    safesearch: Literal['on', 'moderate', 'off'] = 'moderate'
    timelimit: Literal['d', 'w', 'm', 'y'] | None = None
    page: int = Field(default=1, ge=1, le=10)
    limit: int = Field(default=10, ge=1, le=50)
    timeout_seconds: int = Field(default=30, ge=1, le=60)
    max_bytes: int = Field(default=2_097_152, ge=1024, le=2_097_152)
    max_requests: int = Field(default=8, ge=1, le=16)
    expected_snapshot: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def selected_backends(self):
        if len(set(self.backends)) != len(self.backends) or '\x00' in self.query or not self.query.strip():
            raise ValueError('Select distinct providers and a nonempty literal query')
        return self


def source_parameters(value):
    return {key: value[key] for key in ('query', 'backends', 'region', 'safesearch', 'timelimit', 'page')}

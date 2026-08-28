"""Resolve the one executable plugin root in source and installed runtimes."""

from __future__ import annotations

import os
from pathlib import Path

PLUGIN_ROOT_ENV = "EVIDENCE_LANE_PLUGIN_ROOT"


def resolve_plugin_root(module_file: str | Path) -> Path:
    configured = str(os.environ.get(PLUGIN_ROOT_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(module_file).resolve().parents[2]


__all__ = ["PLUGIN_ROOT_ENV", "resolve_plugin_root"]

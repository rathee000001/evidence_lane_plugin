"""Thin PermissionRequest observer that never grants or denies permission."""

import sys
from pathlib import Path

_HOOKS_ROOT = Path(__file__).resolve().parent
if str(_HOOKS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HOOKS_ROOT))

from optional_event_observer import run

if __name__ == "__main__":
    raise SystemExit(run("PermissionRequest"))

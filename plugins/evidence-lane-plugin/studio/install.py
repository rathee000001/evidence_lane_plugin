"""Discoverable Studio installer alias; all work remains in scripts/bootstrap.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def main(argv=None) -> int:
    source = Path(__file__).resolve().parents[1] / "scripts/bootstrap.py"
    spec = importlib.util.spec_from_file_location("evidence_lane_studio_bootstrap", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("STUDIO_BOOTSTRAP_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

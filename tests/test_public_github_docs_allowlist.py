from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _validator():
    path = ROOT / "scripts" / "validate_public_github_docs.py"
    spec = importlib.util.spec_from_file_location("public_docs_allowlist", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_github_docs_are_exact_current_allowlist() -> None:
    receipt = _validator().validate()
    assert receipt["status"] == "PASS"
    assert receipt["public_docs_count"] == 23
    assert receipt["historical_or_internal_docs_in_public_root"] == 0
    assert receipt["broken_internal_references"] == 0

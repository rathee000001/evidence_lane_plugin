#!/usr/bin/env python3
"""Compile every shipped Python source without writing bytecode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def files(workspace_root: Path | None = None) -> list[Path]:
    roots = [PLUGIN / name for name in ("src", "hooks", "sdk", "scripts", "tests", "studio")]
    if workspace_root is not None:
        roots.extend([workspace_root / "tests", workspace_root / "scripts"])
    result = []
    for root in roots:
        if not root.exists():
            continue
        result.extend(
            path for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    return sorted(set(result))


def check(workspace_root: Path | None = None) -> dict:
    selected = files(workspace_root)
    errors = []
    for path in selected:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (SyntaxError, UnicodeError) as error:
            errors.append({"path": str(path), "line": getattr(error, "lineno", None), "message": str(error)})
    return {
        "status": "PASS" if not errors else "FAIL",
        "python": ".".join(str(value) for value in sys.version_info[:3]),
        "file_count": len(selected),
        "errors": errors,
        "bytecode_written": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path)
    args = parser.parse_args()
    result = check(args.workspace_root)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

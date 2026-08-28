#!/usr/bin/env python3
"""Seal one read-only Plan-history/current-route parity audit receipt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--plugin-root", required=True)
    parser.add_argument("--plan-runtime-sqlite", required=True)
    parser.add_argument("--active-row", type=int, required=True)
    parser.add_argument("--systemwide-regression-receipt")
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    repository = Path(arguments.repository_root).resolve()
    plugin = Path(arguments.plugin_root).resolve()
    sys.path.insert(0, str(plugin / "src"))
    from evidence_lane_plugin.systemwide_route_audit import (
        build_systemwide_route_audit,
    )

    receipt = build_systemwide_route_audit(
        plugin_root=plugin,
        repository_root=repository,
        plan_runtime_sqlite=Path(arguments.plan_runtime_sqlite),
        active_row=arguments.active_row,
        systemwide_regression_receipt=arguments.systemwide_regression_receipt,
    )
    output = Path(arguments.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(output)
    print(receipt["status"])
    print(receipt["receipt_sha256"])
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

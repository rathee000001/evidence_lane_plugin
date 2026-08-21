from __future__ import annotations

import argparse
from pathlib import Path

from evidence_lane_plugin.full_reconciliation import (
    build_full_reconciliation,
    write_full_reconciliation_reports,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile all registered sources, lanes, and Git history."
    )
    parser.add_argument("registry", type=Path)
    parser.add_argument("crosswalk", type=Path)
    parser.add_argument("lane_bundle", type=Path)
    parser.add_argument("receipt_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--expected-source-count", type=int, default=48)
    args = parser.parse_args()
    report = build_full_reconciliation(
        args.registry,
        args.crosswalk,
        args.lane_bundle,
        args.receipt_directory,
        expected_source_count=args.expected_source_count,
    )
    written = write_full_reconciliation_reports(report, args.output_directory)
    print(
        "{status} files={file_count} reconciliation_sha256={receipt}".format(
            status=written["status"],
            file_count=written["file_count"],
            receipt=written["reconciliation_sha256"],
        )
    )


if __name__ == "__main__":
    main()

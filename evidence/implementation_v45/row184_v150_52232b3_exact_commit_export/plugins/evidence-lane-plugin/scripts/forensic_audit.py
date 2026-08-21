from __future__ import annotations

import argparse
import json

from evidence_lane_plugin.forensic_audit import (
    audit_lane_bundle,
    write_forensic_audit_reports,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create deterministic per-lane forensic Markdown reports."
    )
    parser.add_argument("lane_bundle")
    parser.add_argument("output_directory")
    parser.add_argument("--subject", required=True)
    args = parser.parse_args()
    audit = audit_lane_bundle(args.lane_bundle, subject=args.subject)
    package = write_forensic_audit_reports(audit, args.output_directory)
    print(json.dumps(package, sort_keys=True, separators=(",", ":")))
    return 0 if audit["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

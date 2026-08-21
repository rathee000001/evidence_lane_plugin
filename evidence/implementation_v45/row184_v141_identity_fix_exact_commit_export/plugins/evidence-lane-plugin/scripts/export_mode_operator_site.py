from __future__ import annotations

import argparse
from pathlib import Path

from evidence_lane_plugin.site_operator_export import write_mode_operator_site_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export source-backed mode/operator guidance for the public site."
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = write_mode_operator_site_payload(args.output)
    print(
        f"PASS modes={payload['mode_count']} export_sha256={payload['export_sha256']}"
    )


if __name__ == "__main__":
    main()

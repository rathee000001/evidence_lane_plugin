from __future__ import annotations

import argparse
import json

from evidence_lane_plugin.successor_addendum import build_successor_addendum


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an append-only successor State Travel hash bridge."
    )
    parser.add_argument("--original-package", required=True)
    parser.add_argument("--addendum-root", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-manifest-sha256", required=True)
    parser.add_argument("--candidate-package-sha256", required=True)
    parser.add_argument("--built-by", required=True)
    args = parser.parse_args()
    result = build_successor_addendum(
        original_package=args.original_package,
        addendum_root=args.addendum_root,
        release_sha=args.release_sha,
        candidate_id=args.candidate_id,
        candidate_manifest_sha256=args.candidate_manifest_sha256,
        candidate_package_sha256=args.candidate_package_sha256,
        built_by=args.built_by,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

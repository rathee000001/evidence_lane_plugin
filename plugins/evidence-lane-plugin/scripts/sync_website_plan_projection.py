from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.hashing import atomic_write_bytes
from evidence_lane_plugin.store import ProjectStore
from evidence_lane_plugin.website_plan_projection import (
    WEBSITE_PLAN_PROJECTION_PATH,
    WEBSITE_PUBLIC_METADATA_PATH,
    build_website_plan_projection,
    expected_public_plan_lane,
    extract_backlog_status,
    validate_website_plan_projection,
)

REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / WEBSITE_PLAN_PROJECTION_PATH
DEFAULT_METADATA = REPOSITORY_ROOT / WEBSITE_PUBLIC_METADATA_PATH


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain one JSON object")
    return value


def _pretty_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate the public Delta ledger projection from native PLAN_LANE "
            "authority or verify the committed sealed snapshot."
        )
    )
    parser.add_argument("--input", type=Path, help="Captured native pv_task_backlog JSON")
    parser.add_argument("--project-id", help="Read the project from the durable local store")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    committed = (
        validate_website_plan_projection(_read_json(args.output))
        if args.output.is_file()
        else None
    )
    generated: dict[str, Any] | None = None
    if args.input:
        generated = build_website_plan_projection(
            extract_backlog_status(_read_json(args.input))
        )
    elif args.project_id:
        data_root = args.data_root or Path(
            os.environ.get("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT")
            or Path.home()
            / ".codex"
            / "plugins"
            / "runtime"
            / "evidence-lane-plugin"
        )
        generated = build_website_plan_projection(
            ProjectStore(data_root).backlog_status(args.project_id)
        )

    if generated is None and committed is None:
        raise SystemExit("No committed snapshot exists; update requires --input or --project-id")
    target = generated or committed
    assert target is not None
    metadata = _read_json(args.public_metadata)
    expected_plan_lane = expected_public_plan_lane(target, metadata.get("plan_lane"))

    if args.check:
        if committed is None:
            raise SystemExit("MISSING committed website Plan snapshot")
        if generated is not None and generated != committed:
            raise SystemExit(
                "STALE website Plan snapshot: committed projection does not match input authority"
            )
        if metadata.get("plan_lane") != expected_plan_lane:
            raise SystemExit(
                "STALE public plugin metadata: plan_lane does not match the sealed website snapshot"
            )
    else:
        if generated is None:
            raise SystemExit("Update requires --input or --project-id")
        metadata["plan_lane"] = expected_plan_lane
        atomic_write_bytes(args.output, _pretty_bytes(target))
        atomic_write_bytes(args.public_metadata, _pretty_bytes(metadata))

    print(
        "PASS "
        f"rows={target['row_start']}-{target['row_end']} "
        f"active={target['active_row']} final={target['physically_final_hil_row']} "
        f"snapshot_sha256={target['snapshot_sha256']}"
    )


if __name__ == "__main__":
    main()

"""Project the retained-only v4 tool definitions into the runtime catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"
DEFINITIONS = PLUGIN / "toolchains/tool-definitions.v4.json"
OUTPUT = PLUGIN / "toolchains/tool-catalog.v4.json"
REMOVED_TOOL_IDS = frozenset(
    {
        "Package_sealer",
        "OpenJDK",
        "Jackcess",
        "OneNote_Parser",
        "RapidFuzz",
        "Promptfoo",
        "TruLens",
        "DeepEval",
        "Helicone",
        "Docker",
        "Kubernetes",
        "AWS_Lambda",
        "Google_Cloud_Run",
        "AWS",
        "Azure",
        "Google_Cloud",
        "Vercel_Git_integration",
        "GitHub_Actions",
        "GitHub_MCP_Server",
        "Filesystem_MCP_Server",
        "PostgreSQL_MCP_Server",
        "Slack_MCP_Server",
        "psutil",
    }
)
REMOVED_LANE_IDS = frozenset(
    {
        "mode",
        "analysis",
        "discussion",
        "brain_loader",
        "project_engulf",
        "sqlite_brain",
        "onenote",
        "access",
        "visio",
        "outlook",
        "project",
        "publisher",
    }
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build() -> dict[str, Any]:
    source = json.loads(DEFINITIONS.read_text(encoding="utf-8"))
    entries = source.get("entries")
    if (
        source.get("schema") != "evidence-lane.tool-definitions.v4"
        or source.get("status") != "CURRENT_RETAINED_ONLY"
        or not isinstance(entries, list)
        or source.get("entry_count") != len(entries)
        or len({row.get("tool_id") for row in entries}) != len(entries)
        or any(row.get("lifecycle") != "retained" for row in entries)
        or REMOVED_TOOL_IDS.intersection(row["tool_id"] for row in entries)
        or REMOVED_LANE_IDS.intersection(
            lane for row in entries for lane in row.get("lanes", [])
        )
    ):
        raise RuntimeError("The retained v4 tool definitions do not reconcile")
    return {
        "schema_version": 4,
        "role": (
            "Retained tool declaration catalog; distinct from public MCP actions "
            "and measured execution evidence"
        ),
        "source": "toolchains/tool-definitions.v4.json",
        "source_sha256": sha256(DEFINITIONS),
        "base_entry_count": len(entries),
        "additional_entry_count": 0,
        "counts": dict(Counter(row["lifecycle"] for row in entries)),
        "selection_rule": source["selection_rule"],
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    content = encoded(build())
    changed = not OUTPUT.is_file() or OUTPUT.read_bytes() != content
    if options.check and changed:
        raise RuntimeError("The retained v4 tool catalog requires regeneration")
    if changed:
        OUTPUT.write_bytes(content)
    print(json.dumps({"changed": changed, "check": options.check,
                      "entries": len(json.loads(content)["entries"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Update the user-requested checklist snapshot without claiming database authority."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--active", type=int, required=True)
    parser.add_argument("--path", type=Path, default=Path("step-task-list.json"))
    args = parser.parse_args()
    document = json.loads(args.path.read_text(encoding="utf-8"))
    if not 1 <= args.active <= len(document["steps"]):
        parser.error("Active step must exist in the approved list")
    counts = {"completed": 0, "in_progress": 0, "pending": 0}
    for step in document["steps"]:
        step["status"] = (
            "completed"
            if step["number"] < args.active
            else "in_progress"
            if step["number"] == args.active
            else "pending"
        )
        counts[step["status"]] += 1
    document["status_counts"] = counts
    document["exported_at"] = datetime.now(UTC).isoformat()
    descriptor, temporary = tempfile.mkstemp(
        dir=args.path.parent, prefix=".step-list-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.path)
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    main()

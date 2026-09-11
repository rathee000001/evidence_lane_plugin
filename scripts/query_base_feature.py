"""Bounded read-only source-index lookup before adapting an implementation family."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from review_earlier_code_index import INDEX, OLD_ROOT, PLUGIN_PREFIX


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature", required=True)
    parser.add_argument("--module", action="append", required=True)
    parser.add_argument("--symbol", default="")
    args = parser.parse_args()
    if (not re.fullmatch(r"[a-z][a-z0-9-]{1,60}", args.feature) or len(args.module) > 10
            or any(not re.fullmatch(r"[a-z_][a-z0-9_]*", item) for item in args.module)):
        parser.error("Use a bounded feature ID and up to ten module names")
    paths = [PLUGIN_PREFIX + "src/evidence_lane_plugin/" + name + ".py" for name in args.module]
    connection = sqlite3.connect(INDEX.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    deadline = time.monotonic() + 15
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    records = []
    try:
        connection.execute("BEGIN")
        for path in paths:
            row = connection.execute("SELECT file_id,current_sha256 FROM code_file WHERE canonical_path=?", (path,)).fetchone()
            if row is None:
                records.append({"path": path, "index_state": "not_indexed"})
                continue
            source = OLD_ROOT / path
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            symbols = [dict(item) for item in connection.execute(
                "SELECT s.symbol_name,s.start_line,s.signature FROM code_symbol s "
                "JOIN code_file_version v ON v.file_version_id=s.file_version_id "
                "WHERE s.file_id=? AND v.raw_file_sha256=? AND s.symbol_name LIKE ? ORDER BY s.start_line LIMIT 80",
                (row["file_id"], row["current_sha256"], "%" + args.symbol + "%"))]
            records.append({"path": path, "indexed_sha256": row["current_sha256"], "source_sha256": digest,
                            "index_matches_source": row["current_sha256"] == digest, "symbols": symbols})
    finally:
        connection.close()
    record = {"feature": args.feature, "index": str(INDEX), "read_mode": "read_only_in_place", "modules": records}
    output = Path(__file__).resolve().parents[1] / ".work/verification/base-feature-queries"
    output.mkdir(exist_ok=True, parents=True)
    (output / (args.feature + ".json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()

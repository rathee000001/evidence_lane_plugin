"""Inspect exact indexed source files in place before adapting a v3 component."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3

from review_earlier_code_index import INDEX, OLD_ROOT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    arguments = parser.parse_args()
    results = []
    connection = sqlite3.connect(INDEX.as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("BEGIN")
        for path in arguments.paths:
            row = connection.execute(
                "SELECT file_id,canonical_path,current_sha256 FROM code_file WHERE canonical_path=?",
                (path,),
            ).fetchone()
            if row is None:
                raise RuntimeError("The exact source path is not indexed: " + path)
            source = (OLD_ROOT / path).resolve(strict=True)
            if not source.is_relative_to(OLD_ROOT.resolve()):
                raise RuntimeError("Source path escaped the selected base")
            with source.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            symbols = [dict(item) for item in connection.execute(
                "SELECT s.symbol_name,s.symbol_type,s.start_line,s.signature FROM code_symbol s "
                "JOIN code_file_version v ON v.file_version_id=s.file_version_id "
                "WHERE s.file_id=? AND v.raw_file_sha256=? ORDER BY s.start_line LIMIT 100",
                (row["file_id"], row["current_sha256"]),
            )]
            results.append({"path": path, "indexed_sha256": row["current_sha256"],
                            "source_sha256": actual, "matches": actual == row["current_sha256"],
                            "symbols": symbols})
        print(json.dumps({"access": "read_only_in_place", "files": results}))
    finally:
        connection.close()


if __name__ == "__main__":
    main()

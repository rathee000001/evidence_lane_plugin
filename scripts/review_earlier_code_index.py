"""Read the user-selected code index in place and reconcile it with live source."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

INDEX = Path("C:/Users/rathe/Downloads/_0000/earlier_version/project/sectors/local_code/local_code_sector_v001.sqlite")
OLD_ROOT = Path("F:/test codex")
PLUGIN_PREFIX = "plugins/evidence-lane-plugin/"


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    before = INDEX.stat()
    connection = sqlite3.connect(INDEX.as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    deadline = time.monotonic() + 30
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    try:
        connection.execute("BEGIN")
        counts = {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                  for table in ("code_file", "code_symbol", "code_chunk", "code_import_edge",
                                "dependency_item", "workflow_node", "source_file")}
        manifest = [dict(row) for row in connection.execute(
            "SELECT sector_id,lane_key,version,status,created_at FROM sector_manifest")]
        files = [dict(row) for row in connection.execute(
            "SELECT file_id,canonical_path,language,current_sha256 FROM code_file "
            "WHERE canonical_path GLOB ? OR canonical_path GLOB 'tests/test_*.py' "
            "OR canonical_path GLOB 'scripts/*.py' ORDER BY canonical_path", (PLUGIN_PREFIX + "*",))]
        symbols = [dict(row) for row in connection.execute(
            "SELECT f.canonical_path,s.symbol_name,s.symbol_type,s.start_line,s.signature "
            "FROM code_symbol s JOIN code_file f ON f.file_id=s.file_id "
            "JOIN code_file_version v ON v.file_version_id=s.file_version_id "
            "WHERE v.raw_file_sha256=f.current_sha256 AND (f.canonical_path GLOB ? "
            "OR f.canonical_path GLOB 'tests/test_*.py' OR f.canonical_path GLOB 'scripts/*.py') "
            "ORDER BY f.canonical_path,s.start_line", (PLUGIN_PREFIX + "*",))]
        imports = [dict(row) for row in connection.execute(
            "SELECT from_path,import_target,import_type,line_number FROM code_import_edge "
            "WHERE from_path GLOB ? OR from_path GLOB 'tests/test_*.py' "
            "OR from_path GLOB 'scripts/*.py' ORDER BY from_path,line_number", (PLUGIN_PREFIX + "*",))]
        coverage = [dict(row) for row in connection.execute(
            "SELECT coverage_status,count(*) files FROM source_byte_coverage GROUP BY coverage_status")]
    finally:
        connection.close()
    by_path = {}
    for row in files:
        path = row["canonical_path"]
        candidate = (OLD_ROOT / path).resolve()
        if not candidate.is_relative_to(OLD_ROOT.resolve()):
            raise RuntimeError("Indexed path is outside the explicitly selected source")
        actual = file_digest(candidate) if candidate.is_file() else None
        by_path[path] = {"indexed_sha256": row["current_sha256"], "current_sha256": actual,
                         "index_matches_source": actual == row["current_sha256"],
                         "language": row["language"], "symbols": [], "imports": []}
    for row in symbols:
        by_path[row["canonical_path"]]["symbols"].append({key: value for key, value in row.items() if key != "canonical_path"})
    for row in imports:
        if row["from_path"] in by_path:
            by_path[row["from_path"]]["imports"].append({key: value for key, value in row.items() if key != "from_path"})
    after = INDEX.stat()
    stable = before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
    if not stable:
        raise RuntimeError("The source index changed during review; rerun against a stable read snapshot")
    document = {
        "schema_version": 1, "reviewed_at": datetime.now(UTC).isoformat(),
        "database": {"path": str(INDEX), "bytes": after.st_size, "sha256": file_digest(INDEX),
                     "mode": "read_only_in_place", "metadata_stable_during_query": stable},
        "manifest_as_observed": manifest, "index_counts": counts, "byte_coverage": coverage,
        "scope": "Exact active plugin prefix, repository tests and scripts; historical .runtime copies excluded",
        "limitations": [
            "The sector manifest still says SCHEMA_READY; this is reported as observed, not upgraded to build acceptance.",
            "Dependency-item and workflow tables are empty; imports, symbols, chunks and live source are the available evidence.",
            "A matching source hash establishes the indexed file version, not feature correctness or installed execution.",
        ],
        "files": by_path,
    }
    destination = root / ".work/verification/earlier-code-index-review.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"index_counts": counts, "reviewed_files": len(by_path),
                      "reviewed_symbols": len(symbols), "reviewed_imports": len(imports),
                      "source_hash_matches": sum(item["index_matches_source"] for item in by_path.values()),
                      "source_hash_mismatches": [path for path, item in by_path.items() if not item["index_matches_source"]],
                      "file_groups": {group: sum(path.startswith(PLUGIN_PREFIX + group + "/") for path in by_path)
                                      for group in ("src", "scripts", "tests", "skills", "sdk", "mcp", "schemas", "authorities", "toolchains")}}))


if __name__ == "__main__":
    main()

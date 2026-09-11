"""Query the separately selected plugin-produced project without opening a lifecycle."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path("F:/EvidenceLaneProjects/Codex_Evidence_lane_Plugin")
SOURCE = Path("F:/test codex")


def connect(path: Path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("BEGIN")
    return connection


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    database = PROJECT / "sectors/local_code/local_code_sector_v001.sqlite"
    connection = connect(database)
    try:
        metadata = dict(connection.execute("SELECT key,value FROM lane_meta"))
        counts = {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                  for table in ("source_registry", "chunk_index", "structured_fact", "chunk_history", "git_commit_registry")}
        refresh = dict(connection.execute("SELECT build_mode,parent_pv,proposed_pv,unchanged_reuse,changed_rebuild,new_register,blocked_unsupported,recorded_at FROM refresh_receipt ORDER BY receipt_id DESC LIMIT 1").fetchone())
        tools = [dict(row) for row in connection.execute(
            "SELECT r.tool,r.role_class,r.implementation_owner,e.execution_state,e.selection_state,e.condition_state,e.recorded_at "
            "FROM tool_route_contract r LEFT JOIN tool_execution_receipt e ON e.tool=r.tool ORDER BY r.tool")]
        registered = [dict(row) for row in connection.execute(
            "SELECT path,sha256,size_bytes,parser_state FROM source_registry "
            "WHERE path GLOB 'plugins/evidence-lane-plugin/src/evidence_lane_plugin/*' ORDER BY path")]
        queries = []
        for term, filename in (
            ('"GRANT_LIVE" OR "role_schema"', "connector_governance.py"),
            ('"compress_exact_bytes" OR "source_registry_is_compact"', "compact_storage.py"),
            ('"reciprocal_rank_fusion" OR "rank_bm25_candidates"', "hybrid_retrieval.py"),
            ('"reset_aware" OR "component_token"', "goal_usage.py"),
        ):
            rows = connection.execute(
                "SELECT s.path,i.locator,i.ordinal,c.sha256,c.size_bytes,c.compression,c.compressed_text "
                "FROM code_chunk_fts JOIN chunk_index i ON i.chunk_id=code_chunk_fts.rowid "
                "JOIN source_registry s ON s.source_id=i.source_id JOIN chunk_content_cas c ON c.sha256=i.sha256 "
                "WHERE code_chunk_fts MATCH ? AND s.path=? ORDER BY bm25(code_chunk_fts) LIMIT 2",
                (term, "plugins/evidence-lane-plugin/src/evidence_lane_plugin/" + filename),
            ).fetchall()
            hits = []
            for row in rows:
                if row["compression"] != "ZLIB_LEVEL_9" or row["size_bytes"] > 32_000:
                    raise RuntimeError("Unsupported or oversized source chunk")
                decompressor = zlib.decompressobj()
                content = decompressor.decompress(row["compressed_text"], row["size_bytes"] + 1)
                if len(content) != row["size_bytes"] or not decompressor.eof or hashlib.sha256(content).hexdigest() != row["sha256"].lower():
                    raise RuntimeError("Source chunk failed its content-address check")
                hits.append({"path": row["path"], "locator": row["locator"], "ordinal": row["ordinal"],
                             "chunk_sha256": row["sha256"], "excerpt": content.decode("utf-8")[:2200]})
            queries.append({"query": term, "source_file": filename, "hits": hits})
    finally:
        connection.close()
    matches, changed = [], []
    for row in registered:
        path = (SOURCE / row["path"]).resolve()
        if not path.is_relative_to(SOURCE.resolve()):
            raise RuntimeError("Source locator escaped the selected workspace")
        current = digest(path) if path.is_file() else None
        result = {"path": row["path"], "indexed_sha256": row["sha256"].lower(), "current_sha256": current}
        (matches if current == row["sha256"].lower() else changed).append(result)
    authority_database = PROJECT / "project_authority/project-authority.sqlite"
    connection = connect(authority_database)
    try:
        registration = [dict(row) for row in connection.execute(
            "SELECT project_id,project_root,registration_state,registered_at,updated_at FROM project_registration")]
        pointers = [dict(row) for row in connection.execute(
            "SELECT generation,project_id,accepted_pv,movement_kind,recorded_at FROM project_pointer_history ORDER BY generation DESC LIMIT 2")]
        members = [dict(row) for row in connection.execute(
            "SELECT authority_kind,authority_id,sqlite_path FROM project_authority_member ORDER BY authority_kind,authority_id")]
    finally:
        connection.close()
    report = {
        "schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
        "access_mode": "read_only_in_place", "lifecycle_invoked": False,
        "lane_database": {"path": str(database), "bytes": database.stat().st_size, "sha256": digest(database)},
        "root_database": {"path": str(authority_database), "sha256": digest(authority_database)},
        "project_registration": registration, "historical_pointer_records": pointers,
        "authority_members": members, "lane_metadata": metadata, "counts": counts,
        "latest_recorded_refresh": refresh, "recorded_tool_execution": tools, "source_queries": queries,
        "source_reconciliation": {"matched": len(matches), "changed": len(changed), "changed_files": changed},
        "interpretation": "The plugin-produced lane supplies source chunks, structured facts, history and per-tool execution records. Its recorded PV12_WORKING snapshot is historical; changed files must be read from the current executable source. These records do not attest this task or activate the old lifecycle.",
    }
    destination = root / ".work/verification/plugin-project-query.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": counts, "latest_refresh": refresh["recorded_at"],
                      "registered_plugin_sources": len(registered), "current_hash_matches": len(matches),
                      "changed_source_count": len(changed), "changed_sources": [row["path"] for row in changed],
                      "fts_hits": {item["source_file"]: len(item["hits"]) for item in queries},
                      "root_registration": registration, "authority_member_count": len(members)}))


if __name__ == "__main__":
    main()

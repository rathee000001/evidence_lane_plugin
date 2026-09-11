"""PowerBi versions and native structure in the physical POWERBI lane only."""

from .migrations import Migration
from .powerbi_parsers import KINDS

POWERBI_TABLES = {kind: "powerbi_" + kind for kind in KINDS}


POWERBI_MIGRATIONS = (
    Migration(
        "powerbi",
        1,
        "Separate immutable powerbi versions, native structure and retrieval",
        (
            """CREATE TABLE powerbi_file(powerbi_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
            """CREATE TABLE powerbi_version(snapshot_id TEXT PRIMARY KEY, powerbi_id TEXT NOT NULL REFERENCES powerbi_file(powerbi_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES powerbi_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(powerbi_id,generation)) STRICT""",
            """CREATE TABLE powerbi_current(powerbi_id TEXT PRIMARY KEY REFERENCES powerbi_file(powerbi_id),
       snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id)) STRICT""",
            """CREATE TABLE powerbi_structure(snapshot_id TEXT PRIMARY KEY REFERENCES powerbi_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT""",
            *(
                f"""CREATE TABLE {table}(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT"""
                for table in sorted(set(POWERBI_TABLES.values()))
            ),
            """CREATE TABLE powerbi_chunk(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT""",
            "CREATE VIRTUAL TABLE powerbi_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)",
            """CREATE TABLE powerbi_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
            "CREATE INDEX powerbi_versions_powerbi ON powerbi_version(powerbi_id,generation)",
        ),
    ),
)

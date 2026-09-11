"""Tableau versions and native structure in the physical TABLEAU lane only."""
from .migrations import Migration
from .tableau_parsers import KINDS

TABLEAU_TABLES = {kind: 'tableau_' + kind for kind in KINDS}


TABLEAU_MIGRATIONS = (Migration('tableau', 1, 'Separate immutable tableau versions, native structure and retrieval', (
    """CREATE TABLE tableau_file(tableau_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
    """CREATE TABLE tableau_version(snapshot_id TEXT PRIMARY KEY, tableau_id TEXT NOT NULL REFERENCES tableau_file(tableau_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES tableau_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(tableau_id,generation)) STRICT""",
    """CREATE TABLE tableau_current(tableau_id TEXT PRIMARY KEY REFERENCES tableau_file(tableau_id),
       snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id)) STRICT""",
    """CREATE TABLE tableau_structure(snapshot_id TEXT PRIMARY KEY REFERENCES tableau_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT""",
    *(f"""CREATE TABLE {table}(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT"""
      for table in sorted(set(TABLEAU_TABLES.values()))),
    """CREATE TABLE tableau_chunk(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT""",
    'CREATE VIRTUAL TABLE tableau_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)',
    """CREATE TABLE tableau_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
    'CREATE INDEX tableau_versions_tableau ON tableau_version(tableau_id,generation)',
)),)

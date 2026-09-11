-- Projection only: the engine applies these owner migrations under its project writer.
-- data v1, digest d936679fb794e612c2d4f9923c37a91750d9eb494c2c6f6c7acefc5f4846f239
CREATE TABLE data_source(source_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
            source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE data_version(snapshot_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES data_source(source_id),
            generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES data_version(snapshot_id),
            manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
            facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(source_id,generation)) STRICT;
CREATE TABLE data_current(source_id TEXT PRIMARY KEY REFERENCES data_source(source_id),
            snapshot_id TEXT NOT NULL REFERENCES data_version(snapshot_id)) STRICT;
CREATE TABLE data_column(snapshot_id TEXT NOT NULL REFERENCES data_version(snapshot_id),
            item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE data_lineage(snapshot_id TEXT NOT NULL REFERENCES data_version(snapshot_id),
            item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE data_sample(snapshot_id TEXT NOT NULL REFERENCES data_version(snapshot_id),
            item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE data_table(snapshot_id TEXT NOT NULL REFERENCES data_version(snapshot_id),
            item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE data_chunk(snapshot_id TEXT NOT NULL REFERENCES data_version(snapshot_id),
            chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE data_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE TABLE data_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES data_version(snapshot_id),
            destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL, effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE data_derivative(derivative_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES data_version(snapshot_id),
            kind TEXT NOT NULL, manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT;
CREATE INDEX data_versions_source ON data_version(source_id,generation);
-- dataselector v1, digest c996a6d7c1a1c11e5f0d52e14adb4761f6a2130c206564a8c9ac349cdc9b1cf8
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

-- Projection only: the engine applies these owner migrations under its project writer.
-- artifacts v1, digest 72b74fd585227fcb022c20d034d07a5a67fdef936d609f1628b848076ca78f53
CREATE TABLE artifact_file(source_id TEXT PRIMARY KEY,logical_name TEXT NOT NULL,
            source_path TEXT NOT NULL,created_at TEXT NOT NULL) STRICT;
CREATE TABLE artifact_version(snapshot_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES artifact_file(source_id),
            generation INTEGER NOT NULL CHECK(generation>0),
            previous_snapshot TEXT REFERENCES artifact_version(snapshot_id),
            manifest_object TEXT NOT NULL REFERENCES objects(digest),raw_object TEXT NOT NULL REFERENCES objects(digest),
            facts_object TEXT NOT NULL REFERENCES objects(digest),parser_contract TEXT NOT NULL,
            created_at TEXT NOT NULL,UNIQUE(source_id,generation)) STRICT;
CREATE TABLE artifact_current(source_id TEXT PRIMARY KEY REFERENCES artifact_file(source_id),
            snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id)) STRICT;
CREATE TABLE artifact_archive_member(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_media_probe(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_metadata(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_native_fact(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_relation_edge(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_review_required(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_sqlite_receipt(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_sqlite_relationship(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_sqlite_row(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_sqlite_schema_object(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_sqlite_table(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_text_extract(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE project_artifact(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE artifact_chunk(snapshot_id TEXT NOT NULL REFERENCES artifact_version(snapshot_id),
            chunk_id TEXT NOT NULL,item_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
            text_object TEXT NOT NULL REFERENCES objects(digest),PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE artifact_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE INDEX artifact_versions_source ON artifact_version(source_id,generation);
-- artifactsselector v1, digest 1fe9c3069807f51dcf76a37fdf93d414e0c3feb5d76216f6d70003aded928ca6
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

-- Projection only: the engine applies these owner migrations under its project writer.
-- custom v1, digest 9c0a24403f1a5a2ab9ce6e6accbf30ca181fb8be89a8b234754d1f0fa959d4c1
CREATE TABLE custom_file(source_id TEXT PRIMARY KEY,logical_name TEXT NOT NULL,
            source_path TEXT NOT NULL,created_at TEXT NOT NULL) STRICT;
CREATE TABLE custom_version(snapshot_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES custom_file(source_id),
            generation INTEGER NOT NULL CHECK(generation>0),
            previous_snapshot TEXT REFERENCES custom_version(snapshot_id),
            manifest_object TEXT NOT NULL REFERENCES objects(digest),raw_object TEXT NOT NULL REFERENCES objects(digest),
            facts_object TEXT NOT NULL REFERENCES objects(digest),parser_contract TEXT NOT NULL,
            created_at TEXT NOT NULL,UNIQUE(source_id,generation)) STRICT;
CREATE TABLE custom_current(source_id TEXT PRIMARY KEY REFERENCES custom_file(source_id),
            snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id)) STRICT;
CREATE TABLE custom_archive_member(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_decision(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_evidence(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_item(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_native_fact(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_next_action(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_review_required(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_source(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_sqlite_receipt(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_sqlite_relationship(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_sqlite_row(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_sqlite_schema_object(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_sqlite_table(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE custom_chunk(snapshot_id TEXT NOT NULL REFERENCES custom_version(snapshot_id),
            chunk_id TEXT NOT NULL,item_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
            text_object TEXT NOT NULL REFERENCES objects(digest),PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE custom_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE INDEX custom_versions_source ON custom_version(source_id,generation);
-- customselector v1, digest 4bb68fa5309664c31a6759c775eb34f497d0b74aaa9d042b058c1381dc494f38
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

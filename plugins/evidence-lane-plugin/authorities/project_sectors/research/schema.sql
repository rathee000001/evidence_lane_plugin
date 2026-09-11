-- Projection only: the engine applies these owner migrations under its project writer.
-- research v1, digest 44e56e3a222240866dd918a205c0d8e84fc50840c08d47a3323b02bf23385b88
CREATE TABLE research_file(source_id TEXT PRIMARY KEY,logical_name TEXT NOT NULL,
            source_path TEXT NOT NULL,created_at TEXT NOT NULL) STRICT;
CREATE TABLE research_version(snapshot_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES research_file(source_id),
            generation INTEGER NOT NULL CHECK(generation>0),
            previous_snapshot TEXT REFERENCES research_version(snapshot_id),
            manifest_object TEXT NOT NULL REFERENCES objects(digest),raw_object TEXT NOT NULL REFERENCES objects(digest),
            facts_object TEXT NOT NULL REFERENCES objects(digest),parser_contract TEXT NOT NULL,
            created_at TEXT NOT NULL,UNIQUE(source_id,generation)) STRICT;
CREATE TABLE research_current(source_id TEXT PRIMARY KEY REFERENCES research_file(source_id),
            snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id)) STRICT;
CREATE TABLE research_archive_member(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_citation(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_evidence(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_finding(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_hypothesis(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_limitation(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_method(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_native_fact(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_open_question(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_question(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_receipt(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_review_required(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_source(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_sqlite_receipt(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_sqlite_relationship(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_sqlite_row(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_sqlite_schema_object(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_sqlite_table(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_chunk(snapshot_id TEXT NOT NULL REFERENCES research_version(snapshot_id),
            chunk_id TEXT NOT NULL,item_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
            text_object TEXT NOT NULL REFERENCES objects(digest),PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE research_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE INDEX research_versions_source ON research_version(source_id,generation);
-- research v2, digest 156185210e2c72b1d022d8f1c8679a46f35cff276bef77f96546b6464d0d017f
CREATE TABLE research_web_source(source_id TEXT PRIMARY KEY,url TEXT NOT NULL,created_at TEXT NOT NULL) STRICT;
CREATE TABLE research_web_version(snapshot_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES research_web_source(source_id),generation INTEGER NOT NULL CHECK(generation>0),
        previous_snapshot TEXT REFERENCES research_web_version(snapshot_id),
        manifest_object TEXT NOT NULL REFERENCES objects(digest),body_object TEXT NOT NULL REFERENCES objects(digest),
        wire_object TEXT NOT NULL REFERENCES objects(digest),facts_object TEXT NOT NULL REFERENCES objects(digest),
        extraction_object TEXT NOT NULL REFERENCES objects(digest),parser_contract TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(source_id,generation)) STRICT;
CREATE TABLE research_web_current(source_id TEXT PRIMARY KEY REFERENCES research_web_source(source_id),
        snapshot_id TEXT NOT NULL REFERENCES research_web_version(snapshot_id)) STRICT;
CREATE TABLE research_web_fact(snapshot_id TEXT NOT NULL REFERENCES research_web_version(snapshot_id),
        item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_web_chunk(snapshot_id TEXT NOT NULL REFERENCES research_web_version(snapshot_id),
        chunk_id TEXT NOT NULL,item_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
        text_object TEXT NOT NULL REFERENCES objects(digest),PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE research_web_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE INDEX research_web_versions_source ON research_web_version(source_id,generation);
-- research v3, digest 8e51760c907d3e04345d10f7b27e37d76e160d0d72e5fb4d6e26fe02046a89a9
CREATE TABLE research_discovery_source(source_id TEXT PRIMARY KEY,parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)),
        created_at TEXT NOT NULL) STRICT;
CREATE TABLE research_discovery_version(snapshot_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES research_discovery_source(source_id),generation INTEGER NOT NULL CHECK(generation>0),
        previous_snapshot TEXT REFERENCES research_discovery_version(snapshot_id),
        manifest_object TEXT NOT NULL REFERENCES objects(digest),raw_object TEXT NOT NULL REFERENCES objects(digest),
        facts_object TEXT NOT NULL REFERENCES objects(digest),parser_contract TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(source_id,generation)) STRICT;
CREATE TABLE research_discovery_current(source_id TEXT PRIMARY KEY REFERENCES research_discovery_source(source_id),
        snapshot_id TEXT NOT NULL REFERENCES research_discovery_version(snapshot_id)) STRICT;
CREATE TABLE research_discovery_fact(snapshot_id TEXT NOT NULL REFERENCES research_discovery_version(snapshot_id),
        item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE research_discovery_chunk(snapshot_id TEXT NOT NULL REFERENCES research_discovery_version(snapshot_id),
        chunk_id TEXT NOT NULL,item_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
        text_object TEXT NOT NULL REFERENCES objects(digest),PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE research_discovery_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE INDEX research_discovery_versions_source ON research_discovery_version(source_id,generation);
-- researchselector v1, digest 9487a4ad61c91f18dea8aa74d3bbe976f80f21d82f4abfe8e008f8353dad4377
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

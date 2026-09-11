-- Projection only: the engine applies these owner migrations under its project writer.
-- powerbi v1, digest dcfdd040d5cd009173fdbe795799239ae481ab0bf69e2348caaf0d9588ec16b4
CREATE TABLE powerbi_file(powerbi_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE powerbi_version(snapshot_id TEXT PRIMARY KEY, powerbi_id TEXT NOT NULL REFERENCES powerbi_file(powerbi_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES powerbi_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(powerbi_id,generation)) STRICT;
CREATE TABLE powerbi_current(powerbi_id TEXT PRIMARY KEY REFERENCES powerbi_file(powerbi_id),
       snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id)) STRICT;
CREATE TABLE powerbi_structure(snapshot_id TEXT PRIMARY KEY REFERENCES powerbi_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT;
CREATE TABLE powerbi_annotation(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_bookmark(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_column(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_culture(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_data_source(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_expression(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_filter(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_hierarchy(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_measure(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_model(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_model_metadata(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_model_row(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_opaque(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_package_member(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_page(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_partition(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_perspective(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_project(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_reference(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_relationship(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_report(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_role(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_table(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_theme(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_visual(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE powerbi_chunk(snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE powerbi_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE TABLE powerbi_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES powerbi_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE INDEX powerbi_versions_powerbi ON powerbi_version(powerbi_id,generation);
-- powerbiselector v1, digest 8aad37ba2da9f95232fc6d219b7bfebecbf62472f232fe6c405b81211a60d362
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

-- Projection only: the engine applies these owner migrations under its project writer.
-- tableau v1, digest c7bef9d7c5a1c72426cdb64ca79cd84914aa671d6cc16ca12e946aa029a1ab87
CREATE TABLE tableau_file(tableau_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE tableau_version(snapshot_id TEXT PRIMARY KEY, tableau_id TEXT NOT NULL REFERENCES tableau_file(tableau_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES tableau_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(tableau_id,generation)) STRICT;
CREATE TABLE tableau_current(tableau_id TEXT PRIMARY KEY REFERENCES tableau_file(tableau_id),
       snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id)) STRICT;
CREATE TABLE tableau_structure(snapshot_id TEXT PRIMARY KEY REFERENCES tableau_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT;
CREATE TABLE tableau_calculation(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_column(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_connection(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_dashboard(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_datasource(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_filter(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_hyper_column(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_hyper_row(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_hyper_schema(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_hyper_table(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_layout(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_mark(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_opaque(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_package_member(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_parameter(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_relationship(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_sheet(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_story(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_workbook(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE tableau_chunk(snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE tableau_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE TABLE tableau_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES tableau_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE INDEX tableau_versions_tableau ON tableau_version(tableau_id,generation);
-- tableauselector v1, digest a338dad665e8d48856eeda08c03b06c5c648071723f3f3ce74fa97fa0a6cc40f
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

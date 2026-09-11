-- Projection only: the engine applies these owner migrations under its project writer.
-- docs v1, digest f84601b682a83ba60e52cca1c2ed14be94ffcff5be784ed9153f5422c2dd5c29
CREATE TABLE doc_file(document_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE doc_version(snapshot_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES doc_file(document_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES doc_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(document_id,generation)) STRICT;
CREATE TABLE doc_current(document_id TEXT PRIMARY KEY REFERENCES doc_file(document_id),
       snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id)) STRICT;
CREATE TABLE doc_structure(snapshot_id TEXT PRIMARY KEY REFERENCES doc_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT;
CREATE TABLE doc_bookmark(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_content_control(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_embedded_object(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_field(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_heading(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_hyperlink(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_image_reference(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_paragraph(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_relationship(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_revision(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_table_extract(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE doc_chunk(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE doc_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE TABLE doc_render(render_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT;
CREATE TABLE doc_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE INDEX doc_versions_document ON doc_version(document_id,generation);
-- docs v2, digest 57748c24417eadbb119e7087f54220dd341aba8156c55258c1911d0bfa2f3ccc
CREATE TABLE docling_extraction(enrichment_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT;
-- docsselector v1, digest c107d43c9784272e42121919513ae8de43e9d8a6bfcc41ee115442310ac2bc99
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

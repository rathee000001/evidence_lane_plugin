-- Projection only: the engine applies these owner migrations under its project writer.
-- ppt v1, digest 5e4598879ea87c00fe752e5c59cdcdfbc179e0a94bf1dce203b74070b4a42a91
CREATE TABLE ppt_file(presentation_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE ppt_version(snapshot_id TEXT PRIMARY KEY, presentation_id TEXT NOT NULL REFERENCES ppt_file(presentation_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES ppt_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(presentation_id,generation)) STRICT;
CREATE TABLE ppt_current(presentation_id TEXT PRIMARY KEY REFERENCES ppt_file(presentation_id),
       snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id)) STRICT;
CREATE TABLE ppt_structure(snapshot_id TEXT PRIMARY KEY REFERENCES ppt_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT;
CREATE TABLE ppt_chart(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE ppt_image_reference(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE ppt_notes(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE ppt_shape(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE ppt_slide(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE ppt_slide_relationship(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE ppt_table(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE ppt_text_block(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT;
CREATE TABLE ppt_chunk(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT;
CREATE VIRTUAL TABLE ppt_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61);
CREATE TABLE ppt_render(render_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT;
CREATE TABLE ppt_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE INDEX ppt_versions_presentation ON ppt_version(presentation_id,generation);
CREATE TABLE ppt_enrichment(enrichment_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT;
-- pptselector v1, digest 998eab50a277bf8debd7a7b586e0976f77c10571129cc35ac5c83c5c23238fb6
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

-- Projection only: the engine applies these owner migrations under its project writer.
-- localcode v1, digest c2d2b57b9c8f604cd99f720b4ca75822690d277b0d10680e90c87921cc24c0d0
CREATE TABLE code_repo(repo_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
       source_root TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE code_snapshot(snapshot_id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES code_repo(repo_id),
       scope_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
       previous_snapshot TEXT REFERENCES code_snapshot(snapshot_id), manifest_object TEXT NOT NULL REFERENCES objects(digest),
       parser_contract TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(scope_id,generation)) STRICT;
CREATE TABLE code_current(scope_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES code_snapshot(snapshot_id)) STRICT;
CREATE TABLE code_file(file_id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES code_repo(repo_id),
       canonical_path TEXT NOT NULL, UNIQUE(repo_id,canonical_path)) STRICT;
CREATE TABLE code_file_version(version_id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES code_file(file_id),
       raw_sha256 TEXT NOT NULL REFERENCES objects(digest), size_bytes INTEGER NOT NULL, encoding TEXT,
       language TEXT NOT NULL, parser_contract TEXT NOT NULL, parser_state TEXT NOT NULL,
       facts_digest TEXT NOT NULL REFERENCES objects(digest)) STRICT;
CREATE TABLE code_snapshot_file(snapshot_id TEXT NOT NULL REFERENCES code_snapshot(snapshot_id),
       path TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
       PRIMARY KEY(snapshot_id,path)) STRICT;
CREATE TABLE code_chunk(chunk_id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
       ordinal INTEGER NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
       content_object TEXT NOT NULL REFERENCES objects(digest), UNIQUE(version_id,ordinal)) STRICT;
CREATE VIRTUAL TABLE code_chunk_fts USING fts5(chunk_id UNINDEXED, text_content, tokenize='unicode61');
CREATE TABLE code_symbol(record_id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, start_line INTEGER NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), UNIQUE(version_id,ordinal)) STRICT;
CREATE TABLE code_import(record_id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, start_line INTEGER NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), UNIQUE(version_id,ordinal)) STRICT;
CREATE TABLE code_call(record_id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, start_line INTEGER NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), UNIQUE(version_id,ordinal)) STRICT;
CREATE TABLE code_route(record_id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, start_line INTEGER NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), UNIQUE(version_id,ordinal)) STRICT;
CREATE TABLE code_dependency(record_id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, start_line INTEGER NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), UNIQUE(version_id,ordinal)) STRICT;
CREATE TABLE code_parser_receipt(record_id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, start_line INTEGER NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), UNIQUE(version_id,ordinal)) STRICT;
CREATE TABLE code_parser_diagnostic(record_id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES code_file_version(version_id),
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, start_line INTEGER NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), UNIQUE(version_id,ordinal)) STRICT;
CREATE TABLE code_import_edge(snapshot_id TEXT NOT NULL REFERENCES code_snapshot(snapshot_id),
       from_path TEXT NOT NULL, import_target TEXT NOT NULL, to_path TEXT,
       line_number INTEGER NOT NULL, resolution TEXT NOT NULL,
       PRIMARY KEY(snapshot_id,from_path,import_target,line_number)) STRICT;
CREATE TABLE code_change(snapshot_id TEXT NOT NULL REFERENCES code_snapshot(snapshot_id), path TEXT NOT NULL,
       change_kind TEXT NOT NULL CHECK(change_kind IN ('added','modified','deleted','unchanged')),
       before_sha256 TEXT, after_sha256 TEXT, PRIMARY KEY(snapshot_id,path)) STRICT;
CREATE TABLE code_git_reference(snapshot_id TEXT PRIMARY KEY REFERENCES code_snapshot(snapshot_id),
       source_batch_id TEXT NOT NULL, source_git_snapshot_id TEXT NOT NULL,
       reference_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json))) STRICT;
CREATE TABLE code_mutation(mutation_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES code_snapshot(snapshot_id),
       path TEXT NOT NULL, before_object TEXT NOT NULL REFERENCES objects(digest),
       after_object TEXT NOT NULL REFERENCES objects(digest), effect_id TEXT NOT NULL,
       job_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE INDEX code_chunk_version ON code_chunk(version_id,ordinal);
CREATE INDEX code_snapshot_versions ON code_snapshot_file(version_id,snapshot_id);
CREATE INDEX code_import_reverse ON code_import_edge(snapshot_id,to_path,from_path);
-- localcode v2, digest 29fe79591cd63b7d8151efd3154bb880c45a2a387d1dd4371e6771be68b0529d
CREATE TABLE code_embedding_run(run_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES code_snapshot(snapshot_id),
               manifest_object TEXT NOT NULL REFERENCES objects(digest), model_id TEXT NOT NULL,
               asset_identity TEXT NOT NULL, chunk_count INTEGER NOT NULL, created_at TEXT NOT NULL) STRICT;
CREATE TABLE code_embedding(run_id TEXT NOT NULL REFERENCES code_embedding_run(run_id),
               chunk_id TEXT NOT NULL REFERENCES code_chunk(chunk_id), vector BLOB NOT NULL CHECK(length(vector)=1536),
               vector_sha256 TEXT NOT NULL, PRIMARY KEY(run_id,chunk_id)) STRICT;
-- localcode v3, digest 0b71ff5a2a2f43b87adbf99a8082f4e01a57b9672810ca7c4fd0617561220131
CREATE TABLE code_import_edge_previous AS SELECT * FROM code_import_edge;
DROP INDEX code_import_reverse;
DROP TABLE code_import_edge;
CREATE TABLE code_import_edge(snapshot_id TEXT NOT NULL REFERENCES code_snapshot(snapshot_id),
               from_path TEXT NOT NULL, import_target TEXT NOT NULL, to_path TEXT,
               line_number INTEGER NOT NULL, resolution TEXT NOT NULL, edge_key TEXT NOT NULL,
               PRIMARY KEY(snapshot_id,edge_key)) STRICT;
INSERT INTO code_import_edge SELECT snapshot_id,from_path,import_target,to_path,line_number,resolution,
               json_array(from_path,import_target,to_path,line_number,resolution) FROM code_import_edge_previous;
DROP TABLE code_import_edge_previous;
CREATE INDEX code_import_reverse ON code_import_edge(snapshot_id,to_path,from_path);
-- localcodeselector v1, digest 368e6d500fb900847c7ac388a185903852af05426e97d18a61fb11ba51260d80
CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT;

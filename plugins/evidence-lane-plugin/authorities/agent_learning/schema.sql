-- Generated from the canonical authority SQLite builder.
-- FTS5 shadow tables are intentionally omitted; SQLite creates them.

CREATE TABLE authority_index_content_cas(
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            compression TEXT NOT NULL,
            compressed_bytes BLOB NOT NULL,
            first_seen_at TEXT NOT NULL
        ) STRICT;

CREATE VIRTUAL TABLE authority_index_fts USING fts5(
            node_id UNINDEXED,
            authority_id UNINDEXED,
            source_table UNINDEXED,
            source_identity UNINDEXED,
            text_content,
            content='',
            contentless_delete=1,
            tokenize='unicode61'
        );

CREATE TABLE authority_index_node(
            node_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES authority_index_source(source_id)
                ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            text_sha256 TEXT NOT NULL
                REFERENCES authority_index_content_cas(sha256),
            metadata_sha256 TEXT NOT NULL
                REFERENCES authority_index_content_cas(sha256),
            UNIQUE(source_id, ordinal)
        ) STRICT;

CREATE TABLE authority_index_refresh_receipt(
            sequence INTEGER PRIMARY KEY,
            authority_id TEXT NOT NULL,
            source_count INTEGER NOT NULL,
            node_count INTEGER NOT NULL,
            indexed_table_count INTEGER NOT NULL,
            llama_index_core_version TEXT NOT NULL,
            chunk_size_tokens INTEGER NOT NULL,
            chunk_overlap_tokens INTEGER NOT NULL,
            prior_receipt_sha256 TEXT,
            recorded_at TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL UNIQUE,
            receipt_json TEXT NOT NULL
        ) STRICT;

CREATE TABLE authority_index_source(
            source_id TEXT PRIMARY KEY,
            authority_id TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_identity TEXT NOT NULL,
            source_text_sha256 TEXT NOT NULL,
            metadata_sha256 TEXT NOT NULL
                REFERENCES authority_index_content_cas(sha256),
            recorded_at TEXT NOT NULL,
            UNIQUE(authority_id, source_table, source_identity)
        ) STRICT;

CREATE TABLE learning_candidate(
    candidate_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    candidate_sha256 TEXT NOT NULL UNIQUE,
    dedup_key_sha256 TEXT NOT NULL,
    tier TEXT NOT NULL,
    lesson_type TEXT NOT NULL,
    expires_at TEXT,
    candidate_json TEXT NOT NULL
) STRICT;

CREATE VIRTUAL TABLE learning_candidate_fts USING fts5(
    candidate_id UNINDEXED,
    project_id UNINDEXED,
    statement,
    lesson_type,
    scope_text,
    tokenize='unicode61'
);

CREATE TABLE learning_decision_receipt(
    decision_key_sha256 TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL
        REFERENCES learning_candidate(candidate_id),
    receipt_sha256 TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL
) STRICT;

CREATE TABLE learning_event(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL
        REFERENCES learning_candidate(candidate_id),
    event_type TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    decision_key_sha256 TEXT,
    event_sha256 TEXT NOT NULL UNIQUE,
    event_json TEXT NOT NULL
) STRICT;

CREATE TABLE learning_pointer_history(
    project_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation > 0),
    candidate_id TEXT NOT NULL
        REFERENCES learning_candidate(candidate_id),
    candidate_sha256 TEXT NOT NULL,
    prior_generation INTEGER NOT NULL CHECK(prior_generation >= 0),
    reason TEXT NOT NULL,
    pointer_sha256 TEXT NOT NULL UNIQUE,
    pointer_json TEXT NOT NULL,
    PRIMARY KEY(project_id,generation)
) STRICT;

CREATE TABLE learning_receipt(
    receipt_sha256 TEXT PRIMARY KEY,
    schema_id TEXT NOT NULL,
    receipt_kind TEXT NOT NULL,
    project_id TEXT NOT NULL,
    candidate_id TEXT REFERENCES learning_candidate(candidate_id),
    occurred_at TEXT,
    receipt_json TEXT NOT NULL
) STRICT;

CREATE TABLE learning_schema_metadata(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    schema_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version > 0),
    ddl_sha256 TEXT NOT NULL,
    schema_signature_sha256 TEXT NOT NULL
) STRICT;

CREATE TABLE memory_edge(
    edge_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_locator_id TEXT NOT NULL REFERENCES memory_locator(locator_id),
    target_locator_id TEXT NOT NULL REFERENCES memory_locator(locator_id),
    edge_type TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    edge_sha256 TEXT NOT NULL UNIQUE,
    edge_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE memory_locator(
    locator_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    sector TEXT NOT NULL,
    locator_kind TEXT NOT NULL,
    locator_value TEXT NOT NULL,
    revision_sha256 TEXT NOT NULL,
    label TEXT NOT NULL,
    search_text TEXT NOT NULL,
    locator_sha256 TEXT NOT NULL UNIQUE,
    locator_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE VIRTUAL TABLE memory_locator_fts USING fts5(
    locator_id UNINDEXED,
    project_id UNINDEXED,
    sector,
    locator_kind,
    label,
    search_text,
    tokenize='unicode61'
);

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE INDEX idx_learning_candidate_dedup
ON learning_candidate(project_id,dedup_key_sha256);

CREATE UNIQUE INDEX idx_learning_decision_once
ON learning_event(decision_key_sha256)
WHERE decision_key_sha256 IS NOT NULL;

CREATE INDEX idx_learning_receipt_project_schema
ON learning_receipt(project_id,schema_id,receipt_sha256);

CREATE INDEX idx_memory_edge_project_source
ON memory_edge(project_id,source_locator_id,edge_type);

CREATE INDEX idx_memory_edge_project_target
ON memory_edge(project_id,target_locator_id,edge_type);

CREATE INDEX idx_memory_locator_project_sector
ON memory_locator(project_id,sector,locator_id);

-- Generated from the canonical authority SQLite builder.
-- FTS5 shadow tables are intentionally omitted; SQLite creates them.

CREATE VIRTUAL TABLE authority_index_fts USING fts5(
            node_id UNINDEXED,
            authority_id UNINDEXED,
            source_table,
            source_identity,
            text_content,
            tokenize='unicode61'
        );

CREATE TABLE authority_index_node(
            node_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES authority_index_source(source_id)
                ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            text_content TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
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
            metadata_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(authority_id, source_table, source_identity)
        ) STRICT;

CREATE TABLE universe_edge(
    edge_id TEXT PRIMARY KEY,
    relation TEXT NOT NULL,
    source_node_id TEXT NOT NULL REFERENCES universe_node(node_id),
    destination_node_id TEXT NOT NULL REFERENCES universe_node(node_id),
    attributes_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    UNIQUE(relation, source_node_id, destination_node_id, attributes_json)
) STRICT;

CREATE VIRTUAL TABLE universe_fts USING fts5(
    node_id UNINDEXED,
    node_kind,
    label,
    canonical_locator,
    attributes
);

CREATE TABLE universe_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE universe_metric(
    metric_key TEXT PRIMARY KEY,
    metric_value INTEGER NOT NULL CHECK(metric_value >= 0)
) STRICT;

CREATE TABLE universe_node(
    node_id TEXT PRIMARY KEY,
    node_kind TEXT NOT NULL,
    canonical_locator TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL
) STRICT;

CREATE TABLE universe_refresh(
    refresh_id TEXT PRIMARY KEY,
    source_fingerprint_sha256 TEXT NOT NULL,
    graph_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL
) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE INDEX universe_edge_destination_idx
ON universe_edge(destination_node_id, relation, source_node_id);

CREATE INDEX universe_edge_source_idx
ON universe_edge(source_node_id, relation, destination_node_id);

CREATE INDEX universe_node_kind_idx
ON universe_node(node_kind, node_id);

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

CREATE TABLE consequence_edge(
    edge_id TEXT PRIMARY KEY,
    relation TEXT NOT NULL,
    source_node_id TEXT NOT NULL REFERENCES consequence_node(node_id),
    destination_node_id TEXT NOT NULL REFERENCES consequence_node(node_id),
    canonical_locator TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL UNIQUE
) STRICT;

CREATE VIRTUAL TABLE consequence_graph_fts USING fts5(
    record_id UNINDEXED,
    record_type UNINDEXED,
    record_kind,
    canonical_locator,
    searchable_text,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE consequence_node(
    node_id TEXT PRIMARY KEY,
    node_kind TEXT NOT NULL,
    canonical_locator TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE graph_metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE INDEX consequence_edge_relation_idx
ON consequence_edge(relation, source_node_id, destination_node_id);

CREATE INDEX consequence_node_kind_idx
ON consequence_node(node_kind, node_id);

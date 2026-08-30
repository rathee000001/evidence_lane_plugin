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

CREATE TABLE canon_backfire_dedup(
    dedup_key_sha256 TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    canon_id TEXT NOT NULL,
    canon_sha256 TEXT NOT NULL
) STRICT;

CREATE TABLE canon_continuity_receipt(
    consumption_key_sha256 TEXT PRIMARY KEY,
    snapshot_sha256 TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL
) STRICT;

CREATE TABLE canon_contract(
    contract_sha256 TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    contract_version INTEGER NOT NULL CHECK(contract_version > 0),
    destination_project_id TEXT NOT NULL,
    destination_task_uuid TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    contract_json TEXT NOT NULL
) STRICT;

CREATE TABLE canon_dispatch(
    dispatch_id TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL
) STRICT;

CREATE TABLE canon_edge(
    edge_id TEXT PRIMARY KEY,
    edge_sha256 TEXT NOT NULL UNIQUE,
    source_node TEXT NOT NULL,
    destination_node TEXT NOT NULL,
    expected_return_contract_sha256 TEXT NOT NULL,
    edge_json TEXT NOT NULL
) STRICT;

CREATE TABLE canon_event(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    canon_id TEXT NOT NULL REFERENCES canon_packet(canon_id),
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    decision_key_sha256 TEXT,
    previous_event_sha256 TEXT,
    event_sha256 TEXT NOT NULL UNIQUE,
    event_json TEXT NOT NULL
) STRICT;

CREATE TABLE canon_packet(
    canon_id TEXT PRIMARY KEY,
    canon_sha256 TEXT NOT NULL UNIQUE,
    owner_project_id TEXT NOT NULL,
    local_role TEXT NOT NULL,
    source_project_id TEXT NOT NULL,
    source_task_uuid TEXT NOT NULL,
    destination_project_id TEXT NOT NULL,
    destination_task_uuid TEXT NOT NULL,
    destination_contract_sha256 TEXT NOT NULL,
    canon_type TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    idempotency_key TEXT NOT NULL,
    current_state TEXT NOT NULL,
    envelope_json TEXT NOT NULL
) STRICT;

CREATE TABLE canon_receipt(
    receipt_sha256 TEXT PRIMARY KEY,
    canon_id TEXT NOT NULL REFERENCES canon_packet(canon_id),
    receipt_type TEXT NOT NULL,
    receipt_json TEXT NOT NULL
) STRICT;

CREATE TABLE canon_schema_migration(
    schema_name TEXT NOT NULL,
    from_version INTEGER NOT NULL CHECK(from_version >= 0),
    to_version INTEGER NOT NULL CHECK(to_version > 0),
    asset_sha256 TEXT NOT NULL,
    migration_mode TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY(schema_name,to_version)
) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE UNIQUE INDEX idx_canon_contract_version
ON canon_contract(contract_id,contract_version,destination_task_uuid);

CREATE UNIQUE INDEX idx_canon_decision_once
ON canon_event(decision_key_sha256)
WHERE decision_key_sha256 IS NOT NULL;

CREATE INDEX idx_canon_packet_inbox
ON canon_packet(destination_task_uuid,current_state);

CREATE UNIQUE INDEX idx_canon_packet_replay
ON canon_packet(owner_project_id,local_role,idempotency_key);

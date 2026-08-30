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

CREATE TABLE goal_projection(
                projection_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                host_task_id TEXT NOT NULL,
                goal_state TEXT NOT NULL,
                active_plan_row INTEGER,
                plan_identity_sha256 TEXT,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;

CREATE VIRTUAL TABLE session_authority_fts USING fts5(
                record_id UNINDEXED,
                record_kind,
                session_id,
                host_task_id,
                payload_text,
                tokenize='unicode61'
            );

CREATE TABLE session_record(
                session_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                state TEXT NOT NULL,
                active_host_task_id TEXT,
                generation INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            ) STRICT;

CREATE TABLE state_travel_entry(
                entry_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                source_task_id TEXT NOT NULL,
                destination_task_id TEXT NOT NULL,
                route TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL UNIQUE,
                recorded_at TEXT NOT NULL
            ) STRICT;

CREATE TABLE task_attachment(
                attachment_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES session_record(session_id),
                host_task_id TEXT NOT NULL,
                destination_title TEXT,
                role TEXT NOT NULL,
                generation INTEGER NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(session_id, host_task_id, generation)
            ) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE INDEX state_travel_destination_idx
            ON state_travel_entry(session_id, destination_task_id, recorded_at);

CREATE INDEX task_attachment_session_idx
            ON task_attachment(session_id, generation, host_task_id);

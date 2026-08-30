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

CREATE TABLE receipt_content_cas(
                receipt_sha256 TEXT PRIMARY KEY,
                byte_count INTEGER NOT NULL CHECK(byte_count >= 0),
                compression TEXT NOT NULL,
                compressed_bytes BLOB NOT NULL,
                first_recorded_at TEXT NOT NULL
            ) STRICT;

CREATE VIRTUAL TABLE receipt_fts USING fts5(
                receipt_sha256 UNINDEXED,
                logical_path,
                receipt_kind,
                schema_id,
                payload_text,
                content='',
                contentless_delete=1,
                tokenize='unicode61'
            );

CREATE TABLE receipt_link(
                link_id INTEGER PRIMARY KEY,
                source_receipt_sha256 TEXT NOT NULL
                    REFERENCES receipt_record(receipt_sha256),
                relation TEXT NOT NULL,
                target_receipt_sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                UNIQUE(source_receipt_sha256, relation, target_receipt_sha256)
            ) STRICT;

CREATE TABLE receipt_migration_batch(
                batch_id TEXT PRIMARY KEY,
                source_root TEXT NOT NULL,
                source_file_count INTEGER NOT NULL,
                source_bytes INTEGER NOT NULL,
                ingested_count INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL,
                removed_count INTEGER NOT NULL,
                source_removal_authorized INTEGER NOT NULL
                    CHECK(source_removal_authorized IN (0,1)),
                receipt_sha256 TEXT NOT NULL UNIQUE,
                receipt_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;

CREATE TABLE receipt_record(
                sequence INTEGER PRIMARY KEY,
                receipt_sha256 TEXT NOT NULL UNIQUE
                    REFERENCES receipt_content_cas(receipt_sha256),
                logical_path TEXT NOT NULL UNIQUE,
                receipt_kind TEXT NOT NULL,
                schema_id TEXT,
                project_id TEXT,
                session_id TEXT,
                host_task_id TEXT,
                media_type TEXT NOT NULL,
                byte_count INTEGER NOT NULL CHECK(byte_count >= 0),
                payload_json TEXT,
                prior_receipt_sha256 TEXT,
                supersedes_receipt_sha256 TEXT,
                recorded_at TEXT NOT NULL,
                source_file_removed INTEGER NOT NULL DEFAULT 0
                    CHECK(source_file_removed IN (0,1))
            ) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE INDEX receipt_record_kind_idx
            ON receipt_record(receipt_kind, sequence);

CREATE INDEX receipt_record_project_session_idx
            ON receipt_record(project_id, session_id, sequence);

CREATE TRIGGER receipt_record_no_delete
            BEFORE DELETE ON receipt_record BEGIN
              SELECT RAISE(ABORT, 'receipt ledger is append-only');
            END;

CREATE TRIGGER receipt_record_no_update
            BEFORE UPDATE ON receipt_record BEGIN
              SELECT RAISE(ABORT, 'receipt ledger is append-only');
            END;

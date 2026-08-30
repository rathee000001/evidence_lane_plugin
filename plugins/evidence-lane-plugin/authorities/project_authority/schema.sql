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

CREATE VIRTUAL TABLE project_authority_fts USING fts5(
                record_id UNINDEXED,
                record_kind,
                project_id,
                authority_id,
                payload_text,
                tokenize='unicode61'
            );

CREATE TABLE project_authority_member(
                member_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                authority_kind TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                sqlite_path TEXT,
                mmd_path TEXT NOT NULL,
                dot_path TEXT NOT NULL,
                tools_path TEXT NOT NULL,
                authority_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(project_id, authority_kind, authority_id)
            ) STRICT;

CREATE TABLE project_authority_migration_receipt(
                sequence INTEGER PRIMARY KEY,
                migration_id TEXT NOT NULL UNIQUE,
                from_layout_sha256 TEXT NOT NULL,
                to_layout_sha256 TEXT NOT NULL,
                migrated_member_count INTEGER NOT NULL,
                removed_redundant_file_count INTEGER NOT NULL,
                source_bytes_preserved INTEGER NOT NULL
                    CHECK(source_bytes_preserved IN (0,1)),
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                recorded_at TEXT NOT NULL
            ) STRICT;

CREATE TABLE project_pointer_history(
                generation INTEGER PRIMARY KEY,
                project_id TEXT NOT NULL,
                accepted_pv TEXT,
                accepted_manifest_sha256 TEXT,
                prior_generation INTEGER,
                movement_kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;

CREATE TABLE project_registration(
                project_id TEXT PRIMARY KEY,
                project_root TEXT NOT NULL,
                project_root_identity_sha256 TEXT NOT NULL,
                registration_state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            ) STRICT;

CREATE TABLE workspace_binding(
                binding_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                host_task_id TEXT NOT NULL,
                workspace_root TEXT NOT NULL,
                repository_identity_sha256 TEXT,
                branch TEXT,
                commit_sha TEXT,
                tree_sha TEXT,
                dirty_identity_sha256 TEXT,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE INDEX project_authority_member_kind_idx
            ON project_authority_member(project_id, authority_kind, authority_id);

CREATE INDEX workspace_binding_project_task_idx
            ON workspace_binding(project_id, host_task_id, recorded_at);

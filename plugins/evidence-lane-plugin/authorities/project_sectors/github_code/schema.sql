-- Generated inspection/replay schema from the canonical lane builder.
-- Canonical implementation: src/evidence_lane_plugin/lane_engine.py

CREATE TABLE artifact_registry(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

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

CREATE TABLE chunk_content_cas(
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            text_content TEXT NOT NULL,
            first_seen_at TEXT NOT NULL
        ) STRICT;

CREATE TABLE chunk_history(
            history_id INTEGER PRIMARY KEY,
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            locator TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            chunk_sha256 TEXT NOT NULL REFERENCES chunk_content_cas(sha256),
            snapshot_ref TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            content_reused INTEGER NOT NULL CHECK(content_reused IN (0, 1)),
            UNIQUE(snapshot_ref, source_path, locator, ordinal, chunk_sha256)
        ) STRICT;

CREATE TABLE chunk_index(
            chunk_id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES source_registry(source_id) ON DELETE CASCADE,
            locator TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            text_content TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(source_id, locator, ordinal)
        ) STRICT;

CREATE TABLE code_call(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_chunk(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE VIRTUAL TABLE code_chunk_fts USING fts5(
            path,
            locator,
            text_content,
            chunk_id UNINDEXED,
            tokenize='unicode61'
        );

CREATE TABLE code_config_build_test_chunk(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_dependency(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_file_snapshot(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_good_snapshot(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_import(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_index_checkpoint(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_parser_diagnostic(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_parser_receipt(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_route(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_route_api_boundary(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_semantic_diff(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_snapshot_history(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_source_active_head(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_source_registry(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_symbol(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_synthetic_snapshot_file(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE code_workflow_edge(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE git_artifact_impact(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE git_blob_cas(
            blob_sha TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            is_binary INTEGER NOT NULL CHECK(is_binary IN (0, 1)),
            encoding TEXT,
            exact_bytes BLOB NOT NULL,
            first_commit_sha TEXT NOT NULL
        ) STRICT;

CREATE TABLE git_chunk_occurrence(
            commit_sha TEXT NOT NULL REFERENCES git_commit_registry(commit_sha),
            path TEXT NOT NULL,
            blob_sha TEXT NOT NULL REFERENCES git_blob_cas(blob_sha),
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            chunk_sha256 TEXT NOT NULL REFERENCES git_content_chunk_cas(chunk_sha256),
            PRIMARY KEY(commit_sha, path, ordinal)
        ) STRICT;

CREATE TABLE git_commit_parent(
            commit_sha TEXT NOT NULL REFERENCES git_commit_registry(commit_sha),
            parent_sha TEXT NOT NULL,
            parent_ordinal INTEGER NOT NULL,
            PRIMARY KEY(commit_sha, parent_ordinal)
        ) STRICT;

CREATE TABLE git_commit_registry(
            commit_sha TEXT PRIMARY KEY,
            ordinal INTEGER NOT NULL,
            tree_sha TEXT NOT NULL,
            authored_at TEXT NOT NULL,
            committed_at TEXT NOT NULL,
            author_name TEXT NOT NULL,
            author_email TEXT NOT NULL,
            committer_name TEXT NOT NULL,
            committer_email TEXT NOT NULL,
            message TEXT NOT NULL
        ) STRICT;

CREATE TABLE git_content_chunk_cas(
            chunk_sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            text_content TEXT NOT NULL
        ) STRICT;

CREATE TABLE git_dependency_impact(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE git_exact_line_change(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE git_file_change(
            commit_sha TEXT NOT NULL REFERENCES git_commit_registry(commit_sha),
            status TEXT NOT NULL,
            path TEXT NOT NULL,
            prior_path TEXT,
            blob_sha TEXT,
            PRIMARY KEY(commit_sha, status, path)
        ) STRICT;

CREATE VIRTUAL TABLE git_history_fts USING fts5(
            commit_sha UNINDEXED,
            path,
            message,
            text_content,
            tokenize='unicode61'
        );

CREATE TABLE git_patch_hunk(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE git_push_event(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE git_ref_registry(
            ref_name TEXT PRIMARY KEY,
            object_sha TEXT NOT NULL,
            peeled_sha TEXT,
            captured_signature TEXT NOT NULL
        ) STRICT;

CREATE TABLE git_route_impact(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE git_symbol_impact(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE git_test_impact(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE lane_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;

CREATE TABLE lane_pointer(
            pointer_kind TEXT PRIMARY KEY,
            pointer_value TEXT,
            generation INTEGER NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;

CREATE TABLE mutation_receipt(
            mutation_id INTEGER PRIMARY KEY,
            mutation_kind TEXT NOT NULL,
            source_path TEXT,
            prior_sha256 TEXT,
            current_sha256 TEXT,
            recorded_at TEXT NOT NULL
        ) STRICT;

CREATE TABLE parser_capability(
            capability TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            tool TEXT NOT NULL,
            detail TEXT NOT NULL
        ) STRICT;

CREATE TABLE refresh_receipt(
            receipt_id INTEGER PRIMARY KEY,
            build_mode TEXT NOT NULL,
            parent_pv TEXT,
            proposed_pv TEXT NOT NULL,
            unchanged_reuse INTEGER NOT NULL,
            changed_rebuild INTEGER NOT NULL,
            new_register INTEGER NOT NULL,
            removed_tombstone INTEGER NOT NULL,
            blocked_unsupported INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;

CREATE TABLE relation_edge(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE sector_head(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE sector_meta(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE snapshot_git_bridge(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE source_registry(
            source_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            extension TEXT NOT NULL,
            encoding TEXT,
            parser_state TEXT NOT NULL,
            exact_bytes BLOB NOT NULL,
            registered_at TEXT NOT NULL
        ) STRICT;

CREATE TABLE source_tombstone(
            tombstone_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            prior_sha256 TEXT NOT NULL,
            prior_size_bytes INTEGER NOT NULL,
            removed_at TEXT NOT NULL,
            parent_pv TEXT
        ) STRICT;

CREATE TABLE structured_fact(
            fact_id INTEGER PRIMARY KEY,
            source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            locator TEXT NOT NULL,
            payload_json TEXT NOT NULL
        ) STRICT;

CREATE TABLE tfidf_term(
            term TEXT PRIMARY KEY,
            document_frequency INTEGER NOT NULL,
            document_count INTEGER NOT NULL,
            idf REAL NOT NULL
        ) STRICT;

CREATE TABLE tfidf_vector(
            chunk_id INTEGER NOT NULL REFERENCES chunk_index(chunk_id) ON DELETE CASCADE,
            term TEXT NOT NULL REFERENCES tfidf_term(term) ON DELETE CASCADE,
            term_count INTEGER NOT NULL,
            token_count INTEGER NOT NULL,
            tf REAL NOT NULL,
            tfidf REAL NOT NULL,
            PRIMARY KEY(chunk_id, term)
        ) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE INDEX chunk_history_source_idx
        ON chunk_history(source_path, snapshot_ref, ordinal);

CREATE INDEX chunk_source_idx ON chunk_index(source_id, ordinal);

CREATE INDEX git_chunk_path_idx
        ON git_chunk_occurrence(path, commit_sha, ordinal);

CREATE INDEX git_file_change_path_idx
        ON git_file_change(path, commit_sha);

CREATE INDEX source_registry_path_idx ON source_registry(path);

CREATE INDEX structured_fact_kind_idx ON structured_fact(kind);

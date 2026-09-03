-- Generated inspection/replay schema from the canonical lane builder.
-- Canonical implementation: src/evidence_lane_plugin/lane_engine.py

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

CREATE TABLE chunk_content_cas(
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            compression TEXT NOT NULL,
            compressed_text BLOB NOT NULL,
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
            sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(source_id, locator, ordinal)
        ) STRICT;

CREATE TABLE docling_extraction(
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

CREATE TABLE ppt_chunk(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE ppt_file(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE VIRTUAL TABLE ppt_fts USING fts5(
            path UNINDEXED,
            locator UNINDEXED,
            text_content,
            chunk_id UNINDEXED,
            content='',
            contentless_delete=1,
            tokenize='unicode61'
        );

CREATE TABLE ppt_image_reference(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE ppt_notes(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE ppt_shape(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE ppt_slide(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE ppt_slide_relationship(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE ppt_structure_signature(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE ppt_table(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE ppt_text_block(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            ;

CREATE TABLE refresh_receipt(
            receipt_id INTEGER PRIMARY KEY,
            build_mode TEXT NOT NULL,
            parent_pv TEXT,
            proposed_pv TEXT NOT NULL,
            unchanged_reuse INTEGER NOT NULL,
            changed_rebuild INTEGER NOT NULL,
            new_register INTEGER NOT NULL,
            removed_purge INTEGER NOT NULL,
            blocked_unsupported INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;

CREATE TABLE source_content_cas(
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            compression TEXT NOT NULL,
            compressed_bytes BLOB NOT NULL,
            first_seen_at TEXT NOT NULL
        ) STRICT;

CREATE TABLE source_registry(
            source_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL REFERENCES source_content_cas(sha256),
            mime_type TEXT NOT NULL,
            extension TEXT NOT NULL,
            encoding TEXT,
            parser_state TEXT NOT NULL,
            registered_at TEXT NOT NULL
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

CREATE TABLE tool_execution_receipt(
            tool TEXT PRIMARY KEY REFERENCES tool_route_contract(tool)
                ON DELETE CASCADE,
            phases_json TEXT NOT NULL,
            eligibility_state TEXT NOT NULL,
            selection_state TEXT NOT NULL,
            condition_state TEXT NOT NULL,
            execution_state TEXT NOT NULL,
            evidence TEXT NOT NULL,
            network_call_performed INTEGER NOT NULL
                CHECK(network_call_performed IN (0, 1)),
            credential_value_read INTEGER NOT NULL
                CHECK(credential_value_read IN (0, 1)),
            recorded_at TEXT NOT NULL,
            row_receipt_sha256 TEXT NOT NULL
        ) STRICT;

CREATE TABLE tool_route_contract(
            tool TEXT PRIMARY KEY,
            role_class TEXT NOT NULL,
            requirement TEXT NOT NULL,
            action_classes_json TEXT NOT NULL,
            primary_json TEXT NOT NULL,
            fallback_json TEXT NOT NULL,
            implementation_owner TEXT NOT NULL,
            runs_only_when_selected INTEGER NOT NULL
                CHECK(runs_only_when_selected IN (0, 1)),
            runtime_state TEXT NOT NULL,
            runtime_evidence_sha256 TEXT NOT NULL,
            route_receipt_sha256 TEXT NOT NULL
        ) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

CREATE INDEX chunk_history_source_idx
        ON chunk_history(source_path, snapshot_ref, ordinal);

CREATE INDEX chunk_source_idx ON chunk_index(source_id, ordinal);

CREATE INDEX source_registry_path_idx ON source_registry(path);

CREATE INDEX structured_fact_kind_idx ON structured_fact(kind);

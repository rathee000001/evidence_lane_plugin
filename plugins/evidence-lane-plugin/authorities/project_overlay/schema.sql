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

CREATE TABLE chat_lineage_event(
            event_id TEXT PRIMARY KEY,
            lineage_index INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            model TEXT,
            submodel TEXT,
            token_metrics_json TEXT NOT NULL,
            visible_payload_json TEXT NOT NULL,
            visible_payload_sha256 TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            previous_event_sha256 TEXT,
            accepted_sector_truth INTEGER NOT NULL CHECK(accepted_sector_truth=0)
        ) STRICT;

CREATE TABLE chat_lineage_sector_fanout(
            event_id TEXT NOT NULL REFERENCES chat_lineage_event(event_id) ON DELETE CASCADE,
            sector_id TEXT NOT NULL REFERENCES project_sector_registry(sector_id),
            routing_reason TEXT NOT NULL,
            candidate_only INTEGER NOT NULL CHECK(candidate_only=1),
            PRIMARY KEY(event_id, sector_id)
        ) STRICT;

CREATE TABLE fusion_receipt(
            receipt_id INTEGER PRIMARY KEY CHECK(receipt_id=1),
            state TEXT NOT NULL CHECK(state='AWAITING_EXACT_APPROVE'),
            parent_accepted_pv TEXT,
            pointer_generation INTEGER NOT NULL,
            candidate_id TEXT NOT NULL,
            accepted_truth_written INTEGER NOT NULL CHECK(accepted_truth_written=0),
            fallback TEXT NOT NULL,
            rollback_semantics TEXT NOT NULL
        ) STRICT;

CREATE TABLE hil_transition_receipt(
            transition_id TEXT PRIMARY KEY REFERENCES pv_hil_transition(transition_id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(state='AWAITING_EXACT_APPROVE'),
            parent_pv TEXT,
            proposed_pv TEXT NOT NULL,
            pointer_generation INTEGER NOT NULL,
            accepted_truth_written INTEGER NOT NULL CHECK(accepted_truth_written=0),
            baseline_authority TEXT NOT NULL,
            rollback_semantics TEXT NOT NULL
        ) STRICT;

CREATE TABLE output_link(
            link_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES chat_lineage_event(event_id) ON DELETE CASCADE,
            link TEXT NOT NULL,
            link_sha256 TEXT NOT NULL,
            UNIQUE(event_id, link_sha256)
        ) STRICT;

CREATE VIRTUAL TABLE overlay_fts USING fts5(
            event_id UNINDEXED,
            event_type,
            actor_type,
            visible_text,
            tokenize='unicode61'
        );

CREATE TABLE overlay_history_baseline(
            baseline_id INTEGER PRIMARY KEY CHECK(baseline_id=1),
            baseline_pv TEXT,
            baseline_candidate_id TEXT,
            baseline_created_at TEXT,
            source_schema TEXT NOT NULL,
            source_database_sha256 TEXT,
            baseline_snapshot_sha256 TEXT NOT NULL,
            history_before_baseline TEXT NOT NULL,
            accepted_archive_opened INTEGER NOT NULL CHECK(accepted_archive_opened=0),
            accepted_archive_queried INTEGER NOT NULL CHECK(accepted_archive_queried=0)
        ) STRICT;

CREATE TABLE overlay_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;

CREATE TABLE project_sector_registry(
            sector_id TEXT PRIMARY KEY,
            display_label TEXT NOT NULL,
            ordinal INTEGER NOT NULL UNIQUE,
            mutation_policy TEXT NOT NULL
        ) STRICT;

CREATE TABLE pv_hil_transition(
            transition_id TEXT PRIMARY KEY,
            sequence INTEGER NOT NULL UNIQUE CHECK(sequence>0),
            parent_pv TEXT,
            proposed_pv TEXT NOT NULL,
            pointer_generation INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            truth_state TEXT NOT NULL CHECK(truth_state='HIL_PROPOSAL_ONLY'),
            prior_transition_sha256 TEXT,
            baseline_snapshot_sha256 TEXT NOT NULL,
            transition_payload_json TEXT NOT NULL,
            transition_sha256 TEXT NOT NULL UNIQUE,
            accepted_archive_opened INTEGER NOT NULL CHECK(accepted_archive_opened=0),
            accepted_archive_queried INTEGER NOT NULL CHECK(accepted_archive_queried=0)
        ) STRICT;

CREATE TABLE sector_candidate_snapshot(
            sector_id TEXT PRIMARY KEY REFERENCES project_sector_registry(sector_id),
            lane_database_sha256 TEXT,
            source_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL,
            truth_state TEXT NOT NULL CHECK(truth_state='CANDIDATE_ONLY'),
            candidate_id TEXT NOT NULL,
            proposed_pv TEXT NOT NULL
        ) STRICT;

CREATE TABLE sector_hil_baseline_snapshot(
            sector_id TEXT PRIMARY KEY,
            lane_database_sha256 TEXT,
            source_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL
        ) STRICT;

CREATE TABLE sector_hil_delta(
            transition_id TEXT NOT NULL REFERENCES pv_hil_transition(transition_id) ON DELETE CASCADE,
            sector_id TEXT NOT NULL,
            change_kind TEXT NOT NULL CHECK(change_kind IN ('ADDED','MODIFIED','REMOVED','UNCHANGED')),
            before_json TEXT,
            after_json TEXT,
            PRIMARY KEY(transition_id,sector_id)
        ) STRICT;

CREATE TABLE sector_hil_snapshot(
            transition_id TEXT NOT NULL REFERENCES pv_hil_transition(transition_id) ON DELETE CASCADE,
            sector_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            lane_database_sha256 TEXT,
            source_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL,
            PRIMARY KEY(transition_id,sector_id),
            UNIQUE(transition_id,ordinal)
        ) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

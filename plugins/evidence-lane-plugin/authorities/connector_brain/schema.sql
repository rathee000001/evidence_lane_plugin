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

CREATE TABLE plugin_event(
                event_id TEXT PRIMARY KEY,
                plugin_id TEXT NOT NULL REFERENCES plugin_registration(plugin_id),
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                details_json TEXT NOT NULL,
                prior_event_sha256 TEXT,
                event_sha256 TEXT NOT NULL
            ) STRICT;

CREATE VIRTUAL TABLE plugin_fts USING fts5(
                plugin_id UNINDEXED,
                name,
                description,
                capabilities,
                tokenize='unicode61'
            );

CREATE TABLE plugin_registration(
                plugin_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                plugin_kind TEXT NOT NULL CHECK(plugin_kind IN ('connector','toolchain')),
                description TEXT NOT NULL,
                config_env_keys_json TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                allowed_lanes_json TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT '',
                allowed_actions_json TEXT NOT NULL DEFAULT '[]',
                write_scope_json TEXT NOT NULL DEFAULT '[]',
                expires_at TEXT NOT NULL DEFAULT 'NO_EXPIRY',
                role TEXT NOT NULL DEFAULT '',
                role_schema_json TEXT NOT NULL DEFAULT '{}',
                host_profiles_json TEXT NOT NULL DEFAULT '["CODEX"]',
                backend_runtime TEXT NOT NULL DEFAULT 'python',
                registered_at TEXT NOT NULL,
                dropped_at TEXT,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','DROPPED')),
                registration_sha256 TEXT NOT NULL
            ) STRICT;

CREATE TABLE role_schema_field(
                plugin_id TEXT NOT NULL REFERENCES plugin_registration(plugin_id),
                field_order INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                field_type TEXT NOT NULL,
                PRIMARY KEY(plugin_id, field_name),
                UNIQUE(plugin_id, field_order)
            ) STRICT;

CREATE TABLE route_decision(
                route_id TEXT PRIMARY KEY,
                requested_capability TEXT NOT NULL,
                canonical_lane_id TEXT,
                requested_plugin_id TEXT,
                selected_plugin_id TEXT REFERENCES plugin_registration(plugin_id),
                decision TEXT NOT NULL,
                decision_reason TEXT NOT NULL DEFAULT '',
                candidate_plugin_ids_json TEXT NOT NULL DEFAULT '[]',
                guard_trace_json TEXT NOT NULL DEFAULT '[]',
                host_profile TEXT NOT NULL DEFAULT 'CODEX',
                role_schema_sha256 TEXT,
                recorded_at TEXT NOT NULL
            ) STRICT;

CREATE TABLE universe_cross_project_edge(
                edge_id TEXT PRIMARY KEY,
                source_mini_brain_id TEXT NOT NULL
                    REFERENCES universe_mini_brain_ref(mini_brain_id),
                target_mini_brain_id TEXT NOT NULL
                    REFERENCES universe_mini_brain_ref(mini_brain_id),
                relation TEXT NOT NULL,
                explicit_grant_sha256 TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                edge_sha256 TEXT NOT NULL UNIQUE,
                recorded_at TEXT NOT NULL,
                CHECK(source_mini_brain_id <> target_mini_brain_id)
            ) STRICT;

CREATE VIRTUAL TABLE universe_federation_fts USING fts5(
                mini_brain_id UNINDEXED,
                project_id UNINDEXED,
                lane_id,
                payload_text,
                tokenize='unicode61'
            );

CREATE TABLE universe_mini_brain_ref(
                mini_brain_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES universe_project_ref(project_id),
                lane_id TEXT NOT NULL,
                database_sha256 TEXT NOT NULL,
                mmd_sha256 TEXT NOT NULL,
                dot_sha256 TEXT NOT NULL,
                tools_sha256 TEXT NOT NULL,
                content_identity_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(project_id,lane_id,content_identity_sha256)
            ) STRICT;

CREATE TABLE universe_project_lane_head(
                project_id TEXT NOT NULL REFERENCES universe_project_ref(project_id),
                lane_id TEXT NOT NULL,
                mini_brain_id TEXT NOT NULL
                    REFERENCES universe_mini_brain_ref(mini_brain_id),
                content_identity_sha256 TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(project_id,lane_id)
            ) STRICT;

CREATE TABLE universe_project_ref(
                project_id TEXT PRIMARY KEY,
                project_root_identity_sha256 TEXT NOT NULL,
                universe_head_sha256 TEXT NOT NULL,
                pointer_generation INTEGER NOT NULL,
                active INTEGER NOT NULL CHECK(active IN (0,1)),
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;

CREATE INDEX authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);

CREATE INDEX authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);

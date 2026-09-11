-- Projection: runtime applies the real ordered migrations under its project writer.

-- sources v1, digest eb6c022b95032fd4878e9143c5c4a654e7a4ff764d5a8915ebdd2852548ba23a
CREATE TABLE IF NOT EXISTS registry_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS intake_batch(
                batch_id TEXT PRIMARY KEY,
                batch_sha256 TEXT NOT NULL UNIQUE,
                source_count INTEGER NOT NULL,
                directory_count INTEGER NOT NULL,
                file_count INTEGER NOT NULL,
                zip_count INTEGER NOT NULL,
                exact_extracted_zip_relations INTEGER NOT NULL,
                unique_zip_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_object(
                object_id TEXT PRIMARY KEY,
                source_pointer TEXT NOT NULL,
                resolved_pointer TEXT NOT NULL,
                kind TEXT NOT NULL,
                lane_id TEXT NOT NULL,
                identity_sha256 TEXT NOT NULL,
                byte_sha256 TEXT,
                size_bytes INTEGER,
                member_count INTEGER NOT NULL,
                included_member_count INTEGER NOT NULL,
                excluded_member_count INTEGER NOT NULL,
                member_path_size_sha256 TEXT,
                content_merkle_sha256 TEXT
            );
CREATE TABLE IF NOT EXISTS source_occurrence(
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                ordinal INTEGER NOT NULL,
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                supplied_pointer TEXT NOT NULL,
                lane_id TEXT NOT NULL,
                PRIMARY KEY(batch_id, ordinal)
            );
CREATE TABLE IF NOT EXISTS source_member(
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                member_path TEXT NOT NULL,
                member_kind TEXT NOT NULL,
                size_bytes INTEGER,
                sha256 TEXT,
                policy_state TEXT NOT NULL,
                policy_reason TEXT NOT NULL,
                PRIMARY KEY(object_id, member_path)
            );
CREATE TABLE IF NOT EXISTS source_relation(
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                left_object_id TEXT NOT NULL REFERENCES source_object(object_id),
                relation_type TEXT NOT NULL,
                right_object_id TEXT NOT NULL REFERENCES source_object(object_id),
                receipt_sha256 TEXT NOT NULL,
                PRIMARY KEY(batch_id, left_object_id, relation_type, right_object_id)
            );
CREATE TABLE IF NOT EXISTS source_policy_receipt(
                object_id TEXT PRIMARY KEY REFERENCES source_object(object_id),
                included_member_count INTEGER NOT NULL,
                excluded_member_count INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_exclusion_summary(
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                policy_reason TEXT NOT NULL,
                excluded_entry_count INTEGER NOT NULL,
                excluded_bytes INTEGER,
                descendant_members_enumerated INTEGER NOT NULL CHECK(descendant_members_enumerated IN (0, 1)),
                member_paths_stored INTEGER NOT NULL CHECK(member_paths_stored = 0),
                capture_mode TEXT NOT NULL,
                PRIMARY KEY(object_id, policy_reason)
            );
CREATE TABLE IF NOT EXISTS source_archive_receipt(
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                archive_object_id TEXT NOT NULL REFERENCES source_object(object_id),
                intake_status TEXT NOT NULL,
                skip_status TEXT NOT NULL,
                counterpart_object_id TEXT REFERENCES source_object(object_id),
                counterpart_scope TEXT,
                matched_prefix TEXT,
                unsafe_member_count INTEGER NOT NULL,
                policy_excluded_member_count INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(batch_id, archive_object_id)
            );
CREATE TABLE IF NOT EXISTS source_provenance(
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                claim_key TEXT NOT NULL,
                claim_json TEXT NOT NULL,
                authority TEXT NOT NULL,
                PRIMARY KEY(batch_id, object_id, claim_key)
            );
CREATE TABLE IF NOT EXISTS source_assertion_set(
                assertion_set_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                crosswalk_sha256 TEXT NOT NULL,
                source_count INTEGER NOT NULL,
                claim_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(batch_id, crosswalk_sha256)
            );
CREATE TABLE IF NOT EXISTS source_sqlite_asset(
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                member_path TEXT NOT NULL,
                inspection_state TEXT NOT NULL,
                schema_sha256 TEXT,
                PRIMARY KEY(object_id, member_path)
            );
CREATE TABLE IF NOT EXISTS source_sqlite_schema_object(
                object_id TEXT NOT NULL,
                member_path TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_name TEXT NOT NULL,
                sql_sha256 TEXT,
                PRIMARY KEY(object_id, member_path, object_type, object_name)
            );
CREATE TABLE IF NOT EXISTS source_sqlite_table_stat(
                object_id TEXT NOT NULL,
                member_path TEXT NOT NULL,
                table_name TEXT NOT NULL,
                row_count INTEGER,
                count_state TEXT NOT NULL,
                PRIMARY KEY(object_id, member_path, table_name)
            );
CREATE TABLE IF NOT EXISTS source_sqlite_receipt(
                canonical_asset_id TEXT PRIMARY KEY,
                byte_sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                inspection_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                integrity_json TEXT NOT NULL,
                foreign_key_error_count INTEGER NOT NULL,
                user_version INTEGER,
                page_count INTEGER,
                page_size INTEGER,
                schema_sha256 TEXT,
                schema_object_count INTEGER NOT NULL,
                table_count INTEGER NOT NULL,
                fts_table_count INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_sqlite_foreign_key(
                canonical_asset_id TEXT NOT NULL REFERENCES source_sqlite_receipt(canonical_asset_id),
                from_table TEXT NOT NULL,
                foreign_key_id INTEGER NOT NULL,
                sequence_id INTEGER NOT NULL,
                to_table TEXT,
                from_column TEXT,
                to_column TEXT,
                on_update TEXT,
                on_delete TEXT,
                match_rule TEXT,
                PRIMARY KEY(canonical_asset_id, from_table, foreign_key_id, sequence_id)
            );
CREATE TABLE IF NOT EXISTS source_custom_schema(
                schema_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                schema_sha256 TEXT NOT NULL UNIQUE,
                target_lane_id TEXT NOT NULL,
                compiler_version TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                compiled_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(schema_id, schema_version)
            );
CREATE TABLE IF NOT EXISTS source_custom_schema_mapping(
                mapping_id TEXT PRIMARY KEY,
                schema_sha256 TEXT NOT NULL REFERENCES source_custom_schema(schema_sha256),
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                occurrence_ordinal INTEGER NOT NULL,
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                member_path TEXT NOT NULL,
                selector_id TEXT NOT NULL,
                target_lane_id TEXT NOT NULL,
                mapped_json TEXT NOT NULL,
                mapping_sha256 TEXT NOT NULL,
                mapping_state TEXT NOT NULL,
                UNIQUE(schema_sha256, batch_id, occurrence_ordinal, member_path, selector_id)
            );
CREATE TABLE IF NOT EXISTS source_custom_schema_receipt(
                receipt_id TEXT PRIMARY KEY,
                schema_sha256 TEXT NOT NULL REFERENCES source_custom_schema(schema_sha256),
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                matched_occurrence_count INTEGER NOT NULL,
                mapping_count INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                UNIQUE(schema_sha256, batch_id)
            );
CREATE TABLE IF NOT EXISTS source_identity_entity(
                entity_id TEXT PRIMARY KEY,
                entity_kind TEXT NOT NULL,
                label TEXT NOT NULL,
                version_label TEXT NOT NULL,
                authority TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                entity_json TEXT NOT NULL,
                entity_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_identity_assertion(
                assertion_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                identity_axis TEXT NOT NULL,
                value_json TEXT NOT NULL,
                authority TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                claim_state TEXT NOT NULL,
                assertion_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_identity_relation(
                relation_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                authority TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                relation_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_identity_receipt(
                receipt_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                matrix_sha256 TEXT NOT NULL UNIQUE,
                source_profile_count INTEGER NOT NULL,
                entity_count INTEGER NOT NULL,
                assertion_count INTEGER NOT NULL,
                relation_count INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_graph_snapshot(
                graph_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                extractor_version TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                selection_sha256 TEXT NOT NULL,
                source_count INTEGER NOT NULL,
                file_count INTEGER NOT NULL,
                parsed_file_count INTEGER NOT NULL,
                node_count INTEGER NOT NULL,
                edge_count INTEGER NOT NULL,
                coverage_sha256 TEXT NOT NULL,
                graph_root_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_graph_node(
                graph_id TEXT NOT NULL REFERENCES source_graph_snapshot(graph_id),
                node_id TEXT NOT NULL,
                source_scope_id TEXT NOT NULL,
                object_id TEXT NOT NULL,
                member_path TEXT NOT NULL,
                node_kind TEXT NOT NULL,
                language TEXT NOT NULL,
                label TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                extraction_authority TEXT NOT NULL,
                parser_id TEXT NOT NULL,
                identity_json TEXT NOT NULL,
                identity_sha256 TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                node_sha256 TEXT NOT NULL,
                PRIMARY KEY(graph_id, node_id)
            );
CREATE TABLE IF NOT EXISTS source_graph_edge(
                graph_id TEXT NOT NULL REFERENCES source_graph_snapshot(graph_id),
                edge_id TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence TEXT NOT NULL,
                source_scope_id TEXT NOT NULL,
                object_id TEXT NOT NULL,
                member_path TEXT NOT NULL,
                line_number INTEGER,
                parser_id TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                edge_sha256 TEXT NOT NULL,
                PRIMARY KEY(graph_id, edge_id),
                FOREIGN KEY(graph_id, source_node_id)
                    REFERENCES source_graph_node(graph_id, node_id),
                FOREIGN KEY(graph_id, target_node_id)
                    REFERENCES source_graph_node(graph_id, node_id)
            );
CREATE TABLE IF NOT EXISTS source_graph_file_coverage(
                graph_id TEXT NOT NULL REFERENCES source_graph_snapshot(graph_id),
                object_id TEXT NOT NULL,
                member_path TEXT NOT NULL,
                language TEXT NOT NULL,
                file_state TEXT NOT NULL,
                parser_id TEXT NOT NULL,
                size_bytes INTEGER,
                byte_sha256 TEXT,
                symbol_count INTEGER NOT NULL,
                import_count INTEGER NOT NULL,
                call_count INTEGER NOT NULL,
                node_count INTEGER NOT NULL,
                edge_count INTEGER NOT NULL,
                reason TEXT NOT NULL,
                PRIMARY KEY(graph_id, object_id, member_path)
            );
CREATE TABLE IF NOT EXISTS source_graph_diff(
                diff_id TEXT PRIMARY KEY,
                from_graph_id TEXT NOT NULL REFERENCES source_graph_snapshot(graph_id),
                to_graph_id TEXT NOT NULL REFERENCES source_graph_snapshot(graph_id),
                added_node_count INTEGER NOT NULL,
                removed_node_count INTEGER NOT NULL,
                changed_node_count INTEGER NOT NULL,
                added_edge_count INTEGER NOT NULL,
                removed_edge_count INTEGER NOT NULL,
                changed_edge_count INTEGER NOT NULL,
                projection_json TEXT NOT NULL,
                projection_sha256 TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                UNIQUE(from_graph_id, to_graph_id)
            );
CREATE TABLE IF NOT EXISTS source_graph_impact(
                impact_id TEXT PRIMARY KEY,
                graph_id TEXT NOT NULL REFERENCES source_graph_snapshot(graph_id),
                seed_nodes_json TEXT NOT NULL,
                relations_json TEXT NOT NULL,
                direction TEXT NOT NULL,
                max_depth INTEGER NOT NULL,
                max_nodes INTEGER NOT NULL,
                projection_json TEXT NOT NULL,
                projection_sha256 TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS source_git_snapshot(
                snapshot_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                occurrence_ordinal INTEGER NOT NULL,
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                repository_identity_sha256 TEXT NOT NULL,
                head_commit_sha TEXT NOT NULL,
                head_tree_sha TEXT NOT NULL,
                branch TEXT NOT NULL,
                worktree_clean INTEGER NOT NULL CHECK(worktree_clean IN (0, 1)),
                worktree_status_sha256 TEXT NOT NULL,
                history_signature_sha256 TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                ref_count INTEGER NOT NULL,
                commit_count INTEGER NOT NULL,
                parent_edge_count INTEGER NOT NULL,
                object_count INTEGER NOT NULL,
                tree_entry_count INTEGER NOT NULL,
                file_change_count INTEGER NOT NULL,
                rename_count INTEGER NOT NULL,
                hunk_count INTEGER NOT NULL,
                changed_line_count INTEGER NOT NULL,
                refs_root_sha256 TEXT NOT NULL,
                commits_root_sha256 TEXT NOT NULL,
                objects_root_sha256 TEXT NOT NULL,
                trees_root_sha256 TEXT NOT NULL,
                changes_root_sha256 TEXT NOT NULL,
                hunks_root_sha256 TEXT NOT NULL,
                lines_root_sha256 TEXT NOT NULL,
                history_root_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                UNIQUE(
                    batch_id,
                    occurrence_ordinal,
                    history_signature_sha256,
                    worktree_status_sha256,
                    repository_identity_sha256
                )
            );
CREATE TABLE IF NOT EXISTS source_git_ref(
                snapshot_id TEXT NOT NULL REFERENCES source_git_snapshot(snapshot_id),
                ref_name TEXT NOT NULL,
                object_sha TEXT NOT NULL,
                object_type TEXT NOT NULL,
                peeled_sha TEXT,
                peeled_type TEXT,
                ref_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, ref_name)
            );
CREATE TABLE IF NOT EXISTS source_git_commit(
                snapshot_id TEXT NOT NULL REFERENCES source_git_snapshot(snapshot_id),
                commit_sha TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                tree_sha TEXT NOT NULL,
                authored_at TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                author_sha256 TEXT NOT NULL,
                committer_sha256 TEXT NOT NULL,
                message_redacted TEXT NOT NULL,
                message_sha256 TEXT NOT NULL,
                object_content_sha256 TEXT NOT NULL,
                parent_count INTEGER NOT NULL,
                commit_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, commit_sha),
                UNIQUE(snapshot_id, ordinal)
            );
CREATE TABLE IF NOT EXISTS source_git_parent(
                snapshot_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                parent_ordinal INTEGER NOT NULL,
                parent_sha TEXT NOT NULL,
                parent_edge_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, commit_sha, parent_ordinal),
                FOREIGN KEY(snapshot_id, commit_sha)
                    REFERENCES source_git_commit(snapshot_id, commit_sha)
            );
CREATE TABLE IF NOT EXISTS source_git_object(
                snapshot_id TEXT NOT NULL REFERENCES source_git_snapshot(snapshot_id),
                object_sha TEXT NOT NULL,
                object_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                object_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, object_sha)
            );
CREATE TABLE IF NOT EXISTS source_git_object_path(
                snapshot_id TEXT NOT NULL,
                object_sha TEXT NOT NULL,
                path_ordinal INTEGER NOT NULL,
                observed_path TEXT NOT NULL,
                path_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, object_sha, path_ordinal),
                FOREIGN KEY(snapshot_id, object_sha)
                    REFERENCES source_git_object(snapshot_id, object_sha)
            );
CREATE TABLE IF NOT EXISTS source_git_tree_entry(
                snapshot_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                entry_ordinal INTEGER NOT NULL,
                member_path TEXT NOT NULL,
                mode TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_sha TEXT NOT NULL,
                entry_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, commit_sha, entry_ordinal),
                FOREIGN KEY(snapshot_id, commit_sha)
                    REFERENCES source_git_commit(snapshot_id, commit_sha)
            );
CREATE TABLE IF NOT EXISTS source_git_file_change(
                snapshot_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                parent_ordinal INTEGER NOT NULL,
                comparison_base_sha TEXT NOT NULL,
                change_ordinal INTEGER NOT NULL,
                status_code TEXT NOT NULL,
                similarity_score INTEGER,
                prior_path TEXT,
                member_path TEXT NOT NULL,
                old_mode TEXT NOT NULL,
                new_mode TEXT NOT NULL,
                old_object_sha TEXT NOT NULL,
                new_object_sha TEXT NOT NULL,
                additions INTEGER,
                deletions INTEGER,
                binary INTEGER NOT NULL CHECK(binary IN (0, 1)),
                change_sha256 TEXT NOT NULL,
                PRIMARY KEY(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal
                ),
                FOREIGN KEY(snapshot_id, commit_sha)
                    REFERENCES source_git_commit(snapshot_id, commit_sha)
            );
CREATE TABLE IF NOT EXISTS source_git_rename(
                snapshot_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                parent_ordinal INTEGER NOT NULL,
                change_ordinal INTEGER NOT NULL,
                prior_path TEXT NOT NULL,
                member_path TEXT NOT NULL,
                similarity_score INTEGER NOT NULL,
                rename_sha256 TEXT NOT NULL,
                PRIMARY KEY(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal
                ),
                FOREIGN KEY(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal
                ) REFERENCES source_git_file_change(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal
                )
            );
CREATE TABLE IF NOT EXISTS source_git_hunk(
                snapshot_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                parent_ordinal INTEGER NOT NULL,
                change_ordinal INTEGER NOT NULL,
                hunk_ordinal INTEGER NOT NULL,
                old_start INTEGER NOT NULL,
                old_count INTEGER NOT NULL,
                new_start INTEGER NOT NULL,
                new_count INTEGER NOT NULL,
                header_sha256 TEXT NOT NULL,
                hunk_sha256 TEXT NOT NULL,
                PRIMARY KEY(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal,
                    hunk_ordinal
                ),
                FOREIGN KEY(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal
                ) REFERENCES source_git_file_change(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal
                )
            );
CREATE TABLE IF NOT EXISTS source_git_changed_line(
                snapshot_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                parent_ordinal INTEGER NOT NULL,
                change_ordinal INTEGER NOT NULL,
                hunk_ordinal INTEGER NOT NULL,
                line_ordinal INTEGER NOT NULL,
                line_kind TEXT NOT NULL,
                old_line_number INTEGER,
                new_line_number INTEGER,
                content_size_bytes INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                no_newline INTEGER NOT NULL CHECK(no_newline IN (0, 1)),
                line_sha256 TEXT NOT NULL,
                PRIMARY KEY(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal,
                    hunk_ordinal,
                    line_ordinal
                ),
                FOREIGN KEY(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal,
                    hunk_ordinal
                ) REFERENCES source_git_hunk(
                    snapshot_id,
                    commit_sha,
                    parent_ordinal,
                    change_ordinal,
                    hunk_ordinal
                )
            );
CREATE TABLE IF NOT EXISTS source_git_impact(
                impact_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES source_git_snapshot(snapshot_id),
                graph_id TEXT NOT NULL REFERENCES source_graph_snapshot(graph_id),
                commit_sha TEXT NOT NULL,
                parent_ordinal INTEGER NOT NULL,
                mapped_path_count INTEGER NOT NULL,
                unmapped_path_count INTEGER NOT NULL,
                seed_nodes_json TEXT NOT NULL,
                projection_json TEXT NOT NULL,
                projection_sha256 TEXT NOT NULL,
                graph_impact_id TEXT,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS registry_event(
                event_id TEXT PRIMARY KEY,
                batch_id TEXT,
                event_type TEXT NOT NULL,
                event_json TEXT NOT NULL,
                event_sha256 TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
ALTER TABLE source_object ADD COLUMN exclusion_summary_sha256 TEXT;
ALTER TABLE source_sqlite_asset ADD COLUMN byte_sha256 TEXT;
ALTER TABLE source_sqlite_asset ADD COLUMN size_bytes INTEGER;
ALTER TABLE source_sqlite_asset ADD COLUMN canonical_asset_id TEXT;
ALTER TABLE source_sqlite_asset ADD COLUMN inspection_receipt_sha256 TEXT;
CREATE INDEX IF NOT EXISTS source_git_change_path_idx ON source_git_file_change(snapshot_id, member_path, commit_sha);
CREATE INDEX IF NOT EXISTS source_git_prior_path_idx ON source_git_file_change(snapshot_id, prior_path, commit_sha);
CREATE INDEX IF NOT EXISTS source_git_tree_path_idx ON source_git_tree_entry(snapshot_id, member_path, commit_sha);
CREATE VIRTUAL TABLE IF NOT EXISTS source_authority_fts USING fts5(object_id UNINDEXED, source_pointer, member_path);
INSERT INTO registry_meta(key,value) VALUES('schema','evidence-lane.source-authority-registry.v1');
-- sources v2, digest c52b0c7790268be4c3846be088014a4377c9de49fdd6bfbf9f28278681a3cdc1
CREATE TABLE source_sqlite_asset_previous AS SELECT * FROM source_sqlite_asset;
DROP TABLE source_sqlite_asset;
CREATE TABLE source_sqlite_asset(
            object_id TEXT NOT NULL REFERENCES source_object(object_id), member_path TEXT NOT NULL,
            inspection_state TEXT NOT NULL, schema_sha256 TEXT, byte_sha256 TEXT, size_bytes INTEGER,
            canonical_asset_id TEXT, inspection_receipt_sha256 TEXT, inspection_id TEXT NOT NULL,
            PRIMARY KEY(object_id, member_path, inspection_id));
INSERT INTO source_sqlite_asset SELECT *, COALESCE(canonical_asset_id, 'legacy')
            FROM source_sqlite_asset_previous;
CREATE TABLE source_sqlite_schema_object_previous AS SELECT * FROM source_sqlite_schema_object;
DROP TABLE source_sqlite_schema_object;
CREATE TABLE source_sqlite_schema_object(
            object_id TEXT NOT NULL, member_path TEXT NOT NULL, object_type TEXT NOT NULL,
            object_name TEXT NOT NULL, sql_sha256 TEXT, inspection_id TEXT NOT NULL,
            PRIMARY KEY(object_id, member_path, inspection_id, object_type, object_name));
INSERT INTO source_sqlite_schema_object SELECT s.*, COALESCE(a.canonical_asset_id, 'legacy')
            FROM source_sqlite_schema_object_previous s LEFT JOIN source_sqlite_asset_previous a
            ON a.object_id=s.object_id AND a.member_path=s.member_path;
CREATE TABLE source_sqlite_table_stat_previous AS SELECT * FROM source_sqlite_table_stat;
DROP TABLE source_sqlite_table_stat;
CREATE TABLE source_sqlite_table_stat(
            object_id TEXT NOT NULL, member_path TEXT NOT NULL, table_name TEXT NOT NULL,
            row_count INTEGER, count_state TEXT NOT NULL, inspection_id TEXT NOT NULL,
            PRIMARY KEY(object_id, member_path, inspection_id, table_name));
INSERT INTO source_sqlite_table_stat SELECT s.*, COALESCE(a.canonical_asset_id, 'legacy')
            FROM source_sqlite_table_stat_previous s LEFT JOIN source_sqlite_asset_previous a
            ON a.object_id=s.object_id AND a.member_path=s.member_path;
DROP TABLE source_sqlite_schema_object_previous;
DROP TABLE source_sqlite_table_stat_previous;
DROP TABLE source_sqlite_asset_previous;
-- restoration v1, digest 7d978515d683d19a0aa22335bfd68dc9571532c9051b8a4ee3e1060210593728
CREATE TABLE restoration_requests (request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
       input_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       state TEXT NOT NULL CHECK(state IN ('pending','published','abandoned')),
       result_json TEXT CHECK(json_valid(result_json)));
CREATE TABLE restoration_history (generation INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)));
CREATE TABLE restoration_control (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       generation INTEGER NOT NULL REFERENCES restoration_history(generation),
       restore_digest TEXT NOT NULL, required_after_revision INTEGER NOT NULL,
       cleared_by_revision INTEGER);
CREATE UNIQUE INDEX restoration_one_pending ON restoration_requests(state) WHERE state='pending';
-- restoration v2, digest b2d0958686ea1c695c10103de72c7837a201fb404289fa58c1c78e8b6f4aeaaa
CREATE TABLE restoration_reindex (generation INTEGER PRIMARY KEY REFERENCES restoration_history(generation),
       digest TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)));
-- gitbranch v1, digest 57b55153da9c0963abb4ceb1ca8a4fe430581d1fe0d8de4f1c92b1869cb6d6c9
CREATE TABLE gitbranch_history (generation INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)));
CREATE TABLE gitbranch_current (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       generation INTEGER NOT NULL REFERENCES gitbranch_history(generation), digest TEXT NOT NULL);
CREATE TABLE gitbranch_sync (sync_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)));
-- gitbranch v2, digest dbe99550c78e5a7fcfe1e8bec03b11ba025cd2ff8988588d2291def7f8bab5fb
CREATE TABLE gitbranch_enrollment (enrollment_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
           body_json TEXT NOT NULL CHECK(json_valid(body_json)));
-- sourceroutes v1, digest 91ee7c98f3b9c368c07cf0de3caea1edc5c0d35b091355a35aeb231dcf17be0f
CREATE TABLE sourceroutes_receipts (
       route_id TEXT PRIMARY KEY REFERENCES objects(digest),
       batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
       parent_route_id TEXT REFERENCES sourceroutes_receipts(route_id),
       created_at TEXT NOT NULL);
CREATE TABLE sourceroutes_requests (
       request_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, action TEXT NOT NULL,
       request_digest TEXT NOT NULL, route_id TEXT NOT NULL REFERENCES sourceroutes_receipts(route_id),
       result_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL);
-- sourceroutes v2, digest cbc85aafcb9553c1ba5995ec4cbb413d7e8b491ef318102659a1ec748cb7c921
CREATE TABLE sourceroutes_preparations (
       request_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, request_digest TEXT NOT NULL,
       preparation_id TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL);
-- sourcematerialization v1, digest d7fb49b1a5916318b1459b1eb08777491a2946734f4e7d3dce1be75c35ebb614
CREATE TABLE sourcematerialization_runs (
       run_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, request_digest TEXT NOT NULL,
       preparation_id TEXT NOT NULL REFERENCES objects(digest), plan_revision INTEGER NOT NULL,
       engine_instance TEXT NOT NULL, intent_object TEXT NOT NULL REFERENCES objects(digest),
       state TEXT NOT NULL CHECK(state IN ('active','completed','blocked')),
       outcome_object TEXT REFERENCES objects(digest), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX sourcematerialization_one_active ON sourcematerialization_runs(state) WHERE state='active';
-- customlanes v1, digest a21f91b1baa7d04bfa079afbe492f80d6f18184290dd3eb9341b98b07b3c7d3c
CREATE TABLE customlanes_contracts(contract_sha256 TEXT PRIMARY KEY,lane_id TEXT NOT NULL,
        previous_contract TEXT REFERENCES customlanes_contracts(contract_sha256),
        contract_json TEXT NOT NULL CHECK(json_valid(contract_json)),created_at TEXT NOT NULL) STRICT;
CREATE TABLE customlanes_current(lane_id TEXT PRIMARY KEY,
        contract_sha256 TEXT NOT NULL REFERENCES customlanes_contracts(contract_sha256)) STRICT;

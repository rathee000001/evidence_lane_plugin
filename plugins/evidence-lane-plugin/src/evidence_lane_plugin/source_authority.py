"""Deterministic, read-only source authority registry.

The registry records what Source Intake inspected without copying source payloads into
the project store.  File content is hashed by streaming reads, directory members are
hashed independently, excluded secret/runtime members remain counted without content
capture, and every ordered occurrence is preserved.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file
from .timeutil import utc_now

REGISTRY_SCHEMA = "evidence-lane.source-authority-registry.v1"
BATCH_SCHEMA = "evidence-lane.source-authority-batch.v1"

_RUNTIME_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)
_SECRET_BASENAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "service-account.json",
        "service_account.json",
    }
)
_SECRET_SUFFIXES = (".key", ".p12", ".pfx", ".pem")
_UNSAFE_ARCHIVE_REASONS = frozenset(
    {
        "ARCHIVE_MEMBER_SIZE_LIMIT",
        "ARCHIVE_SYMLINK_NOT_FOLLOWED",
        "DUPLICATE_CASEFOLD_PATH",
        "ENCRYPTED_ARCHIVE_MEMBER",
        "SUSPICIOUS_COMPRESSION_RATIO",
        "UNSAFE_ARCHIVE_PATH",
    }
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


@dataclass(frozen=True, slots=True)
class SourceAuthoritySpec:
    """One exact ordered source occurrence supplied by the user."""

    source: str
    ordinal: int
    lane_id: str
    assertions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FrozenSourceObject:
    """A source identity captured without mutating or copying the source."""

    object_id: str
    source: str
    resolved_pointer: str
    kind: str
    lane_id: str
    identity_sha256: str
    byte_sha256: str | None
    size_bytes: int | None
    member_count: int
    included_member_count: int
    excluded_member_count: int
    member_path_size_sha256: str | None
    content_merkle_sha256: str | None
    members: tuple[dict[str, Any], ...] = ()

    def as_dict(self, *, include_members: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "object_id": self.object_id,
            "source": self.source,
            "resolved_pointer": self.resolved_pointer,
            "kind": self.kind,
            "lane_id": self.lane_id,
            "identity_sha256": self.identity_sha256,
            "byte_sha256": self.byte_sha256,
            "size_bytes": self.size_bytes,
            "member_count": self.member_count,
            "included_member_count": self.included_member_count,
            "excluded_member_count": self.excluded_member_count,
            "member_path_size_sha256": self.member_path_size_sha256,
            "content_merkle_sha256": self.content_merkle_sha256,
        }
        if include_members:
            payload["members"] = list(self.members)
        return payload


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        yield connection
    finally:
        connection.close()


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        str(row["name"]) for row in connection.execute(f'PRAGMA table_info("{table}")')
    }
    if column not in columns:
        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')


def initialize_source_authority_registry(path: str | Path) -> Path:
    """Create the project-local authority registry idempotently."""

    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _connect(target) as connection:
        connection.executescript(
            """
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
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS source_git_change_path_idx "
            "ON source_git_file_change(snapshot_id, member_path, commit_sha)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS source_git_prior_path_idx "
            "ON source_git_file_change(snapshot_id, prior_path, commit_sha)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS source_git_tree_path_idx "
            "ON source_git_tree_entry(snapshot_id, member_path, commit_sha)"
        )
        _ensure_column(
            connection,
            "source_object",
            "member_path_size_sha256",
            "TEXT",
        )
        _ensure_column(
            connection,
            "source_sqlite_asset",
            "byte_sha256",
            "TEXT",
        )
        _ensure_column(
            connection,
            "source_sqlite_asset",
            "size_bytes",
            "INTEGER",
        )
        _ensure_column(
            connection,
            "source_sqlite_asset",
            "canonical_asset_id",
            "TEXT",
        )
        _ensure_column(
            connection,
            "source_sqlite_asset",
            "inspection_receipt_sha256",
            "TEXT",
        )
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS source_authority_fts "
                "USING fts5(object_id UNINDEXED, source_pointer, member_path)"
            )
        except sqlite3.OperationalError as error:
            raise EvidenceLaneError(
                "SOURCE_AUTHORITY_FTS5_UNAVAILABLE",
                "The source authority registry requires SQLite FTS5.",
                status="BLOCKED",
                details={"error": str(error)},
            ) from error
        connection.execute(
            "INSERT OR REPLACE INTO registry_meta(key, value) VALUES (?, ?)",
            ("schema", REGISTRY_SCHEMA),
        )
        connection.commit()
    return target


def reconcile_legacy_source_authority_registry(
    project_root: str | Path,
) -> dict[str, Any]:
    """Move or merge the retired root registry into ``sources/`` exactly once."""

    root = Path(project_root).resolve()
    canonical = root / "sources" / "source_authority.sqlite"
    legacy = root / "source_authority.sqlite"
    receipt_path = root / "sources" / "source_authority_layout_receipt.json"
    if not legacy.is_file():
        return {
            "status": "PASS",
            "state": "CANONICAL_SOURCE_AUTHORITY_ROUTE",
            "canonical_path": str(canonical),
            "legacy_path_present": False,
        }

    canonical.parent.mkdir(parents=True, exist_ok=True)
    legacy_before = sha256_file(legacy)
    canonical_before = sha256_file(canonical) if canonical.is_file() else None
    if not canonical.is_file():
        os.replace(legacy, canonical)
        initialize_source_authority_registry(canonical)
        merge_counts: dict[str, int] = {}
        state = "LEGACY_ROOT_REGISTRY_MOVED_TO_SOURCES"
    else:
        initialize_source_authority_registry(canonical)
        checkpoint = sqlite3.connect(canonical)
        try:
            checkpoint.execute("PRAGMA wal_checkpoint(FULL)")
        finally:
            checkpoint.close()
        with tempfile.TemporaryDirectory(
            prefix=".source-authority-layout-", dir=canonical.parent
        ) as temporary:
            merged = Path(temporary) / canonical.name
            shutil.copy2(canonical, merged)
            connection = sqlite3.connect(merged)
            try:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))
                main_tables = {
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT name FROM main.sqlite_schema
                        WHERE type='table'
                          AND name NOT LIKE 'sqlite_%'
                          AND name NOT LIKE 'source_authority_fts%'
                          AND sql NOT LIKE 'CREATE VIRTUAL TABLE%'
                        """
                    )
                }
                legacy_tables = {
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT name FROM legacy.sqlite_schema
                        WHERE type='table'
                          AND name NOT LIKE 'sqlite_%'
                          AND name NOT LIKE 'source_authority_fts%'
                          AND sql NOT LIKE 'CREATE VIRTUAL TABLE%'
                        """
                    )
                }
                require(
                    legacy_tables <= main_tables,
                    "SOURCE_AUTHORITY_LAYOUT_SCHEMA_MISMATCH",
                    "The retired root source registry contains unknown tables.",
                    status="MISMATCH",
                    unknown_tables=sorted(legacy_tables - main_tables),
                )
                merge_counts = {}
                connection.execute("BEGIN IMMEDIATE")
                for table in sorted(legacy_tables):
                    quoted = table.replace('"', '""')
                    main_columns = [
                        (str(row[1]), int(row[5]))
                        for row in connection.execute(
                            f'PRAGMA main.table_info("{quoted}")'
                        )
                    ]
                    legacy_columns = [
                        (str(row[1]), int(row[5]))
                        for row in connection.execute(
                            f'PRAGMA legacy.table_info("{quoted}")'
                        )
                    ]
                    require(
                        main_columns == legacy_columns,
                        "SOURCE_AUTHORITY_LAYOUT_SCHEMA_MISMATCH",
                        "The canonical and retired source registry table schemas differ.",
                        status="MISMATCH",
                        table=table,
                    )
                    column_names = [name for name, _ in main_columns]
                    primary_keys = [
                        name
                        for name, order in sorted(
                            main_columns, key=lambda item: item[1] or 10_000
                        )
                        if order > 0
                    ]
                    if primary_keys:
                        join = " AND ".join(
                            f'm."{name}" IS l."{name}"' for name in primary_keys
                        )
                        differs = " OR ".join(
                            f'm."{name}" IS NOT l."{name}"' for name in column_names
                        )
                        conflict = connection.execute(
                            f'SELECT 1 FROM main."{quoted}" m '
                            f'JOIN legacy."{quoted}" l ON {join} '
                            f'WHERE {differs} LIMIT 1'
                        ).fetchone()
                        require(
                            conflict is None,
                            "SOURCE_AUTHORITY_LAYOUT_PRIMARY_KEY_CONFLICT",
                            "The canonical and retired source registries disagree on one identity.",
                            status="MISMATCH",
                            table=table,
                        )
                    before = int(
                        connection.execute(
                            f'SELECT COUNT(*) FROM main."{quoted}"'
                        ).fetchone()[0]
                    )
                    columns = ",".join(f'"{name}"' for name in column_names)
                    connection.execute(
                        f'INSERT OR IGNORE INTO main."{quoted}"({columns}) '
                        f'SELECT {columns} FROM legacy."{quoted}"'
                    )
                    after = int(
                        connection.execute(
                            f'SELECT COUNT(*) FROM main."{quoted}"'
                        ).fetchone()[0]
                    )
                    merge_counts[table] = after - before
                connection.execute("DELETE FROM source_authority_fts")
                connection.execute(
                    """
                    INSERT INTO source_authority_fts(
                        object_id,source_pointer,member_path
                    )
                    SELECT member.object_id,object.source_pointer,member.member_path
                    FROM source_member AS member
                    JOIN source_object AS object USING(object_id)
                    ORDER BY member.object_id,member.member_path
                    """
                )
                connection.commit()
                connection.execute("DETACH DATABASE legacy")
                connection.execute("PRAGMA foreign_keys=ON")
                integrity = [
                    str(row[0])
                    for row in connection.execute("PRAGMA integrity_check")
                ]
                foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
                require(
                    integrity == ["ok"] and not foreign_keys,
                    "SOURCE_AUTHORITY_LAYOUT_MERGE_INTEGRITY_FAILED",
                    "The merged canonical source registry failed SQLite integrity checks.",
                    status="MISMATCH",
                )
            finally:
                connection.close()
            # Windows can deny replacing an existing SQLite file even after all
            # Python connections have closed when a short-lived host handle is
            # still draining. SQLite's backup API performs the same page-atomic
            # replacement without changing the canonical pathname.
            source_connection = sqlite3.connect(merged)
            target_connection = sqlite3.connect(canonical)
            try:
                source_connection.backup(target_connection)
                target_connection.commit()
                require(
                    [
                        str(row[0])
                        for row in target_connection.execute(
                            "PRAGMA integrity_check"
                        )
                    ]
                    == ["ok"]
                    and not list(
                        target_connection.execute("PRAGMA foreign_key_check")
                    ),
                    "SOURCE_AUTHORITY_LAYOUT_MERGE_INTEGRITY_FAILED",
                    "The canonical source registry failed post-backup integrity checks.",
                    status="MISMATCH",
                )
            finally:
                target_connection.close()
                source_connection.close()
        legacy.unlink()
        state = "LEGACY_ROOT_REGISTRY_MERGED_INTO_SOURCES"

    receipt_body = {
        "schema": "evidence-lane.source-authority-layout-migration.v1",
        "status": "PASS",
        "state": state,
        "canonical_relative_path": "sources/source_authority.sqlite",
        "retired_relative_path": "source_authority.sqlite",
        "legacy_sha256": legacy_before,
        "canonical_before_sha256": canonical_before,
        "canonical_after_sha256": sha256_file(canonical),
        "inserted_row_counts": merge_counts,
        "legacy_path_present_after": legacy.exists(),
        "payload_reingested": False,
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    atomic_write_json(receipt_path, receipt)
    return receipt


def _policy_reason(relative_path: str, policy: Mapping[str, Any]) -> tuple[str, str]:
    parts = tuple(part.casefold() for part in PurePosixPath(relative_path).parts)
    basename = parts[-1] if parts else ""
    excluded_dirs = {
        str(value).casefold()
        for value in policy.get("excluded_directory_names", _RUNTIME_DIRECTORY_NAMES)
    }
    if any(part in excluded_dirs for part in parts[:-1]):
        return "EXCLUDED", "RUNTIME_DIRECTORY"
    if basename in _SECRET_BASENAMES or basename.startswith(".env."):
        return "EXCLUDED", "SECRET_SHAPED_BASENAME"
    if basename.endswith(_SECRET_SUFFIXES):
        return "EXCLUDED", "SECRET_SHAPED_SUFFIX"
    return "INCLUDED", "POLICY_APPROVED"


def _stable_file_identity(path: Path) -> tuple[int, str]:
    before = path.stat()
    require(
        stat.S_ISREG(before.st_mode),
        "SOURCE_AUTHORITY_NOT_REGULAR_FILE",
        "Only regular file bytes may be hashed as file authority.",
        status="BLOCKED",
        source=str(path),
    )
    digest = sha256_file(path)
    after = path.stat()
    require(
        (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
        "SOURCE_AUTHORITY_CHANGED_DURING_READ",
        "A source changed while its authority identity was being captured.",
        status="STALE",
        source=str(path),
    )
    return after.st_size, digest


def _directory_members(
    root: Path, policy: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    members: list[dict[str, Any]] = []
    max_members = int(policy.get("max_members", 250_000))
    for candidate in sorted(
        root.rglob("*"), key=lambda item: item.as_posix().casefold()
    ):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            members.append(
                {
                    "member_path": relative,
                    "member_kind": "symlink",
                    "size_bytes": None,
                    "sha256": None,
                    "policy_state": "EXCLUDED",
                    "policy_reason": "SYMLINK_NOT_FOLLOWED",
                }
            )
        elif candidate.is_file():
            policy_state, reason = _policy_reason(relative, policy)
            if policy_state == "INCLUDED":
                size_bytes, digest = _stable_file_identity(candidate)
            else:
                size_bytes = candidate.stat().st_size
                digest = None
            members.append(
                {
                    "member_path": relative,
                    "member_kind": "file",
                    "size_bytes": size_bytes,
                    "sha256": digest,
                    "policy_state": policy_state,
                    "policy_reason": reason,
                }
            )
        if len(members) > max_members:
            raise EvidenceLaneError(
                "SOURCE_AUTHORITY_MEMBER_LIMIT_EXCEEDED",
                "A source directory exceeded the governed member limit.",
                status="BLOCKED",
                details={"source": str(root), "max_members": max_members},
            )
    return tuple(members)


def _normalize_archive_name(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith(("/", "//"))
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(part.endswith((" ", ".")) for part in pure.parts)
        or any(
            part.casefold().split(".", 1)[0] in _WINDOWS_RESERVED_NAMES
            for part in pure.parts
        )
    ):
        return None
    return pure.as_posix()


def _archive_member_disposition(
    info: zipfile.ZipInfo,
    seen: set[str],
    policy: Mapping[str, Any],
    *,
    max_member_bytes: int,
    max_compression_ratio: float,
) -> tuple[str, str, str]:
    member_path = _normalize_archive_name(info.filename)
    if member_path is None:
        policy_state, reason = "EXCLUDED", "UNSAFE_ARCHIVE_PATH"
        member_path = f"__unsafe__/{sha256_bytes(info.filename.encode('utf-8'))}"
    elif member_path.casefold() in seen:
        policy_state, reason = "EXCLUDED", "DUPLICATE_CASEFOLD_PATH"
    elif info.flag_bits & 0x1:
        policy_state, reason = "EXCLUDED", "ENCRYPTED_ARCHIVE_MEMBER"
    elif stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
        policy_state, reason = "EXCLUDED", "ARCHIVE_SYMLINK_NOT_FOLLOWED"
    elif info.file_size > max_member_bytes:
        policy_state, reason = "EXCLUDED", "ARCHIVE_MEMBER_SIZE_LIMIT"
    elif info.file_size and (
        info.compress_size == 0
        or info.file_size / info.compress_size > max_compression_ratio
    ):
        policy_state, reason = "EXCLUDED", "SUSPICIOUS_COMPRESSION_RATIO"
    else:
        policy_state, reason = _policy_reason(member_path, policy)
    seen.add(member_path.casefold())
    return member_path, policy_state, reason


def archive_safety_profile(
    archive_path: str | Path, policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Inspect ZIP central-directory safety without extraction or source mutation."""

    exact_policy = policy or {}
    target = Path(archive_path).expanduser().resolve()
    max_members = int(exact_policy.get("max_members", 250_000))
    max_member_bytes = int(exact_policy.get("max_archive_member_bytes", 1024**3))
    max_total_bytes = int(exact_policy.get("max_archive_total_bytes", 4 * 1024**3))
    max_compression_ratio = float(
        exact_policy.get("max_archive_compression_ratio", 1000.0)
    )
    try:
        with zipfile.ZipFile(target) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            total_uncompressed = sum(info.file_size for info in infos)
            total_compressed = sum(info.compress_size for info in infos)
            if len(infos) > max_members:
                return {
                    "schema": "evidence-lane.archive-safety-profile.v1",
                    "status": "BLOCKED",
                    "code": "SOURCE_AUTHORITY_ARCHIVE_MEMBER_LIMIT_EXCEEDED",
                    "member_count": len(infos),
                    "max_members": max_members,
                    "source_bytes_mutated": False,
                    "source_payloads_extracted": False,
                }
            if total_uncompressed > max_total_bytes:
                return {
                    "schema": "evidence-lane.archive-safety-profile.v1",
                    "status": "BLOCKED",
                    "code": "SOURCE_AUTHORITY_ARCHIVE_SIZE_LIMIT_EXCEEDED",
                    "declared_uncompressed_bytes": total_uncompressed,
                    "max_archive_total_bytes": max_total_bytes,
                    "source_bytes_mutated": False,
                    "source_payloads_extracted": False,
                }
            seen: set[str] = set()
            reasons: list[str] = []
            policy_excluded_count = 0
            for info in sorted(infos, key=lambda row: row.filename.casefold()):
                _, state, reason = _archive_member_disposition(
                    info,
                    seen,
                    exact_policy,
                    max_member_bytes=max_member_bytes,
                    max_compression_ratio=max_compression_ratio,
                )
                if reason in _UNSAFE_ARCHIVE_REASONS:
                    reasons.append(reason)
                elif state == "EXCLUDED":
                    policy_excluded_count += 1
    except (OSError, zipfile.BadZipFile) as error:
        return {
            "schema": "evidence-lane.archive-safety-profile.v1",
            "status": "MISMATCH",
            "code": "SOURCE_AUTHORITY_ARCHIVE_UNREADABLE",
            "error": str(error),
            "source_bytes_mutated": False,
            "source_payloads_extracted": False,
        }
    return {
        "schema": "evidence-lane.archive-safety-profile.v1",
        "status": "BLOCKED" if reasons else "PASS",
        "code": "UNSAFE_ARCHIVE_MEMBER" if reasons else None,
        "member_count": len(infos),
        "declared_uncompressed_bytes": total_uncompressed,
        "declared_compressed_bytes": total_compressed,
        "unsafe_member_count": len(reasons),
        "unsafe_reasons": sorted(set(reasons)),
        "policy_excluded_member_count": policy_excluded_count,
        "max_members": max_members,
        "max_archive_member_bytes": max_member_bytes,
        "max_archive_total_bytes": max_total_bytes,
        "max_archive_compression_ratio": max_compression_ratio,
        "source_bytes_mutated": False,
        "source_payloads_extracted": False,
    }


def _archive_members(
    archive_path: Path, policy: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    members: list[dict[str, Any]] = []
    max_members = int(policy.get("max_members", 250_000))
    max_member_bytes = int(policy.get("max_archive_member_bytes", 1024**3))
    max_total_bytes = int(policy.get("max_archive_total_bytes", 4 * 1024**3))
    max_compression_ratio = float(policy.get("max_archive_compression_ratio", 1000.0))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            require(
                len(infos) <= max_members,
                "SOURCE_AUTHORITY_ARCHIVE_MEMBER_LIMIT_EXCEEDED",
                "An archive exceeded the governed member limit.",
                status="BLOCKED",
                source=str(archive_path),
                max_members=max_members,
            )
            require(
                sum(info.file_size for info in infos) <= max_total_bytes,
                "SOURCE_AUTHORITY_ARCHIVE_SIZE_LIMIT_EXCEEDED",
                "An archive exceeded the governed declared-size limit.",
                status="BLOCKED",
                source=str(archive_path),
                max_total_bytes=max_total_bytes,
            )
            seen: set[str] = set()
            for info in sorted(infos, key=lambda row: row.filename.casefold()):
                member_path, policy_state, reason = _archive_member_disposition(
                    info,
                    seen,
                    policy,
                    max_member_bytes=max_member_bytes,
                    max_compression_ratio=max_compression_ratio,
                )
                digest: str | None = None
                if policy_state == "INCLUDED":
                    import hashlib

                    hasher = hashlib.sha256()
                    with archive.open(info, "r") as handle:
                        while block := handle.read(1024 * 1024):
                            hasher.update(block)
                    digest = hasher.hexdigest().upper()
                members.append(
                    {
                        "member_path": member_path,
                        "member_kind": "archive_member",
                        "size_bytes": info.file_size,
                        "sha256": digest,
                        "policy_state": policy_state,
                        "policy_reason": reason,
                    }
                )
    except (OSError, zipfile.BadZipFile) as error:
        raise EvidenceLaneError(
            "SOURCE_AUTHORITY_ARCHIVE_UNREADABLE",
            "An archive could not be indexed safely.",
            status="BLOCKED",
            details={"source": str(archive_path), "error": str(error)},
        ) from error
    return tuple(members)


def _member_merkle(members: Iterable[Mapping[str, Any]]) -> str:
    approved = [
        {
            "path": str(member["member_path"]),
            "bytes": member.get("size_bytes"),
            "sha256": member.get("sha256"),
        }
        for member in members
        if member.get("policy_state") == "INCLUDED" and member.get("sha256")
    ]
    return sha256_bytes(canonical_json_bytes(approved))


def _member_path_size_identity(
    members: Iterable[Mapping[str, Any]],
) -> str:
    manifest = [
        {
            "path": str(member["member_path"]),
            "kind": str(member["member_kind"]),
            "bytes": member.get("size_bytes"),
            "policy_state": str(member["policy_state"]),
            "policy_reason": str(member["policy_reason"]),
        }
        for member in members
    ]
    return sha256_bytes(canonical_json_bytes(manifest))


def freeze_source_authority(
    spec: SourceAuthoritySpec, policy: Mapping[str, Any] | None = None
) -> FrozenSourceObject:
    """Capture one stable source identity through read-only operations."""

    exact_policy = policy or {}
    source = spec.source.strip()
    require(
        bool(source),
        "SOURCE_AUTHORITY_POINTER_EMPTY",
        "A source authority pointer cannot be empty.",
        status="BLOCKED",
    )
    path = Path(source).expanduser()
    if path.exists() and path.is_file():
        size_bytes, byte_sha256 = _stable_file_identity(path)
        kind = "zip" if path.suffix.casefold() == ".zip" else "file"
        before_archive_scan = path.stat() if kind == "zip" else None
        members = _archive_members(path, exact_policy) if kind == "zip" else ()
        if before_archive_scan is not None:
            after_archive_scan = path.stat()
            require(
                (before_archive_scan.st_size, before_archive_scan.st_mtime_ns)
                == (after_archive_scan.st_size, after_archive_scan.st_mtime_ns),
                "SOURCE_AUTHORITY_CHANGED_DURING_READ",
                "An archive changed while its member authority was being captured.",
                status="STALE",
                source=str(path),
            )
        merkle = _member_merkle(members) if members else None
        path_size_identity = _member_path_size_identity(members) if members else None
        resolved = str(path.resolve())
    elif path.exists() and path.is_dir():
        kind = "directory"
        members = _directory_members(path, exact_policy)
        merkle = _member_merkle(members)
        path_size_identity = _member_path_size_identity(members)
        size_bytes = sum(
            int(member["size_bytes"] or 0)
            for member in members
            if member["member_kind"] == "file"
        )
        byte_sha256 = None
        resolved = str(path.resolve())
    else:
        kind = "remote_or_declared"
        members = ()
        merkle = None
        path_size_identity = None
        size_bytes = None
        byte_sha256 = None
        resolved = source
    included = sum(member["policy_state"] == "INCLUDED" for member in members)
    excluded = len(members) - included
    identity_body = {
        "schema": "evidence-lane.frozen-source-object.v1",
        "source": source,
        "resolved_pointer": resolved,
        "kind": kind,
        "lane_id": spec.lane_id,
        "byte_sha256": byte_sha256,
        "size_bytes": size_bytes,
        "member_count": len(members),
        "included_member_count": included,
        "excluded_member_count": excluded,
        "member_path_size_sha256": path_size_identity,
        "content_merkle_sha256": merkle,
    }
    identity_sha256 = sha256_bytes(canonical_json_bytes(identity_body))
    return FrozenSourceObject(
        object_id=f"source_{identity_sha256[:32].lower()}",
        source=source,
        resolved_pointer=resolved,
        kind=kind,
        lane_id=spec.lane_id,
        identity_sha256=identity_sha256,
        byte_sha256=byte_sha256,
        size_bytes=size_bytes,
        member_count=len(members),
        included_member_count=included,
        excluded_member_count=excluded,
        member_path_size_sha256=path_size_identity,
        content_merkle_sha256=merkle,
        members=members,
    )


def _approved_catalog(
    source: FrozenSourceObject, *, ignore_archive_echo_sha256: str | None = None
) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "path": member["member_path"],
                "bytes": member["size_bytes"],
                "sha256": member["sha256"],
            }
            for member in source.members
            if member["policy_state"] == "INCLUDED"
            and member["sha256"]
            and not (
                ignore_archive_echo_sha256
                and str(member["sha256"]) == ignore_archive_echo_sha256
                and PurePosixPath(str(member["member_path"])).suffix.casefold()
                == ".zip"
            )
        ],
        key=lambda row: str(row["path"]).casefold(),
    )


def _catalog_variants(
    source: FrozenSourceObject, *, ignore_archive_echo_sha256: str | None = None
) -> dict[str, dict[str, Any]]:
    approved = _approved_catalog(
        source,
        ignore_archive_echo_sha256=ignore_archive_echo_sha256,
    )
    variants: dict[str, dict[str, Any]] = {}
    current = approved
    removed_prefix: list[str] = []
    for _ in range(8):
        variants.setdefault(
            sha256_bytes(canonical_json_bytes(current)),
            {
                "member_count": len(current),
                "removed_prefix": "/".join(removed_prefix) or None,
            },
        )
        paths = [PurePosixPath(str(row["path"])) for row in current]
        if not paths or not all(len(path.parts) > 1 for path in paths):
            break
        first_parts = {path.parts[0].casefold() for path in paths}
        if len(first_parts) != 1:
            break
        removed_prefix.append(paths[0].parts[0])
        current = [
            {**row, "path": "/".join(PurePosixPath(str(row["path"])).parts[1:])}
            for row in current
        ]
    return variants


def _subtree_catalog_variants(
    source: FrozenSourceObject,
    *,
    required_member_count: int,
    ignore_archive_echo_sha256: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Hash exact directory subtrees without treating surrounding files as equal."""

    approved = _approved_catalog(
        source,
        ignore_archive_echo_sha256=ignore_archive_echo_sha256,
    )
    variants: dict[str, dict[str, Any]] = {}
    for depth in range(1, 9):
        grouped: defaultdict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        display_prefixes: dict[tuple[str, ...], tuple[str, ...]] = {}
        for row in approved:
            parts = PurePosixPath(str(row["path"])).parts
            if len(parts) <= depth:
                continue
            folded = tuple(part.casefold() for part in parts[:depth])
            grouped[folded].append(row)
            display_prefixes.setdefault(folded, parts[:depth])
        for folded_prefix in sorted(grouped):
            rows = grouped[folded_prefix]
            if len(rows) != required_member_count:
                continue
            stripped = sorted(
                [
                    {
                        **row,
                        "path": "/".join(PurePosixPath(str(row["path"])).parts[depth:]),
                    }
                    for row in rows
                ],
                key=lambda row: str(row["path"]).casefold(),
            )
            variants.setdefault(
                sha256_bytes(canonical_json_bytes(stripped)),
                {
                    "member_count": len(stripped),
                    "matched_prefix": "/".join(display_prefixes[folded_prefix]),
                },
            )
    return variants


def _counterpart_relations(
    frozen: list[FrozenSourceObject], batch_id: str
) -> list[dict[str, Any]]:
    directories = [row for row in frozen if row.kind == "directory"]
    archives = [row for row in frozen if row.kind == "zip"]
    relations: list[dict[str, Any]] = []
    for archive in archives:
        unsafe_reasons = sorted(
            {
                str(member["policy_reason"])
                for member in archive.members
                if member["policy_reason"] in _UNSAFE_ARCHIVE_REASONS
            }
        )
        if unsafe_reasons:
            continue
        archive_variants = _catalog_variants(archive)
        archive_member_count = len(_approved_catalog(archive))
        for directory in directories:
            directory_variants = _catalog_variants(
                directory,
                ignore_archive_echo_sha256=archive.byte_sha256,
            )
            matching = sorted(set(archive_variants) & set(directory_variants))
            catalog_scope = (
                "EXACT_WHOLE_DIRECTORY_POLICY_APPROVED_MEMBER_PATH_SIZE_SHA256"
            )
            matched_directory_prefix: str | None = None
            matched_hash = matching[0] if matching else None
            if matched_hash is None:
                subtree_variants = _subtree_catalog_variants(
                    directory,
                    required_member_count=archive_member_count,
                    ignore_archive_echo_sha256=archive.byte_sha256,
                )
                matching = sorted(set(archive_variants) & set(subtree_variants))
                if not matching:
                    continue
                matched_hash = matching[0]
                catalog_scope = (
                    "EXACT_NESTED_SUBTREE_POLICY_APPROVED_MEMBER_PATH_SIZE_SHA256"
                )
                matched_directory_prefix = str(
                    subtree_variants[matched_hash]["matched_prefix"]
                )
            body = {
                "schema": "evidence-lane.source-relation.v1",
                "batch_id": batch_id,
                "left_object_id": archive.object_id,
                "relation_type": "EXACT_EXTRACTED_COUNTERPART",
                "right_object_id": directory.object_id,
                "catalog_scope": catalog_scope,
                "archive_prefix_removed": archive_variants[matched_hash][
                    "removed_prefix"
                ],
                "matched_directory_prefix": matched_directory_prefix,
                "matched_member_count": archive_member_count,
                "unsafe_archive_member_count": 0,
            }
            relations.append(
                {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}
            )
            break
    return relations


def register_source_batch(
    registry_path: str | Path,
    specs: Iterable[SourceAuthoritySpec],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze and append one immutable ordered source batch."""

    exact_specs = sorted(specs, key=lambda row: row.ordinal)
    require(
        bool(exact_specs),
        "SOURCE_AUTHORITY_BATCH_EMPTY",
        "A source authority batch requires at least one source.",
        status="BLOCKED",
    )
    require(
        [row.ordinal for row in exact_specs] == list(range(1, len(exact_specs) + 1)),
        "SOURCE_AUTHORITY_ORDINAL_GAP",
        "Source authority ordinals must be the exact contiguous 1..N sequence.",
        status="BLOCKED",
    )
    frozen = [freeze_source_authority(spec, policy) for spec in exact_specs]
    batch_body = {
        "schema": BATCH_SCHEMA,
        "occurrences": [
            {
                "ordinal": spec.ordinal,
                "object_id": source.object_id,
                "source": spec.source,
                "lane_id": spec.lane_id,
                "identity_sha256": source.identity_sha256,
                "assertions_sha256": sha256_bytes(
                    canonical_json_bytes(dict(spec.assertions))
                ),
            }
            for spec, source in zip(exact_specs, frozen, strict=True)
        ],
    }
    batch_sha256 = sha256_bytes(canonical_json_bytes(batch_body))
    batch_id = f"intake_{batch_sha256[:32].lower()}"
    relations = _counterpart_relations(frozen, batch_id)
    directory_count = sum(row.kind == "directory" for row in frozen)
    file_count = sum(row.kind in {"file", "zip"} for row in frozen)
    zip_count = sum(row.kind == "zip" for row in frozen)
    related_zip_ids = {row["left_object_id"] for row in relations}
    summary = {
        "schema": BATCH_SCHEMA,
        "batch_id": batch_id,
        "batch_sha256": batch_sha256,
        "source_count": len(frozen),
        "directory_count": directory_count,
        "file_count": file_count,
        "zip_count": zip_count,
        "exact_extracted_zip_relations": len(relations),
        "unique_zip_count": zip_count - len(related_zip_ids),
        "ordinals_complete": True,
        "source_bytes_mutated": False,
        "source_payloads_copied": False,
    }
    target = initialize_source_authority_registry(registry_path)
    with _connect(target) as connection:
        existing = connection.execute(
            "SELECT batch_sha256 FROM intake_batch WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if existing:
            require(
                existing["batch_sha256"] == batch_sha256,
                "SOURCE_AUTHORITY_BATCH_COLLISION",
                "An immutable source batch ID resolved to different bytes.",
                status="MISMATCH",
            )
            archive_intake = reconcile_archive_counterparts(target, batch_id)
            return {
                **summary,
                "append_status": "IDEMPOTENT_REUSE",
                "archive_intake": archive_intake,
            }
        with connection:
            connection.execute(
                """INSERT INTO intake_batch VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch_id,
                    batch_sha256,
                    len(frozen),
                    directory_count,
                    file_count,
                    zip_count,
                    len(relations),
                    zip_count - len(related_zip_ids),
                    utc_now(),
                ),
            )
            for spec, source in zip(exact_specs, frozen, strict=True):
                connection.execute(
                    """INSERT OR IGNORE INTO source_object(
                        object_id, source_pointer, resolved_pointer, kind, lane_id,
                        identity_sha256, byte_sha256, size_bytes, member_count,
                        included_member_count, excluded_member_count,
                        member_path_size_sha256, content_merkle_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source.object_id,
                        source.source,
                        source.resolved_pointer,
                        source.kind,
                        source.lane_id,
                        source.identity_sha256,
                        source.byte_sha256,
                        source.size_bytes,
                        source.member_count,
                        source.included_member_count,
                        source.excluded_member_count,
                        source.member_path_size_sha256,
                        source.content_merkle_sha256,
                    ),
                )
                connection.execute(
                    "INSERT INTO source_occurrence VALUES (?, ?, ?, ?, ?)",
                    (
                        batch_id,
                        spec.ordinal,
                        source.object_id,
                        spec.source,
                        spec.lane_id,
                    ),
                )
                for member in source.members:
                    connection.execute(
                        "INSERT OR IGNORE INTO source_member VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            source.object_id,
                            member["member_path"],
                            member["member_kind"],
                            member["size_bytes"],
                            member["sha256"],
                            member["policy_state"],
                            member["policy_reason"],
                        ),
                    )
                    connection.execute(
                        "INSERT INTO source_authority_fts(object_id, source_pointer, member_path) VALUES (?, ?, ?)",
                        (source.object_id, source.source, member["member_path"]),
                    )
                policy_receipt = {
                    "schema": "evidence-lane.source-policy.receipt.v1",
                    "object_id": source.object_id,
                    "included_member_count": source.included_member_count,
                    "excluded_member_count": source.excluded_member_count,
                    "excluded_content_stored": False,
                }
                policy_sha = sha256_bytes(canonical_json_bytes(policy_receipt))
                connection.execute(
                    "INSERT OR IGNORE INTO source_policy_receipt VALUES (?, ?, ?, ?, ?)",
                    (
                        source.object_id,
                        source.included_member_count,
                        source.excluded_member_count,
                        canonical_json_bytes(policy_receipt).decode("utf-8").strip(),
                        policy_sha,
                    ),
                )
                for claim_key, claim_value in sorted(spec.assertions.items()):
                    connection.execute(
                        "INSERT INTO source_provenance VALUES (?, ?, ?, ?, ?)",
                        (
                            batch_id,
                            source.object_id,
                            str(claim_key),
                            canonical_json_bytes(claim_value).decode("utf-8").strip(),
                            "USER_STATED",
                        ),
                    )
            for relation in relations:
                connection.execute(
                    "INSERT INTO source_relation VALUES (?, ?, ?, ?, ?)",
                    (
                        batch_id,
                        relation["left_object_id"],
                        relation["relation_type"],
                        relation["right_object_id"],
                        relation["receipt_sha256"],
                    ),
                )
            event = {**summary, "event_type": "source.authority.batch.registered"}
            event_sha = sha256_bytes(canonical_json_bytes(event))
            connection.execute(
                "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"registry_{event_sha[:32].lower()}",
                    batch_id,
                    "source.authority.batch.registered",
                    canonical_json_bytes(event).decode("utf-8").strip(),
                    event_sha,
                    utc_now(),
                ),
            )
    archive_intake = reconcile_archive_counterparts(target, batch_id)
    return {
        **summary,
        "append_status": "APPENDED",
        "archive_intake": archive_intake,
    }


def _load_registry_frozen_sources(
    registry_path: Path, batch_id: str
) -> list[FrozenSourceObject]:
    with _connect(registry_path) as connection:
        rows = list(
            connection.execute(
                """SELECT s.* FROM source_object s
                JOIN source_occurrence o USING(object_id)
                WHERE o.batch_id=? AND s.kind IN ('directory', 'zip')
                GROUP BY s.object_id ORDER BY MIN(o.ordinal)""",
                (batch_id,),
            )
        )
        frozen: list[FrozenSourceObject] = []
        for row in rows:
            members = tuple(
                dict(member)
                for member in connection.execute(
                    "SELECT * FROM source_member WHERE object_id=? "
                    "ORDER BY member_path COLLATE NOCASE, member_path",
                    (row["object_id"],),
                )
            )
            frozen.append(
                FrozenSourceObject(
                    object_id=str(row["object_id"]),
                    source=str(row["source_pointer"]),
                    resolved_pointer=str(row["resolved_pointer"]),
                    kind=str(row["kind"]),
                    lane_id=str(row["lane_id"]),
                    identity_sha256=str(row["identity_sha256"]),
                    byte_sha256=row["byte_sha256"],
                    size_bytes=row["size_bytes"],
                    member_count=int(row["member_count"]),
                    included_member_count=int(row["included_member_count"]),
                    excluded_member_count=int(row["excluded_member_count"]),
                    member_path_size_sha256=row["member_path_size_sha256"],
                    content_merkle_sha256=row["content_merkle_sha256"],
                    members=members,
                )
            )
    return frozen


def reconcile_archive_counterparts(
    registry_path: str | Path, batch_id: str
) -> dict[str, Any]:
    """Append safe archive-use receipts from one immutable registered batch."""

    target = initialize_source_authority_registry(registry_path)
    with _connect(target) as connection:
        batch = connection.execute(
            "SELECT * FROM intake_batch WHERE batch_id=?", (batch_id,)
        ).fetchone()
    require(
        batch is not None,
        "SOURCE_AUTHORITY_BATCH_MISSING",
        "The requested source authority batch is not registered.",
        status="MISMATCH",
        batch_id=batch_id,
    )
    frozen = _load_registry_frozen_sources(target, batch_id)
    archives = [row for row in frozen if row.kind == "zip"]
    relations = _counterpart_relations(frozen, batch_id)
    relation_by_archive = {str(row["left_object_id"]): row for row in relations}
    receipts: list[dict[str, Any]] = []
    for archive in archives:
        unsafe_reasons = sorted(
            {
                str(member["policy_reason"])
                for member in archive.members
                if member["policy_reason"] in _UNSAFE_ARCHIVE_REASONS
            }
        )
        unsafe_member_count = sum(
            member["policy_reason"] in _UNSAFE_ARCHIVE_REASONS
            for member in archive.members
        )
        policy_excluded_member_count = sum(
            member["policy_state"] == "EXCLUDED"
            and member["policy_reason"] not in _UNSAFE_ARCHIVE_REASONS
            for member in archive.members
        )
        relation = relation_by_archive.get(archive.object_id)
        if unsafe_member_count:
            intake_status = "BLOCKED"
            skip_status = "BLOCKED_UNSAFE_ARCHIVE"
        elif relation:
            intake_status = "PASS"
            skip_status = "SKIP_ARCHIVE_USE_EXACT_EXTRACTED_COUNTERPART"
        else:
            intake_status = "PASS"
            skip_status = "USE_ARCHIVE_AS_INDEPENDENT_AUTHORITY"
        receipt = {
            "schema": "evidence-lane.archive-intake.receipt.v1",
            "batch_id": batch_id,
            "archive_object_id": archive.object_id,
            "archive_pointer": archive.source,
            "archive_byte_sha256": archive.byte_sha256,
            "member_count": archive.member_count,
            "included_member_count": archive.included_member_count,
            "unsafe_member_count": unsafe_member_count,
            "unsafe_reasons": unsafe_reasons,
            "policy_excluded_member_count": policy_excluded_member_count,
            "intake_status": intake_status,
            "skip_status": skip_status,
            "counterpart_object_id": (
                relation["right_object_id"] if relation else None
            ),
            "counterpart_scope": relation.get("catalog_scope") if relation else None,
            "matched_prefix": (
                relation.get("matched_directory_prefix") if relation else None
            ),
            "source_bytes_mutated": False,
            "source_payloads_extracted": False,
            "candidate_created": False,
            "pointer_moved": False,
        }
        receipt_sha = sha256_bytes(canonical_json_bytes(receipt))
        receipts.append({**receipt, "receipt_sha256": receipt_sha})
    event = {
        "schema": "evidence-lane.archive-intake.reconciliation.v1",
        "batch_id": batch_id,
        "archive_count": len(archives),
        "skip_eligible_count": sum(
            row["skip_status"] == "SKIP_ARCHIVE_USE_EXACT_EXTRACTED_COUNTERPART"
            for row in receipts
        ),
        "independent_archive_count": sum(
            row["skip_status"] == "USE_ARCHIVE_AS_INDEPENDENT_AUTHORITY"
            for row in receipts
        ),
        "blocked_archive_count": sum(
            row["skip_status"] == "BLOCKED_UNSAFE_ARCHIVE" for row in receipts
        ),
        "exact_extracted_zip_relations": len(relations),
        "receipt_sha256": sorted(row["receipt_sha256"] for row in receipts),
        "source_bytes_mutated": False,
        "source_payloads_extracted": False,
    }
    event_sha = sha256_bytes(canonical_json_bytes(event))
    append_status = "IDEMPOTENT_REUSE"
    with _connect(target) as connection, connection:
        for relation in relations:
            existing_relation = connection.execute(
                """SELECT receipt_sha256 FROM source_relation
                WHERE batch_id=? AND left_object_id=? AND relation_type=?
                AND right_object_id=?""",
                (
                    batch_id,
                    relation["left_object_id"],
                    relation["relation_type"],
                    relation["right_object_id"],
                ),
            ).fetchone()
            if existing_relation is None:
                append_status = "APPENDED"
                connection.execute(
                    "INSERT INTO source_relation VALUES (?, ?, ?, ?, ?)",
                    (
                        batch_id,
                        relation["left_object_id"],
                        relation["relation_type"],
                        relation["right_object_id"],
                        relation["receipt_sha256"],
                    ),
                )
        for receipt in receipts:
            existing_receipt = connection.execute(
                """SELECT receipt_sha256 FROM source_archive_receipt
                WHERE batch_id=? AND archive_object_id=?""",
                (batch_id, receipt["archive_object_id"]),
            ).fetchone()
            require(
                existing_receipt is None
                or existing_receipt["receipt_sha256"] == receipt["receipt_sha256"],
                "SOURCE_ARCHIVE_RECEIPT_MISMATCH",
                "An immutable archive intake receipt resolved to different evidence.",
                status="MISMATCH",
                batch_id=batch_id,
                archive_object_id=receipt["archive_object_id"],
            )
            if existing_receipt is None:
                append_status = "APPENDED"
                connection.execute(
                    "INSERT INTO source_archive_receipt VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        batch_id,
                        receipt["archive_object_id"],
                        receipt["intake_status"],
                        receipt["skip_status"],
                        receipt["counterpart_object_id"],
                        receipt["counterpart_scope"],
                        receipt["matched_prefix"],
                        receipt["unsafe_member_count"],
                        receipt["policy_excluded_member_count"],
                        canonical_json_bytes(receipt).decode("utf-8").strip(),
                        receipt["receipt_sha256"],
                        utc_now(),
                    ),
                )
        event_id = f"registry_{event_sha[:32].lower()}"
        if (
            connection.execute(
                "SELECT 1 FROM registry_event WHERE event_id=?", (event_id,)
            ).fetchone()
            is None
        ):
            append_status = "APPENDED"
            connection.execute(
                "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    batch_id,
                    "source.archive.intake.reconciled",
                    canonical_json_bytes(event).decode("utf-8").strip(),
                    event_sha,
                    utc_now(),
                ),
            )
    return {
        "status": "PASS",
        "append_status": append_status,
        **event,
        "event_sha256": event_sha,
        "receipts": receipts,
    }


def load_source_batch(registry_path: str | Path, batch_id: str) -> dict[str, Any]:
    target = Path(registry_path).resolve()
    require(
        target.is_file(),
        "SOURCE_AUTHORITY_REGISTRY_MISSING",
        "The project source authority registry does not exist.",
        status="MISMATCH",
        registry_path=str(target),
    )
    with _connect(target) as connection:
        batch = connection.execute(
            "SELECT * FROM intake_batch WHERE batch_id=?", (batch_id,)
        ).fetchone()
        require(
            batch is not None,
            "SOURCE_AUTHORITY_BATCH_MISSING",
            "The requested source authority batch is not registered.",
            status="MISMATCH",
            batch_id=batch_id,
        )
        occurrences = [
            dict(row)
            for row in connection.execute(
                """SELECT o.ordinal, o.supplied_pointer, o.lane_id, s.*
                FROM source_occurrence o JOIN source_object s USING(object_id)
                WHERE o.batch_id=? ORDER BY o.ordinal""",
                (batch_id,),
            )
        ]
        relations = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM source_relation WHERE batch_id=? ORDER BY left_object_id, right_object_id",
                (batch_id,),
            )
        ]
        provenance = [
            {**dict(row), "claim": json.loads(row["claim_json"])}
            for row in connection.execute(
                "SELECT * FROM source_provenance WHERE batch_id=? ORDER BY object_id, claim_key",
                (batch_id,),
            )
        ]
        assertion_sets = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM source_assertion_set WHERE batch_id=? "
                "ORDER BY assertion_set_id",
                (batch_id,),
            )
        ]
        archive_receipts = [
            {
                **dict(row),
                "receipt": json.loads(row["receipt_json"]),
            }
            for row in connection.execute(
                "SELECT * FROM source_archive_receipt WHERE batch_id=? "
                "ORDER BY archive_object_id",
                (batch_id,),
            )
        ]
    return {
        "schema": BATCH_SCHEMA,
        **dict(batch),
        "occurrences": occurrences,
        "relations": relations,
        "provenance": provenance,
        "assertion_sets": assertion_sets,
        "archive_receipts": archive_receipts,
    }


def register_source_crosswalk(
    registry_path: str | Path,
    batch_id: str,
    crosswalk_path: str | Path,
) -> dict[str, Any]:
    """Bind a reviewed use/reject crosswalk to an existing immutable batch."""

    target = initialize_source_authority_registry(registry_path)
    crosswalk_file = Path(crosswalk_path).resolve()
    require(
        crosswalk_file.is_file(),
        "SOURCE_CROSSWALK_MISSING",
        "The source use/reject crosswalk does not exist.",
        status="BLOCKED",
        crosswalk_path=str(crosswalk_file),
    )
    try:
        crosswalk = json.loads(crosswalk_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceLaneError(
            "SOURCE_CROSSWALK_INVALID_JSON",
            "The source use/reject crosswalk is not valid UTF-8 JSON.",
            status="BLOCKED",
            details={"error": str(error)},
        ) from error
    require(
        isinstance(crosswalk, dict)
        and crosswalk.get("schema") == "evidence-lane.all-source-authority-crosswalk.v1"
        and isinstance(crosswalk.get("sources"), list),
        "SOURCE_CROSSWALK_SCHEMA_INVALID",
        "The source use/reject crosswalk schema is unsupported.",
        status="BLOCKED",
    )
    registered = load_source_batch(target, batch_id)
    source_rows = crosswalk["sources"]
    require(
        len(source_rows) == registered["source_count"],
        "SOURCE_CROSSWALK_COUNT_MISMATCH",
        "The source crosswalk must account for every registered occurrence.",
        status="MISMATCH",
        registered_count=registered["source_count"],
        crosswalk_count=len(source_rows),
    )
    occurrence_by_ordinal = {
        int(row["ordinal"]): row for row in registered["occurrences"]
    }
    claim_rows: list[tuple[str, str, str, str]] = []
    seen_names: set[str] = set()
    for expected_ordinal, row in enumerate(source_rows, start=1):
        require(
            isinstance(row, dict) and row.get("ordinal") == expected_ordinal,
            "SOURCE_CROSSWALK_ORDINAL_MISMATCH",
            "The source crosswalk ordinals must be the exact contiguous 1..N sequence.",
            status="MISMATCH",
            expected_ordinal=expected_ordinal,
        )
        occurrence = occurrence_by_ordinal[expected_ordinal]
        supplied_name = Path(str(occurrence["supplied_pointer"])).name
        require(
            row.get("name") == supplied_name and supplied_name not in seen_names,
            "SOURCE_CROSSWALK_NAME_MISMATCH",
            "A crosswalk row does not bind the exact registered source name once.",
            status="MISMATCH",
            ordinal=expected_ordinal,
            registered_name=supplied_name,
            crosswalk_name=row.get("name"),
        )
        seen_names.add(supplied_name)
        object_id = str(occurrence["object_id"])
        for claim_key in (
            "reason_for_presence",
            "planned_use",
            "rejected_use",
            "license_state",
            "user_stated_provenance",
        ):
            if claim_key not in row:
                continue
            authority = (
                "USER_STATED"
                if claim_key == "user_stated_provenance"
                else "RESEARCH_ASSESSMENT"
            )
            claim_rows.append(
                (
                    object_id,
                    claim_key,
                    canonical_json_bytes(row[claim_key]).decode("utf-8").strip(),
                    authority,
                )
            )
    crosswalk_sha256 = sha256_file(crosswalk_file)
    assertion_set_id = f"assertions_{crosswalk_sha256[:32].lower()}"
    with _connect(target) as connection:
        existing = connection.execute(
            "SELECT * FROM source_assertion_set WHERE assertion_set_id=?",
            (assertion_set_id,),
        ).fetchone()
        if existing:
            require(
                existing["batch_id"] == batch_id
                and existing["claim_count"] == len(claim_rows),
                "SOURCE_CROSSWALK_ASSERTION_SET_COLLISION",
                "The immutable assertion-set identity resolved to different claims.",
                status="MISMATCH",
            )
            return {
                "status": "PASS",
                "append_status": "IDEMPOTENT_REUSE",
                **dict(existing),
            }
        with connection:
            for object_id, claim_key, claim_json, authority in claim_rows:
                prior = connection.execute(
                    """SELECT claim_json, authority FROM source_provenance
                    WHERE batch_id=? AND object_id=? AND claim_key=?""",
                    (batch_id, object_id, claim_key),
                ).fetchone()
                require(
                    prior is None
                    or (
                        prior["claim_json"] == claim_json
                        and prior["authority"] == authority
                    ),
                    "SOURCE_CROSSWALK_EXISTING_CLAIM_MISMATCH",
                    "A registered source claim cannot be silently replaced.",
                    status="MISMATCH",
                    object_id=object_id,
                    claim_key=claim_key,
                )
                if prior is None:
                    connection.execute(
                        "INSERT INTO source_provenance VALUES (?, ?, ?, ?, ?)",
                        (batch_id, object_id, claim_key, claim_json, authority),
                    )
            connection.execute(
                "INSERT INTO source_assertion_set VALUES (?, ?, ?, ?, ?, ?)",
                (
                    assertion_set_id,
                    batch_id,
                    crosswalk_sha256,
                    len(source_rows),
                    len(claim_rows),
                    utc_now(),
                ),
            )
            event = {
                "schema": "evidence-lane.source-crosswalk.receipt.v1",
                "batch_id": batch_id,
                "assertion_set_id": assertion_set_id,
                "crosswalk_sha256": crosswalk_sha256,
                "source_count": len(source_rows),
                "claim_count": len(claim_rows),
            }
            event_sha = sha256_bytes(canonical_json_bytes(event))
            connection.execute(
                "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"registry_{event_sha[:32].lower()}",
                    batch_id,
                    "source.authority.crosswalk.registered",
                    canonical_json_bytes(event).decode("utf-8").strip(),
                    event_sha,
                    utc_now(),
                ),
            )
    return {
        "status": "PASS",
        "append_status": "APPENDED",
        "assertion_set_id": assertion_set_id,
        "batch_id": batch_id,
        "crosswalk_sha256": crosswalk_sha256,
        "source_count": len(source_rows),
        "claim_count": len(claim_rows),
    }


def verify_source_batch_unchanged(
    registry_path: str | Path,
    batch_id: str,
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-freeze every ordered source and fail closed on any identity change."""

    registered = load_source_batch(registry_path, batch_id)
    comparisons: list[dict[str, Any]] = []
    for occurrence in registered["occurrences"]:
        fresh = freeze_source_authority(
            SourceAuthoritySpec(
                source=str(occurrence["supplied_pointer"]),
                ordinal=int(occurrence["ordinal"]),
                lane_id=str(occurrence["lane_id"]),
            ),
            policy,
        )
        matches = fresh.identity_sha256 == occurrence["identity_sha256"]
        comparisons.append(
            {
                "ordinal": occurrence["ordinal"],
                "object_id": occurrence["object_id"],
                "registered_identity_sha256": occurrence["identity_sha256"],
                "current_identity_sha256": fresh.identity_sha256,
                "matches": matches,
            }
        )
    changed = [row for row in comparisons if not row["matches"]]
    require(
        not changed,
        "SOURCE_AUTHORITY_BATCH_CHANGED",
        "One or more registered source authorities changed after classification.",
        status="STALE",
        batch_id=batch_id,
        changed=changed,
    )
    return {
        "status": "PASS",
        "batch_id": batch_id,
        "batch_sha256": registered["batch_sha256"],
        "source_count": len(comparisons),
        "comparisons": comparisons,
        "source_bytes_mutated": False,
    }


def snapshot_source_authority_registry(
    registry_path: str | Path, batch_id: str | None = None
) -> dict[str, Any]:
    """Return a deterministic read-only registry projection."""

    target = Path(registry_path).resolve()
    if not target.is_file():
        return {
            "schema": REGISTRY_SCHEMA,
            "status": "NOT_INITIALIZED",
            "batch_count": 0,
            "latest_batch": None,
        }
    initialize_source_authority_registry(target)
    with _connect(target) as connection:
        batches = [
            dict(row)
            for row in connection.execute("SELECT * FROM intake_batch ORDER BY rowid")
        ]
        assertion_sets = [
            {key: value for key, value in dict(row).items() if key != "created_at"}
            for row in connection.execute(
                "SELECT * FROM source_assertion_set ORDER BY assertion_set_id"
            )
        ]
        archive_receipts = [
            {
                key: value
                for key, value in dict(row).items()
                if key not in {"created_at", "receipt_json"}
            }
            for row in connection.execute(
                "SELECT * FROM source_archive_receipt "
                "ORDER BY batch_id, archive_object_id"
            )
        ]
        relation_counts = [
            dict(row)
            for row in connection.execute(
                """SELECT batch_id, relation_type, COUNT(*) AS relation_count
                FROM source_relation
                GROUP BY batch_id, relation_type
                ORDER BY batch_id, relation_type"""
            )
        ]
        sqlite_asset_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT object_id, member_path, inspection_state,
                schema_sha256, byte_sha256, size_bytes, canonical_asset_id,
                inspection_receipt_sha256
                FROM source_sqlite_asset ORDER BY object_id, member_path"""
            )
        ]
        sqlite_receipt_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT canonical_asset_id, receipt_sha256
                FROM source_sqlite_receipt ORDER BY canonical_asset_id"""
            )
        ]
        sqlite_projection = {
            "asset_count": len(sqlite_asset_projection),
            "asset_set_sha256": sha256_bytes(
                canonical_json_bytes(sqlite_asset_projection)
            ),
            "receipt_count": len(sqlite_receipt_projection),
            "receipt_set_sha256": sha256_bytes(
                canonical_json_bytes(sqlite_receipt_projection)
            ),
        }
        custom_schema_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT schema_id, schema_version, schema_sha256,
                target_lane_id, compiler_version
                FROM source_custom_schema
                ORDER BY schema_id, schema_version"""
            )
        ]
        custom_mapping_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT mapping_id, schema_sha256, batch_id,
                occurrence_ordinal, object_id, member_path, selector_id,
                target_lane_id, mapping_sha256, mapping_state
                FROM source_custom_schema_mapping
                ORDER BY schema_sha256, batch_id, occurrence_ordinal,
                member_path, selector_id"""
            )
        ]
        custom_receipt_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT receipt_id, schema_sha256, batch_id, receipt_sha256
                FROM source_custom_schema_receipt
                ORDER BY receipt_id"""
            )
        ]
        custom_projection = {
            "schema_count": len(custom_schema_projection),
            "schema_set_sha256": sha256_bytes(
                canonical_json_bytes(custom_schema_projection)
            ),
            "mapping_count": len(custom_mapping_projection),
            "mapping_set_sha256": sha256_bytes(
                canonical_json_bytes(custom_mapping_projection)
            ),
            "receipt_count": len(custom_receipt_projection),
            "receipt_set_sha256": sha256_bytes(
                canonical_json_bytes(custom_receipt_projection)
            ),
        }
        identity_entity_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT entity_id, entity_kind, label, version_label,
                authority, evidence_ref, entity_sha256
                FROM source_identity_entity ORDER BY entity_id"""
            )
        ]
        identity_assertion_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT assertion_id, batch_id, object_id, identity_axis,
                authority, evidence_ref, claim_state, assertion_sha256
                FROM source_identity_assertion
                ORDER BY batch_id, object_id, identity_axis, assertion_id"""
            )
        ]
        identity_relation_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT relation_id, batch_id, subject_type, subject_id,
                relation_type, object_type, object_id, authority, evidence_ref,
                relation_sha256
                FROM source_identity_relation
                ORDER BY batch_id, subject_type, subject_id, relation_type,
                object_type, object_id"""
            )
        ]
        identity_receipt_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT receipt_id, batch_id, matrix_sha256, receipt_sha256
                FROM source_identity_receipt ORDER BY receipt_id"""
            )
        ]
        identity_projection = {
            "entity_count": len(identity_entity_projection),
            "entity_set_sha256": sha256_bytes(
                canonical_json_bytes(identity_entity_projection)
            ),
            "assertion_count": len(identity_assertion_projection),
            "assertion_set_sha256": sha256_bytes(
                canonical_json_bytes(identity_assertion_projection)
            ),
            "relation_count": len(identity_relation_projection),
            "relation_set_sha256": sha256_bytes(
                canonical_json_bytes(identity_relation_projection)
            ),
            "receipt_count": len(identity_receipt_projection),
            "receipt_set_sha256": sha256_bytes(
                canonical_json_bytes(identity_receipt_projection)
            ),
        }
        graph_snapshot_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT graph_id, batch_id, extractor_version,
                selection_sha256, source_count, file_count, parsed_file_count,
                node_count, edge_count, coverage_sha256, graph_root_sha256,
                status, receipt_sha256
                FROM source_graph_snapshot ORDER BY graph_id"""
            )
        ]
        graph_diff_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT diff_id, from_graph_id, to_graph_id,
                projection_sha256, receipt_sha256
                FROM source_graph_diff ORDER BY diff_id"""
            )
        ]
        graph_impact_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT impact_id, graph_id, projection_sha256, receipt_sha256
                FROM source_graph_impact ORDER BY impact_id"""
            )
        ]
        graph_projection = {
            "snapshot_count": len(graph_snapshot_projection),
            "snapshot_set_sha256": sha256_bytes(
                canonical_json_bytes(graph_snapshot_projection)
            ),
            "diff_count": len(graph_diff_projection),
            "diff_set_sha256": sha256_bytes(
                canonical_json_bytes(graph_diff_projection)
            ),
            "impact_count": len(graph_impact_projection),
            "impact_set_sha256": sha256_bytes(
                canonical_json_bytes(graph_impact_projection)
            ),
        }
        git_snapshot_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT snapshot_id, batch_id, occurrence_ordinal, object_id,
                repository_identity_sha256, head_commit_sha, head_tree_sha,
                branch, worktree_clean, worktree_status_sha256,
                history_signature_sha256, ref_count, commit_count,
                parent_edge_count, object_count, tree_entry_count,
                file_change_count, rename_count, hunk_count,
                changed_line_count, history_root_sha256, status,
                receipt_sha256
                FROM source_git_snapshot ORDER BY snapshot_id"""
            )
        ]
        git_impact_projection = [
            dict(row)
            for row in connection.execute(
                """SELECT impact_id, snapshot_id, graph_id, commit_sha,
                parent_ordinal, mapped_path_count, unmapped_path_count,
                projection_sha256, graph_impact_id, receipt_sha256
                FROM source_git_impact ORDER BY impact_id"""
            )
        ]
        git_projection = {
            "snapshot_count": len(git_snapshot_projection),
            "snapshot_set_sha256": sha256_bytes(
                canonical_json_bytes(git_snapshot_projection)
            ),
            "impact_count": len(git_impact_projection),
            "impact_set_sha256": sha256_bytes(
                canonical_json_bytes(git_impact_projection)
            ),
        }
    selected = None
    if batch_id:
        selected = next((row for row in batches if row["batch_id"] == batch_id), None)
        require(
            selected is not None,
            "SOURCE_AUTHORITY_BATCH_MISSING",
            "The requested source authority batch is not registered.",
            status="MISMATCH",
            batch_id=batch_id,
        )
    elif batches:
        selected = batches[-1]
    deterministic = [
        {key: value for key, value in row.items() if key != "created_at"}
        for row in batches
    ]
    deterministic_state = {
        "batches": deterministic,
        "assertion_sets": assertion_sets,
        "archive_receipts": archive_receipts,
        "relation_counts": relation_counts,
        "sqlite_projection": sqlite_projection,
        "custom_projection": custom_projection,
        "identity_projection": identity_projection,
        "graph_projection": graph_projection,
        "git_projection": git_projection,
    }
    return {
        "schema": REGISTRY_SCHEMA,
        "status": "PASS",
        "batch_count": len(batches),
        "registry_root_sha256": sha256_bytes(canonical_json_bytes(deterministic_state)),
        "latest_batch": selected,
        "assertion_set_count": len(assertion_sets),
        "archive_receipt_count": len(archive_receipts),
        "relation_counts": relation_counts,
        "sqlite_projection": sqlite_projection,
        "custom_projection": custom_projection,
        "identity_projection": identity_projection,
        "graph_projection": graph_projection,
        "git_projection": git_projection,
    }

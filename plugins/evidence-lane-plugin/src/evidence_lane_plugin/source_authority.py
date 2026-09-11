"""Deterministic, read-only source authority registry.

The registry records what Source Intake inspected without copying source payloads into
the project store. File content is hashed by streaming reads and approved directory
members are hashed independently. Excluded secret/runtime entries are aggregated by
reason without persisting their paths or traversing excluded runtime subtrees, and
every ordered occurrence is preserved.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import struct
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import EvidenceLaneError, LaneError, require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .lanes import LaneRegistryError, get_lane
from .migrations import Migration, apply_migrations
from .storage import LaneStore, ProjectStore, reject_links
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
    directory_selection: str | None = None
    expected_directory_path_size_sha256: str | None = None


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
    exclusion_summary: tuple[dict[str, Any], ...] = ()
    directory_selection: str | None = None

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
            payload["exclusion_summary"] = list(self.exclusion_summary)
        if self.directory_selection is not None:
            payload['directory_selection'] = self.directory_selection
        return payload


@dataclass
class SourceCaptureBudget:
    """One bounded observation, authorized by its active engine operation.

    This is an internal capture boundary, not a user-supplied callback or a new
    authority. Repeated overlapping paths retain one measured byte identity.
    """
    authorize: Callable[[Path], None]
    check: Callable[[], None]
    max_files: int = 512
    max_file_bytes: int = 1_048_576
    max_total_bytes: int = 33_554_432
    max_entries: int = 20_580
    max_seconds: float = 15
    allow_archives: bool = False
    max_archive_members: int = 512
    max_archive_member_bytes: int = 16_777_216
    max_archive_total_bytes: int = 33_554_432
    archive_observations: dict = field(default_factory=dict)
    identities: dict = field(default_factory=dict)
    entries: int = 0
    started: float = field(default_factory=time.monotonic)

    def boundary(self, path):
        self.check()
        self.authorize(path)
        if time.monotonic() - self.started > self.max_seconds:
            raise LaneError('SOURCE_CAPTURE_TIME_BUDGET', 'The source observation exceeded its elapsed-time budget.')

    def entry(self, path):
        self.boundary(path)
        self.entries += 1
        if self.entries > self.max_entries:
            raise LaneError('SOURCE_CAPTURE_ENTRY_BUDGET', 'Select fewer source directory entries.')

    def file(self, path, size):
        self.boundary(path)
        if path not in self.identities and len(self.identities) >= self.max_files:
            raise LaneError('SOURCE_CAPTURE_FILE_BUDGET', 'Select fewer source files for this refresh.')
        previous = self.identities.get(path, {}).get('size_bytes', 0)
        total = sum(item['size_bytes'] for item in self.identities.values()) - previous + size
        if size > self.max_file_bytes or total > self.max_total_bytes:
            raise LaneError('SOURCE_CAPTURE_BYTE_BUDGET', 'The source refresh exceeds its explicit byte budget.')


    def archive(self, path, infos):
        self.boundary(path)
        if not self.allow_archives:
            raise LaneError('SOURCE_CAPTURE_LOCAL_SCOPE', 'This source observation does not permit archive expansion.')
        observation = {'entries': len(infos), 'expanded_bytes': sum(info.file_size for info in infos)}
        others = [value for key, value in self.archive_observations.items() if key != path]
        if (observation['entries'] + sum(row['entries'] for row in others) > self.max_archive_members
                or any(info.file_size > self.max_archive_member_bytes for info in infos)
                or observation['expanded_bytes'] + sum(row['expanded_bytes'] for row in others) > self.max_archive_total_bytes):
            raise LaneError('SOURCE_CAPTURE_ARCHIVE_BUDGET', 'The complete source observation exceeds its archive entry or expanded-byte bound.')
        self.archive_observations[path] = observation


def _capture_directory_walk(root, capture):
    """Bound a directory listing before materializing it; preserve pruning."""
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        capture.boundary(current)
        if depth > 64:
            raise LaneError('SOURCE_CAPTURE_DEPTH_BUDGET', 'Select a shallower source tree.')
        directories, files = [], []
        with os.scandir(current) as iterator:
            for entry in iterator:
                capture.entry(Path(entry.path))
                (directories if entry.is_dir(follow_symlinks=False) else files).append(entry.name)
        yield str(current), directories, files
        stack.extend((current / name, depth + 1) for name in reversed(directories))


def _source_store(store: ProjectStore | LaneStore) -> LaneStore:
    if isinstance(store, ProjectStore):
        return store.lane('sources')
    if not isinstance(store, LaneStore) or store.lane_id != 'sources':
        raise LaneError('SOURCE_AUTHORITY_STORE_REQUIRED', 'Select the Sources lane of an explicit project store.')
    return store


@contextmanager
def _connect(store: ProjectStore | LaneStore, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    target = _source_store(store)
    with (target.transaction() if write else target.connection(read_only=True)) as connection:
        yield connection


def source_authority_write(function):
    """Keep an entire retained source operation and its evidence in one publication."""
    @wraps(function)
    def mutate(registry_path, *args, writer=None, **kwargs):
        store = _source_store(registry_path)
        with store.project.coordinated_transaction(['sources'], writer=writer):
            initialize_source_authority_registry(store)
            result = function(store, *args, **kwargs)
            if result.get('append_status') != 'IDEMPOTENT_REUSE':
                content_digest = store.put_object(canonical_json_bytes(result))
                store.append_receipt('source_authority_operation', {
                    'operation': function.__name__, 'batch_id': result.get('batch_id'),
                    'content_digest': content_digest, 'content_lane_id': 'sources'})
            return result
    return mutate


SOURCES_MIGRATIONS = (
    Migration('sources', 1, 'Retained source identities, provenance, archives, selected SQLite, graph and Git evidence', (
        """CREATE TABLE IF NOT EXISTS registry_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""",
        """CREATE TABLE IF NOT EXISTS intake_batch(
                batch_id TEXT PRIMARY KEY,
                batch_sha256 TEXT NOT NULL UNIQUE,
                source_count INTEGER NOT NULL,
                directory_count INTEGER NOT NULL,
                file_count INTEGER NOT NULL,
                zip_count INTEGER NOT NULL,
                exact_extracted_zip_relations INTEGER NOT NULL,
                unique_zip_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )""",
        """CREATE TABLE IF NOT EXISTS source_object(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_occurrence(
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                ordinal INTEGER NOT NULL,
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                supplied_pointer TEXT NOT NULL,
                lane_id TEXT NOT NULL,
                PRIMARY KEY(batch_id, ordinal)
            )""",
        """CREATE TABLE IF NOT EXISTS source_member(
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                member_path TEXT NOT NULL,
                member_kind TEXT NOT NULL,
                size_bytes INTEGER,
                sha256 TEXT,
                policy_state TEXT NOT NULL,
                policy_reason TEXT NOT NULL,
                PRIMARY KEY(object_id, member_path)
            )""",
        """CREATE TABLE IF NOT EXISTS source_relation(
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                left_object_id TEXT NOT NULL REFERENCES source_object(object_id),
                relation_type TEXT NOT NULL,
                right_object_id TEXT NOT NULL REFERENCES source_object(object_id),
                receipt_sha256 TEXT NOT NULL,
                PRIMARY KEY(batch_id, left_object_id, relation_type, right_object_id)
            )""",
        """CREATE TABLE IF NOT EXISTS source_policy_receipt(
                object_id TEXT PRIMARY KEY REFERENCES source_object(object_id),
                included_member_count INTEGER NOT NULL,
                excluded_member_count INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL
            )""",
        """CREATE TABLE IF NOT EXISTS source_exclusion_summary(
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                policy_reason TEXT NOT NULL,
                excluded_entry_count INTEGER NOT NULL,
                excluded_bytes INTEGER,
                descendant_members_enumerated INTEGER NOT NULL CHECK(descendant_members_enumerated IN (0, 1)),
                member_paths_stored INTEGER NOT NULL CHECK(member_paths_stored = 0),
                capture_mode TEXT NOT NULL,
                PRIMARY KEY(object_id, policy_reason)
            )""",
        """CREATE TABLE IF NOT EXISTS source_archive_receipt(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_provenance(
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                claim_key TEXT NOT NULL,
                claim_json TEXT NOT NULL,
                authority TEXT NOT NULL,
                PRIMARY KEY(batch_id, object_id, claim_key)
            )""",
        """CREATE TABLE IF NOT EXISTS source_assertion_set(
                assertion_set_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                crosswalk_sha256 TEXT NOT NULL,
                source_count INTEGER NOT NULL,
                claim_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(batch_id, crosswalk_sha256)
            )""",
        """CREATE TABLE IF NOT EXISTS source_sqlite_asset(
                object_id TEXT NOT NULL REFERENCES source_object(object_id),
                member_path TEXT NOT NULL,
                inspection_state TEXT NOT NULL,
                schema_sha256 TEXT,
                PRIMARY KEY(object_id, member_path)
            )""",
        """CREATE TABLE IF NOT EXISTS source_sqlite_schema_object(
                object_id TEXT NOT NULL,
                member_path TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_name TEXT NOT NULL,
                sql_sha256 TEXT,
                PRIMARY KEY(object_id, member_path, object_type, object_name)
            )""",
        """CREATE TABLE IF NOT EXISTS source_sqlite_table_stat(
                object_id TEXT NOT NULL,
                member_path TEXT NOT NULL,
                table_name TEXT NOT NULL,
                row_count INTEGER,
                count_state TEXT NOT NULL,
                PRIMARY KEY(object_id, member_path, table_name)
            )""",
        """CREATE TABLE IF NOT EXISTS source_sqlite_receipt(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_sqlite_foreign_key(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_custom_schema(
                schema_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                schema_sha256 TEXT NOT NULL UNIQUE,
                target_lane_id TEXT NOT NULL,
                compiler_version TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                compiled_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(schema_id, schema_version)
            )""",
        """CREATE TABLE IF NOT EXISTS source_custom_schema_mapping(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_custom_schema_receipt(
                receipt_id TEXT PRIMARY KEY,
                schema_sha256 TEXT NOT NULL REFERENCES source_custom_schema(schema_sha256),
                batch_id TEXT NOT NULL REFERENCES intake_batch(batch_id),
                matched_occurrence_count INTEGER NOT NULL,
                mapping_count INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                UNIQUE(schema_sha256, batch_id)
            )""",
        """CREATE TABLE IF NOT EXISTS source_identity_entity(
                entity_id TEXT PRIMARY KEY,
                entity_kind TEXT NOT NULL,
                label TEXT NOT NULL,
                version_label TEXT NOT NULL,
                authority TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                entity_json TEXT NOT NULL,
                entity_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )""",
        """CREATE TABLE IF NOT EXISTS source_identity_assertion(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_identity_relation(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_identity_receipt(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_graph_snapshot(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_graph_node(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_graph_edge(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_graph_file_coverage(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_graph_diff(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_graph_impact(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_snapshot(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_ref(
                snapshot_id TEXT NOT NULL REFERENCES source_git_snapshot(snapshot_id),
                ref_name TEXT NOT NULL,
                object_sha TEXT NOT NULL,
                object_type TEXT NOT NULL,
                peeled_sha TEXT,
                peeled_type TEXT,
                ref_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, ref_name)
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_commit(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_parent(
                snapshot_id TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                parent_ordinal INTEGER NOT NULL,
                parent_sha TEXT NOT NULL,
                parent_edge_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, commit_sha, parent_ordinal),
                FOREIGN KEY(snapshot_id, commit_sha)
                    REFERENCES source_git_commit(snapshot_id, commit_sha)
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_object(
                snapshot_id TEXT NOT NULL REFERENCES source_git_snapshot(snapshot_id),
                object_sha TEXT NOT NULL,
                object_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                object_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, object_sha)
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_object_path(
                snapshot_id TEXT NOT NULL,
                object_sha TEXT NOT NULL,
                path_ordinal INTEGER NOT NULL,
                observed_path TEXT NOT NULL,
                path_sha256 TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, object_sha, path_ordinal),
                FOREIGN KEY(snapshot_id, object_sha)
                    REFERENCES source_git_object(snapshot_id, object_sha)
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_tree_entry(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_file_change(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_rename(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_hunk(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_changed_line(
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
            )""",
        """CREATE TABLE IF NOT EXISTS source_git_impact(
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
            )""",
        """CREATE TABLE IF NOT EXISTS registry_event(
                event_id TEXT PRIMARY KEY,
                batch_id TEXT,
                event_type TEXT NOT NULL,
                event_json TEXT NOT NULL,
                event_sha256 TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )""",
        """ALTER TABLE source_object ADD COLUMN exclusion_summary_sha256 TEXT""",
        """ALTER TABLE source_sqlite_asset ADD COLUMN byte_sha256 TEXT""",
        """ALTER TABLE source_sqlite_asset ADD COLUMN size_bytes INTEGER""",
        """ALTER TABLE source_sqlite_asset ADD COLUMN canonical_asset_id TEXT""",
        """ALTER TABLE source_sqlite_asset ADD COLUMN inspection_receipt_sha256 TEXT""",
        """CREATE INDEX IF NOT EXISTS source_git_change_path_idx ON source_git_file_change(snapshot_id, member_path, commit_sha)""",
        """CREATE INDEX IF NOT EXISTS source_git_prior_path_idx ON source_git_file_change(snapshot_id, prior_path, commit_sha)""",
        """CREATE INDEX IF NOT EXISTS source_git_tree_path_idx ON source_git_tree_entry(snapshot_id, member_path, commit_sha)""",
        """CREATE VIRTUAL TABLE IF NOT EXISTS source_authority_fts USING fts5(object_id UNINDEXED, source_pointer, member_path)""",
        """INSERT INTO registry_meta(key,value) VALUES('schema','evidence-lane.source-authority-registry.v1')""",
    )),
    Migration('sources', 2, 'Preserve SQLite occurrence history across complete source states and inspection contracts', (
        """CREATE TABLE source_sqlite_asset_previous AS SELECT * FROM source_sqlite_asset""",
        """DROP TABLE source_sqlite_asset""",
        """CREATE TABLE source_sqlite_asset(
            object_id TEXT NOT NULL REFERENCES source_object(object_id), member_path TEXT NOT NULL,
            inspection_state TEXT NOT NULL, schema_sha256 TEXT, byte_sha256 TEXT, size_bytes INTEGER,
            canonical_asset_id TEXT, inspection_receipt_sha256 TEXT, inspection_id TEXT NOT NULL,
            PRIMARY KEY(object_id, member_path, inspection_id))""",
        """INSERT INTO source_sqlite_asset SELECT *, COALESCE(canonical_asset_id, 'legacy')
            FROM source_sqlite_asset_previous""",
        """CREATE TABLE source_sqlite_schema_object_previous AS SELECT * FROM source_sqlite_schema_object""",
        """DROP TABLE source_sqlite_schema_object""",
        """CREATE TABLE source_sqlite_schema_object(
            object_id TEXT NOT NULL, member_path TEXT NOT NULL, object_type TEXT NOT NULL,
            object_name TEXT NOT NULL, sql_sha256 TEXT, inspection_id TEXT NOT NULL,
            PRIMARY KEY(object_id, member_path, inspection_id, object_type, object_name))""",
        """INSERT INTO source_sqlite_schema_object SELECT s.*, COALESCE(a.canonical_asset_id, 'legacy')
            FROM source_sqlite_schema_object_previous s LEFT JOIN source_sqlite_asset_previous a
            ON a.object_id=s.object_id AND a.member_path=s.member_path""",
        """CREATE TABLE source_sqlite_table_stat_previous AS SELECT * FROM source_sqlite_table_stat""",
        """DROP TABLE source_sqlite_table_stat""",
        """CREATE TABLE source_sqlite_table_stat(
            object_id TEXT NOT NULL, member_path TEXT NOT NULL, table_name TEXT NOT NULL,
            row_count INTEGER, count_state TEXT NOT NULL, inspection_id TEXT NOT NULL,
            PRIMARY KEY(object_id, member_path, inspection_id, table_name))""",
        """INSERT INTO source_sqlite_table_stat SELECT s.*, COALESCE(a.canonical_asset_id, 'legacy')
            FROM source_sqlite_table_stat_previous s LEFT JOIN source_sqlite_asset_previous a
            ON a.object_id=s.object_id AND a.member_path=s.member_path""",
        """DROP TABLE source_sqlite_schema_object_previous""",
        """DROP TABLE source_sqlite_table_stat_previous""",
        """DROP TABLE source_sqlite_asset_previous""",
    )),
)


def initialize_source_authority_registry(store: ProjectStore | LaneStore, *, writer=None) -> LaneStore:
    """Apply the retained schema only to the selected Sources authority."""
    target = _source_store(store)
    apply_migrations(target, SOURCES_MIGRATIONS, writer=writer)
    return target


def reconcile_legacy_source_authority_registry(project_root: str | Path) -> dict[str, Any]:
    """The retired implicit move/merge cannot mutate a separate-lane project."""
    raise LaneError('SOURCE_LEGACY_ROUTE_RETIRED', 'Use explicit project storage migration into a fresh state root.')


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


def _stable_file_identity(path: Path, capture: SourceCaptureBudget | None = None) -> tuple[int, str]:
    if capture:
        capture.boundary(path)
    before = path.stat()
    require(
        stat.S_ISREG(before.st_mode),
        "SOURCE_AUTHORITY_NOT_REGULAR_FILE",
        "Only regular file bytes may be hashed as file authority.",
        status="BLOCKED",
        source=str(path),
    )
    if capture is None:
        digest = sha256_file(path)
    else:
        capture.file(path, before.st_size)
        observed, hasher = 0, hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(min(65_536, capture.max_file_bytes - observed + 1)):
                observed += len(chunk)
                capture.file(path, observed)
                hasher.update(chunk)
        digest = hasher.hexdigest().upper()
    after = path.stat()
    require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        and (capture is None or observed == after.st_size),
        "SOURCE_AUTHORITY_CHANGED_DURING_READ",
        "A source changed while its authority identity was being captured.",
        status="STALE",
        source=str(path),
    )
    if capture:
        identity = {'size_bytes': after.st_size, 'sha256': digest.lower()}
        if path in capture.identities and capture.identities[path] != identity:
            raise LaneError('SOURCE_AUTHORITY_CHANGED_DURING_READ', 'Overlapping source observations disagree about a file.')
        capture.identities[path] = identity
    return after.st_size, digest


def _record_exclusion(
    accumulator: dict[str, dict[str, Any]],
    *,
    reason: str,
    size_bytes: int | None,
    descendant_members_enumerated: bool,
) -> None:
    row = accumulator.setdefault(
        reason,
        {
            "policy_reason": reason,
            "excluded_entry_count": 0,
            "excluded_bytes": 0,
            "excluded_bytes_complete": True,
            "descendant_members_enumerated": True,
            "member_paths_stored": False,
        },
    )
    row["excluded_entry_count"] = int(row["excluded_entry_count"]) + 1
    if size_bytes is None:
        row["excluded_bytes_complete"] = False
    else:
        row["excluded_bytes"] = int(row["excluded_bytes"]) + int(size_bytes)
    row["descendant_members_enumerated"] = bool(
        row["descendant_members_enumerated"]
    ) and bool(descendant_members_enumerated)


def _finalize_exclusions(
    accumulator: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for reason in sorted(accumulator):
        source = accumulator[reason]
        rows.append(
            {
                "policy_reason": reason,
                "excluded_entry_count": int(source["excluded_entry_count"]),
                "excluded_bytes": (
                    int(source["excluded_bytes"])
                    if source["excluded_bytes_complete"]
                    else None
                ),
                "descendant_members_enumerated": bool(
                    source["descendant_members_enumerated"]
                ),
                "member_paths_stored": False,
            }
        )
    return tuple(rows)


def _directory_members(
    root: Path, policy: Mapping[str, Any], capture: SourceCaptureBudget | None = None
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    members: list[dict[str, Any]] = []
    exclusions: dict[str, dict[str, Any]] = {}
    max_members = int(policy.get("max_members", 250_000))
    excluded_dirs = {
        str(value).casefold()
        for value in policy.get("excluded_directory_names", _RUNTIME_DIRECTORY_NAMES)
    }
    walk = _capture_directory_walk(root, capture) if capture else os.walk(root, topdown=True, followlinks=False)
    for current_root, directory_names, file_names in walk:
        current = Path(current_root)
        retained_directories: list[str] = []
        for name in sorted(directory_names, key=str.casefold):
            candidate = current / name
            if candidate.is_symlink():
                _record_exclusion(
                    exclusions,
                    reason="SYMLINK_NOT_FOLLOWED",
                    size_bytes=None,
                    descendant_members_enumerated=False,
                )
            elif name.casefold() in excluded_dirs:
                _record_exclusion(
                    exclusions,
                    reason="RUNTIME_DIRECTORY",
                    size_bytes=None,
                    descendant_members_enumerated=False,
                )
            else:
                retained_directories.append(name)
        directory_names[:] = retained_directories

        for name in sorted(file_names, key=str.casefold):
            candidate = current / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                _record_exclusion(
                    exclusions,
                    reason="SYMLINK_NOT_FOLLOWED",
                    size_bytes=None,
                    descendant_members_enumerated=True,
                )
                continue
            policy_state, reason = _policy_reason(relative, policy)
            if policy_state == "INCLUDED":
                size_bytes, digest = _stable_file_identity(candidate, capture)
            else:
                _record_exclusion(
                    exclusions,
                    reason=reason,
                    size_bytes=candidate.stat().st_size,
                    descendant_members_enumerated=True,
                )
                continue
            members.append(
                {
                    "member_path": relative,
                    "member_kind": "file",
                    "size_bytes": size_bytes,
                    "sha256": digest,
                    "policy_state": "INCLUDED",
                    "policy_reason": "POLICY_APPROVED",
                }
            )
        excluded_entries = sum(
            int(row["excluded_entry_count"]) for row in exclusions.values()
        )
        if len(members) + excluded_entries > max_members:
            raise EvidenceLaneError(
                "SOURCE_AUTHORITY_MEMBER_LIMIT_EXCEEDED",
                "A source directory exceeded the governed member limit.",
                status="BLOCKED",
                details={"source": str(root), "max_members": max_members},
            )
    return tuple(members), _finalize_exclusions(exclusions)


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


def _archive_records(stream, start, size, *, max_members, max_entries, check):
    """Walk fixed central-directory records without creating decoded metadata."""
    position, entries, files = start, 0, 0
    while position < start + size:
        check()
        stream.seek(position)
        header = stream.read(46)
        if len(header) != 46 or header[:4] != b'PK\x01\x02':
            raise zipfile.BadZipFile('Invalid central-directory record')
        name_size, extra_size, comment_size = struct.unpack_from('<HHH', header, 28)
        record_size = 46 + name_size + extra_size + comment_size
        if position + record_size > start + size:
            raise zipfile.BadZipFile('Truncated central-directory record')
        entries += 1
        require(entries <= max_entries, 'SOURCE_ARCHIVE_METADATA_ENTRY_BUDGET',
            'The archive exceeds its metadata-entry budget.', status='BLOCKED')
        stream.seek(position + 46 + max(0, name_size - 1))
        last_name_byte = stream.read(1) if name_size else b''
        files += last_name_byte not in {b'/', b'\\'}
        require(files <= max_members, 'SOURCE_AUTHORITY_ARCHIVE_MEMBER_LIMIT_EXCEEDED',
            'The archive exceeds its file-member budget.', status='BLOCKED')
        position += record_size
    if position != start + size:
        raise zipfile.BadZipFile('Invalid central-directory size')
    return entries


def _archive_metadata(stream, *, max_members, max_entries, max_directory_bytes, check):
    """Bound ZIP/ZIP64 directory bytes and records before ZipInfo allocation.

    Record layouts follow PKWARE APPNOTE 6.3.10. This is metadata preflight;
    the standard-library parser still owns decoding and decompression.
    """
    stream.seek(0, 2)
    size = stream.tell()
    tail_start = max(0, size - 65_557)
    stream.seek(tail_start)
    tail = stream.read(65_557)
    caches = [(tail_start, tail)]
    found = tail.rfind(b'PK\x05\x06')
    if found < 0 or len(tail) - found < 22:
        raise zipfile.BadZipFile('ZIP end record not found')
    end = struct.unpack_from('<4s4H2IH', tail, found)
    _, disk, directory_disk, disk_entries, entries, directory_size, directory_offset, comment_size = end
    if len(tail) - found < 22 + comment_size:
        raise zipfile.BadZipFile('Truncated ZIP comment')
    directory_end = tail_start + found
    if disk or directory_disk or disk_entries != entries:
        raise zipfile.BadZipFile('Multipart or inconsistent ZIP directory')
    locator_position = directory_end - 20
    if locator_position >= 0:
        stream.seek(locator_position)
        locator = stream.read(20)
        if locator[:4] == b'PK\x06\x07':
            _, locator_disk, relative_record, disks = struct.unpack('<4sIQI', locator)
            if locator_disk or disks != 1:
                raise zipfile.BadZipFile('Multipart ZIP64 is unsupported')
            record = b''
            record_position = 0
            for candidate in dict.fromkeys((relative_record, locator_position - 56)):
                if not 0 <= candidate <= locator_position - 56:
                    continue
                stream.seek(candidate)
                observed = stream.read(56)
                caches.append((candidate, observed))
                if observed[:4] == b'PK\x06\x06':
                    record, record_position = observed, candidate
                    break
            if len(record) != 56:
                raise zipfile.BadZipFile('ZIP64 end record not found')
            _, record_size, _, _, disk, directory_disk, disk_entries, entries, directory_size, directory_offset = struct.unpack('<4sQ2H2I4Q', record)
            if (disk or directory_disk or disk_entries != entries
                    or record_size + 12 != locator_position - record_position
                    or directory_offset + directory_size != relative_record):
                raise zipfile.BadZipFile('Inconsistent ZIP64 end record')
            require(record_size <= max_directory_bytes, 'SOURCE_ARCHIVE_DIRECTORY_BYTE_BUDGET',
                'The ZIP64 metadata exceeds its byte budget.', status='BLOCKED')
            directory_end = record_position
    require(directory_size <= max_directory_bytes, 'SOURCE_ARCHIVE_DIRECTORY_BYTE_BUDGET',
        'The archive directory exceeds its byte budget.', status='BLOCKED')
    require(entries <= max_entries, 'SOURCE_ARCHIVE_METADATA_ENTRY_BUDGET',
        'The archive exceeds its metadata-entry budget.', status='BLOCKED')
    directory_start = directory_end - directory_size
    if directory_start < 0 or directory_offset > directory_start:
        raise zipfile.BadZipFile('Invalid central-directory offset')
    actual = _archive_records(stream, directory_start, directory_size,
        max_members=max_members, max_entries=max_entries, check=check)
    if actual != entries:
        raise zipfile.BadZipFile('Directory entry count disagrees with its end record')
    stream.seek(directory_start)
    directory = stream.read(directory_size)
    if len(directory) != directory_size:
        raise zipfile.BadZipFile('Truncated archive directory')
    # Recheck the bounded bytes actually supplied to the native parser. Source
    # changes between the streaming walk and capture cannot bypass the count.
    actual = _archive_records(io.BytesIO(directory), 0, len(directory),
        max_members=max_members, max_entries=max_entries, check=check)
    if actual != entries:
        raise zipfile.BadZipFile('Archive directory changed during preflight')
    caches.append((directory_start, directory))
    for index, (start, data) in enumerate(caches):
        for other_start, other_data in caches[index + 1:]:
            left, right = max(start, other_start), min(start + len(data), other_start + len(other_data))
            if left < right and data[left-start:right-start] != other_data[left-other_start:right-other_start]:
                raise EvidenceLaneError('SOURCE_ARCHIVE_CHANGED_DURING_READ',
                    'Archive metadata changed during preflight.', status='STALE')
    return size, caches


class _ArchiveMetadataView:
    """Seekable source view with immutable preflighted ZIP metadata regions."""
    def __init__(self, stream, size, caches):
        self.stream, self.size, self.caches, self.position = stream, size, caches, 0

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        if whence not in {0, 1, 2}:
            raise ValueError('Invalid seek origin')
        position = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        if position < 0:
            raise OSError('Negative archive seek')
        self.position = position
        return position

    def read(self, size=-1):
        if size < 0:
            size = max(0, self.size - self.position)
        # Prefer a region containing the entire read (e.g. a directory that
        # overlaps the bounded footer cache), avoiding artificial truncation.
        matches = [(start, data) for start, data in self.caches
            if start <= self.position < start + len(data)]
        if matches:
            start, data = max(matches, key=lambda row: row[0] + len(row[1]))
            value = data[self.position-start:self.position-start+size]
        else:
            next_cache = min((start for start, _ in self.caches if start > self.position), default=self.size)
            self.stream.seek(self.position)
            value = self.stream.read(max(0, min(size, 1_048_576, next_cache - self.position)))
        self.position += len(value)
        return value


@contextmanager
def _open_source_archive(path: Path, *, max_members: int = 250_000,
        max_entries: int | None = None, max_directory_bytes: int = 64 * 1024 * 1024,
        content: bytes | None = None, expected_sha256: str | None = None,
        expected_size: int | None = None, capture: SourceCaptureBudget | None = None) -> Iterator[zipfile.ZipFile]:
    """Read through one bounded metadata view; never extract source payloads."""
    require(type(max_members) is int and 0 <= max_members <= 250_000,
        'SOURCE_ARCHIVE_BUDGET_INVALID', 'The archive file-member budget is invalid.', status='BLOCKED')
    if max_entries is None:
        max_entries = max(1024, 2 * max_members)
    require(type(max_members) is int and 0 <= max_members <= 250_000
        and type(max_entries) is int and 0 <= max_entries <= 500_000
        and type(max_directory_bytes) is int and 1 <= max_directory_bytes <= 64 * 1024 * 1024,
        'SOURCE_ARCHIVE_BUDGET_INVALID', 'Archive metadata budgets are invalid.', status='BLOCKED')
    deadline = time.monotonic() + 15
    def check():
        require(time.monotonic() < deadline, 'SOURCE_ARCHIVE_TIME_BUDGET',
            'The archive read exceeded its elapsed-time budget.', status='BLOCKED')
        if capture:
            capture.boundary(path)
    def identity(stream):
        check()
        stream.seek(0)
        hasher = hashlib.sha256()
        observed = 0
        while block := stream.read(1_048_576):
            check()
            observed += len(block)
            require(observed <= 4 * 1024**3, 'SOURCE_ARCHIVE_CONTAINER_BYTE_BUDGET',
                'The archive container exceeds its byte budget.', status='BLOCKED')
            hasher.update(block)
        return observed, hasher.hexdigest()
    check()
    source = path.expanduser().absolute()
    stream: Any
    before = opened = None
    if content is not None:
        require(type(content) is bytes and len(content) <= 4 * 1024**3,
            'SOURCE_ARCHIVE_CONTAINER_BYTE_BUDGET', 'The archive byte image exceeds its budget.', status='BLOCKED')
        stream = io.BytesIO(content)
    else:
        reject_links(source, Path(source.anchor))
        before = source.stat()
        require(stat.S_ISREG(before.st_mode) and before.st_size <= 4 * 1024**3,
            'SOURCE_ARCHIVE_CONTAINER_BYTE_BUDGET', 'Select a bounded regular archive file.', status='BLOCKED')
        descriptor = os.open(source, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        stream = os.fdopen(descriptor, 'rb')
        opened = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
    try:
        if before is not None:
            require(all(getattr(before, name) == getattr(opened, name) for name in fields),
                'SOURCE_ARCHIVE_CHANGED_DURING_READ', 'The opened archive identity changed.', status='STALE')
        if expected_sha256 is not None:
            measured_size, measured_sha = identity(stream)
            require(measured_sha == expected_sha256.lower() and (expected_size is None or expected_size == measured_size),
                'SOURCE_SQLITE_ARCHIVE_CONTAINER_CHANGED', 'The registered archive container changed.', status='STALE')
        size, caches = _archive_metadata(stream, max_members=max_members, max_entries=max_entries,
            max_directory_bytes=max_directory_bytes, check=check)
        with zipfile.ZipFile(_ArchiveMetadataView(stream, size, caches)) as archive:
            yield archive
        check()
        if expected_sha256 is not None:
            measured_size, measured_sha = identity(stream)
            require(measured_sha == expected_sha256.lower() and (expected_size is None or expected_size == measured_size),
                'SOURCE_SQLITE_ARCHIVE_CONTAINER_CHANGED', 'The registered archive container changed during inspection.', status='STALE')
        if before is not None:
            reject_links(source, Path(source.anchor))
            after, ended = source.stat(), os.fstat(stream.fileno())
            require(all(getattr(before, name) == getattr(after, name) for name in (*fields, 'st_ctime_ns'))
                and all(getattr(opened, name) == getattr(ended, name) for name in (*fields, 'st_ctime_ns')),
                'SOURCE_ARCHIVE_CHANGED_DURING_READ', 'The archive changed during inspection.', status='STALE')
    finally:
        stream.close()


def archive_safety_profile(
    archive_path: str | Path, policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Inspect ZIP central-directory safety without extraction or source mutation."""

    exact_policy = policy or {}
    target = Path(archive_path).expanduser().absolute()
    max_members = int(exact_policy.get("max_members", 250_000))
    max_member_bytes = int(exact_policy.get("max_archive_member_bytes", 1024**3))
    max_total_bytes = int(exact_policy.get("max_archive_total_bytes", 4 * 1024**3))
    max_compression_ratio = float(
        exact_policy.get("max_archive_compression_ratio", 1000.0)
    )
    try:
        with _open_source_archive(target, max_members=max_members,
                max_directory_bytes=exact_policy.get('max_archive_directory_bytes', 64 * 1024 * 1024)) as archive:
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
    except EvidenceLaneError as error:
        return {'schema': 'evidence-lane.archive-safety-profile.v1', 'status': error.status,
            'code': error.code, 'max_members': max_members,
            'source_bytes_mutated': False, 'source_payloads_extracted': False}
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
    archive_path: Path, policy: Mapping[str, Any], *, capture: SourceCaptureBudget | None = None,
    content: bytes | None = None, expected_container_sha256: str | None = None,
) -> tuple[dict[str, Any], ...]:
    members: list[dict[str, Any]] = []
    max_members = int(policy.get("max_members", 250_000))
    max_member_bytes = int(policy.get("max_archive_member_bytes", 1024**3))
    max_total_bytes = int(policy.get("max_archive_total_bytes", 4 * 1024**3))
    max_compression_ratio = float(policy.get("max_archive_compression_ratio", 1000.0))
    if capture:
        capture.boundary(archive_path)
        if content is not None and len(content) > capture.max_file_bytes:
            raise LaneError('SOURCE_CAPTURE_BYTE_BUDGET', 'The proposed archive exceeds the source file bound.')
    try:
        remaining_entries = (capture.max_archive_members - sum(value['entries'] for key, value
            in capture.archive_observations.items() if key != archive_path)) if capture else None
        with _open_source_archive(archive_path, max_members=max_members, max_entries=remaining_entries,
                max_directory_bytes=policy.get('max_archive_directory_bytes', 64 * 1024 * 1024),
                content=content, capture=capture, expected_sha256=expected_container_sha256) as archive:
            if capture:
                capture.archive(archive_path, archive.infolist())
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
                if capture:
                    capture.boundary(archive_path)
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
                    observed = 0
                    with archive.open(info, "r") as handle:
                        while block := handle.read(65_536 if capture else 1024 * 1024):
                            observed += len(block)
                            if capture:
                                capture.boundary(archive_path)
                                if observed > info.file_size or observed > capture.max_archive_member_bytes:
                                    raise LaneError('SOURCE_CAPTURE_ARCHIVE_BUDGET', 'An archive member exceeded its declared bounded size.')
                            hasher.update(block)
                    if capture and observed != info.file_size:
                        raise LaneError('SOURCE_CAPTURE_ARCHIVE_BUDGET', 'An archive member differs from its declared size.')
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
    except EvidenceLaneError as error:
        if capture and error.code in {'SOURCE_ARCHIVE_METADATA_ENTRY_BUDGET', 'SOURCE_ARCHIVE_DIRECTORY_BYTE_BUDGET'}:
            raise LaneError('SOURCE_CAPTURE_ARCHIVE_BUDGET', 'The source archive exceeded its bounded metadata budget.') from error
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
        raise EvidenceLaneError(
            "SOURCE_AUTHORITY_ARCHIVE_UNREADABLE",
            "An archive could not be indexed safely.",
            status="BLOCKED",
            details={"source": str(archive_path), "error": str(error)},
        ) from error
    return tuple(members)


def _partition_archive_members(
    members: Iterable[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    included: list[dict[str, Any]] = []
    exclusions: dict[str, dict[str, Any]] = {}
    for member in members:
        if member.get("policy_state") == "INCLUDED":
            included.append(dict(member))
            continue
        _record_exclusion(
            exclusions,
            reason=str(member.get("policy_reason") or "POLICY_EXCLUDED"),
            size_bytes=(
                int(member["size_bytes"])
                if member.get("size_bytes") is not None
                else None
            ),
            descendant_members_enumerated=True,
        )
    return tuple(included), _finalize_exclusions(exclusions)


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
    spec: SourceAuthoritySpec, policy: Mapping[str, Any] | None = None,
    *, capture: SourceCaptureBudget | None = None,
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
    if capture:
        # Archive expansion is an explicit internal capture mode. Embedded
        # members remain provenance; they never become filesystem reads.
        capture.boundary(path)
        if (not path.is_absolute() or not path.exists()
                or path.suffix.casefold() == '.zip' and not capture.allow_archives):
            raise LaneError('SOURCE_CAPTURE_LOCAL_SCOPE', 'This automatic refresh requires existing local files and directories, without archive expansion.')
    if path.exists() and path.is_file():
        size_bytes, byte_sha256 = _stable_file_identity(path, capture)
        kind = "zip" if path.suffix.casefold() == ".zip" else "file"
        before_archive_scan = path.stat() if kind == "zip" else None
        raw_members = _archive_members(path, exact_policy, capture=capture,
            expected_container_sha256=byte_sha256) if kind == "zip" else ()
        members, exclusion_summary = (
            _partition_archive_members(raw_members) if raw_members else ((), ())
        )
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
        # Rehash after member reads: path metadata alone cannot bind the
        # byte identity of a concurrently replaced same-sized archive.
        if capture and kind == "zip" and _stable_file_identity(path, capture) != (size_bytes, byte_sha256):
            raise LaneError('SOURCE_AUTHORITY_CHANGED_DURING_READ', 'The archive changed during member capture.')
        merkle = _member_merkle(members) if members else None
        path_size_identity = _member_path_size_identity(members) if members else None
        resolved = str(path.resolve())
    elif path.exists() and path.is_dir():
        kind = "directory"
        if spec.directory_selection is None:
            members, exclusion_summary = _directory_members(path, exact_policy, capture)
        else:
            from .source_selection import capture_selected_directory
            members, exclusion_summary = capture_selected_directory(path, spec, exact_policy, capture)
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
        exclusion_summary = ()
        merkle = None
        path_size_identity = None
        size_bytes = None
        byte_sha256 = None
        resolved = source
    included = len(members)
    excluded = sum(
        int(row["excluded_entry_count"]) for row in exclusion_summary
    )
    exclusion_summary_sha256 = (
        sha256_bytes(canonical_json_bytes(list(exclusion_summary)))
        if exclusion_summary
        else None
    )
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
        "exclusion_summary_sha256": exclusion_summary_sha256,
    }
    if spec.directory_selection is not None:
        if kind != 'directory':
            raise LaneError('SOURCE_SELECTION_CHANGED', 'The registered directory no longer exists as a directory.')
        identity_body['directory_selection'] = spec.directory_selection
        identity_body['directory_selection_version'] = 4
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
        exclusion_summary=exclusion_summary,
        directory_selection=spec.directory_selection,
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
                str(exclusion["policy_reason"])
                for exclusion in archive.exclusion_summary
                if exclusion["policy_reason"] in _UNSAFE_ARCHIVE_REASONS
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


@source_authority_write
def register_source_batch(
    registry_path: ProjectStore | LaneStore,
    specs: Iterable[SourceAuthoritySpec],
    *,
    policy: Mapping[str, Any] | None = None,
    capture: SourceCaptureBudget | None = None,
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
    for spec in exact_specs:
        try:
            lane = get_lane(spec.lane_id)
        except LaneRegistryError:
            raise LaneError('SOURCE_SECTOR_REQUIRED', 'Select a retained canonical sector lane.') from None
        if lane.kind != 'sector' or lane.canonical_lane_id != spec.lane_id:
            raise LaneError('SOURCE_SECTOR_REQUIRED', 'Bind each source to its explicit canonical sector lane.')
    frozen = [freeze_source_authority(spec, policy, capture=capture) for spec in exact_specs]
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
    with _connect(target, write=True) as connection:
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
        with target.transaction():
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
                        member_path_size_sha256, content_merkle_sha256,
                        exclusion_summary_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        (
                            sha256_bytes(
                                canonical_json_bytes(list(source.exclusion_summary))
                            )
                            if source.exclusion_summary
                            else None
                        ),
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
                    require(
                        member["policy_state"] == "INCLUDED"
                        and bool(member.get("sha256")),
                        "SOURCE_AUTHORITY_EXCLUDED_MEMBER_PERSISTENCE_FORBIDDEN",
                        "Excluded source members must be aggregated without paths.",
                        status="FAIL",
                    )
                    inserted = connection.execute(
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
                    if inserted.rowcount:
                        connection.execute(
                            "INSERT INTO source_authority_fts(object_id, source_pointer, member_path) VALUES (?, ?, ?)",
                            (source.object_id, source.source, member["member_path"]),
                        )
                for exclusion in source.exclusion_summary:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO source_exclusion_summary(
                            object_id,policy_reason,excluded_entry_count,
                            excluded_bytes,descendant_members_enumerated,
                            member_paths_stored,capture_mode
                        ) VALUES (?, ?, ?, ?, ?, 0, 'AGGREGATE_NO_PATHS')
                        """,
                        (
                            source.object_id,
                            exclusion["policy_reason"],
                            exclusion["excluded_entry_count"],
                            exclusion["excluded_bytes"],
                            int(bool(exclusion["descendant_members_enumerated"])),
                        ),
                    )
                policy_receipt = {
                    "schema": "evidence-lane.source-policy.receipt.v1",
                    "object_id": source.object_id,
                    "included_member_count": source.included_member_count,
                    "excluded_member_count": source.excluded_member_count,
                    "excluded_content_stored": False,
                    "excluded_member_paths_stored": False,
                    "exclusion_summary": list(source.exclusion_summary),
                }
                if source.directory_selection is not None:
                    policy_receipt.update(directory_selection=source.directory_selection,
                        directory_selection_version=4, excluded_inventory_scope='observed_candidates_only')
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
    registry_path: ProjectStore | LaneStore, batch_id: str
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
            exclusion_summary = tuple(
                {
                    "policy_reason": str(summary["policy_reason"]),
                    "excluded_entry_count": int(summary["excluded_entry_count"]),
                    "excluded_bytes": summary["excluded_bytes"],
                    "descendant_members_enumerated": bool(
                        summary["descendant_members_enumerated"]
                    ),
                    "member_paths_stored": False,
                }
                for summary in connection.execute(
                    "SELECT * FROM source_exclusion_summary WHERE object_id=? "
                    "ORDER BY policy_reason",
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
                    exclusion_summary=exclusion_summary,
                )
            )
    return frozen


@source_authority_write
def reconcile_archive_counterparts(
    registry_path: ProjectStore | LaneStore, batch_id: str
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
            str(row["policy_reason"])
            for row in archive.exclusion_summary
            if row["policy_reason"] in _UNSAFE_ARCHIVE_REASONS
        )
        unsafe_member_count = sum(
            int(row["excluded_entry_count"])
            for row in archive.exclusion_summary
            if row["policy_reason"] in _UNSAFE_ARCHIVE_REASONS
        )
        policy_excluded_member_count = sum(
            int(row["excluded_entry_count"])
            for row in archive.exclusion_summary
            if row["policy_reason"] not in _UNSAFE_ARCHIVE_REASONS
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
    with _connect(target, write=True) as connection:
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


def load_source_batch(registry_path: ProjectStore | LaneStore, batch_id: str) -> dict[str, Any]:
    target = _source_store(registry_path)
    require(
        target.database.is_file(),
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


@source_authority_write
def register_source_crosswalk(
    registry_path: ProjectStore | LaneStore,
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
    with _connect(target, write=True) as connection:
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
        with target.transaction():
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
    registry_path: ProjectStore | LaneStore,
    batch_id: str,
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-freeze every ordered source and fail closed on any identity change."""

    registered = load_source_batch(registry_path, batch_id)
    comparisons: list[dict[str, Any]] = []
    for occurrence in registered["occurrences"]:
        from .source_selection import registered_directory_selection
        fresh = freeze_source_authority(
            SourceAuthoritySpec(
                source=str(occurrence["supplied_pointer"]),
                ordinal=int(occurrence["ordinal"]),
                lane_id=str(occurrence["lane_id"]),
                directory_selection=registered_directory_selection(registry_path, occurrence['object_id']),
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
    registry_path: ProjectStore | LaneStore, batch_id: str | None = None
) -> dict[str, Any]:
    """Return a deterministic read-only registry projection."""

    target = _source_store(registry_path)
    if not target.database.is_file():
        return {
            "schema": REGISTRY_SCHEMA,
            "status": "NOT_INITIALIZED",
            "batch_count": 0,
            "latest_batch": None,
        }
    with _connect(target) as connection:
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='intake_batch'").fetchone() is None:
            return {'schema': REGISTRY_SCHEMA, 'status': 'NOT_INITIALIZED', 'batch_count': 0, 'latest_batch': None}
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
                inspection_receipt_sha256, inspection_id
                FROM source_sqlite_asset ORDER BY object_id, member_path, inspection_id"""
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

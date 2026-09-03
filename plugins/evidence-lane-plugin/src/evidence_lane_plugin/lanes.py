"""One immutable V1/V3-fused registry for all Evidence Lane sectors."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .package_root import resolve_plugin_root

MUTATION_AUTOMATIC_APPEND_ONLY = "automatic_append_only"
MUTATION_NAMED_GRANT_RELOCK = "explicit_named_one_turn_grant_receipt_snapshot_relock"
PRIMARY_CODE_LANES = frozenset({"github_code", "local_code"})
LANE_SCHEMA_REGISTRY_SCHEMA = "evidence-lane.lane-schema-registry.v1"
def _package_schema_root() -> Path:
    return resolve_plugin_root(__file__) / "schemas"


_PACKAGE_SCHEMA_ROOT = _package_schema_root()
LANE_SCHEMA_REGISTRY_PATH = _PACKAGE_SCHEMA_ROOT / "lane-schema-registry.v001.json"
LANE_SCHEMA_EVOLUTION_POLICY_SCHEMA = (
    "evidence-lane.lane-schema-evolution-policy.v1"
)
LANE_SCHEMA_EVOLUTION_POLICY_PATH = (
    _PACKAGE_SCHEMA_ROOT / "lane-schema-evolution.v001.json"
)
LANE_ARTIFACT_ROLE_REGISTRY_SCHEMA = (
    "evidence-lane.lane-artifact-role-registry.v1"
)
LANE_ARTIFACT_ROLE_REGISTRY_PATH = (
    _PACKAGE_SCHEMA_ROOT / "lane-artifact-contract.v001.json"
)
_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_SAFE_SCHEMA_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Exact SHA-256 of the authorized SQLite Brain Builder
# ``backend/src/sqlite_brain_builder/mmd/generate_lane_mmd.py`` supplied for
# this contract.  The runtime does not depend on that external workstation
# path; it carries the fingerprint so receipts can prove which logical
# projection was implemented.
SQLITE_BRAIN_BUILDER_MMD_AUTHORITY_SHA256 = (
    "1B87064906E8A805C4A69A7A3A14668DCCE963E00928ED3EB23CC186AB8A65EC"
)

# Exact SHA-256 of the supplied end-to-end master topology reference
# ``project/topology/project_master_topology.mmd``.  As above, the runtime
# carries only the immutable identity and independently emits its own graph;
# it never reads or copies the external workstation file at build time.
SQLITE_BRAIN_BUILDER_MASTER_TOPOLOGY_AUTHORITY_SHA256 = (
    "E9E610D982B5E855A54C39B7A16E06C6FD4D28A2538C8790CC2B9E34C9FECA01"
)

# Logical projection inherited from the authorized SQLite brain builder.  The
# current lane database intentionally keeps its richer physical schema; both
# code modes must still expose this exact seven-entity contract in MMD and DOT.
CODE_LOGICAL_TOPOLOGY = (
    ("code_repo", "Repo", "lane_meta"),
    ("git_commit", "Commit", "git_commit_registry"),
    ("code_file", "File", "source_registry"),
    ("code_symbol", "Symbol", "code_symbol"),
    ("app_route", "Route", "code_route"),
    ("dependency_item", "Dependency", "code_dependency"),
    ("project_artifact", "Artifact", "structured_fact"),
)


class LaneRegistryError(ValueError):
    """The canonical lane registry or a requested alias is invalid."""


@dataclass(frozen=True, slots=True)
class LaneDefinition:
    canonical_lane_id: str
    display_label: str
    command: str
    aliases: tuple[str, ...]
    source_types: tuple[str, ...]
    extensions: tuple[str, ...]
    parser_id: str
    chunker_version: str
    fts_table: str
    schema_contract: tuple[str, ...]
    mutation_policy: str = MUTATION_NAMED_GRANT_RELOCK

    @property
    def sqlite_filename(self) -> str:
        return f"{self.canonical_lane_id}_sector_v001.sqlite"

    @property
    def mmd_filename(self) -> str:
        return f"{self.canonical_lane_id}.mmd"

    @property
    def dot_filename(self) -> str:
        return f"{self.canonical_lane_id}.dot"

    @property
    def mmd_node_id(self) -> str:
        return f"lane_{self.canonical_lane_id}"

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        schema = lane_schema_asset(self.canonical_lane_id)
        payload.update(
            {
                "sqlite_filename": self.sqlite_filename,
                "mmd_filename": self.mmd_filename,
                "dot_filename": self.dot_filename,
                "mmd_node_id": self.mmd_node_id,
                "lane_schema_id": schema["schema_id"],
                "lane_schema_version": schema["schema_version"],
                "lane_schema_contract_sha256": schema["contract_sha256"],
                "lane_schema_registry_sha256": LANE_SCHEMA_REGISTRY_SHA256,
                "extension_namespace": schema["extension_namespace"],
                "migration_head": schema["migration_ledger"][-1][
                    "migration_id"
                ],
                "lane_schema_evolution_policy_sha256": (
                    LANE_SCHEMA_EVOLUTION_POLICY_SHA256
                ),
                "lane_schema_evolution_user_gate": (
                    self.canonical_lane_id in PRIMARY_CODE_LANES
                ),
                "lane_artifact_contract_sha256": lane_artifact_contract(
                    self.canonical_lane_id
                )["contract_sha256"],
                "lane_artifact_registry_sha256": (
                    LANE_ARTIFACT_ROLE_REGISTRY_SHA256
                ),
            }
        )
        return payload


_CORE_SCHEMA = (
    "lane_meta",
    "lane_pointer",
    "source_content_cas",
    "source_registry",
    "chunk_index",
    "chunk_content_cas",
    "chunk_history",
    "structured_fact",
    "parser_capability",
    "tool_route_contract",
    "tool_execution_receipt",
    "tfidf_term",
    "tfidf_vector",
    "refresh_receipt",
    "mutation_receipt",
    "authority_index_content_cas",
    "authority_index_source",
    "authority_index_node",
    "authority_index_fts",
    "authority_index_refresh_receipt",
)
CORE_SCHEMA_TABLES = frozenset(_CORE_SCHEMA)

_CODE_SCHEMA = _CORE_SCHEMA + (
    "code_symbol",
    "code_import",
    "code_call",
    "code_parser_receipt",
    "code_parser_diagnostic",
    "code_route",
    "code_dependency",
    "git_commit_registry",
    "git_commit_parent",
    "git_file_change",
    "git_ref_registry",
    "git_blob_cas",
    "git_content_chunk_cas",
    "git_chunk_occurrence",
    "git_history_fts",
    "code_chunk_fts",
)

_CODE_EXTENSIONS = (
    ".bat",
    ".c",
    ".cfg",
    ".cjs",
    ".cmd",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".mjs",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".yaml",
    ".yml",
)
_CODE_MANIFEST_NAMES = frozenset(
    {
        "cargo.lock",
        "composer.json",
        "go.mod",
        "go.sum",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "requirements.in",
        "requirements.txt",
        "uv.lock",
        "yarn.lock",
    }
)


def _lane(
    lane_id: str,
    label: str,
    command: str,
    *,
    aliases: tuple[str, ...],
    source_types: tuple[str, ...],
    extensions: tuple[str, ...],
    parser_id: str,
    chunker: str,
    fts_table: str,
    schema: tuple[str, ...] = _CORE_SCHEMA,
    mutation_policy: str = MUTATION_NAMED_GRANT_RELOCK,
) -> LaneDefinition:
    return LaneDefinition(
        canonical_lane_id=lane_id,
        display_label=label,
        command=command,
        aliases=(lane_id, label, command, *aliases),
        source_types=source_types,
        extensions=extensions,
        parser_id=parser_id,
        chunker_version=chunker,
        fts_table=fts_table,
        schema_contract=schema,
        mutation_policy=mutation_policy,
    )


_DEFINITIONS = (
    _lane(
        "github_code",
        "GitHub Code",
        "evi-source-intake --lane github_code",
        aliases=("code", "git", "github", "github repository", "git remote"),
        source_types=("github_repository", "git_remote"),
        extensions=_CODE_EXTENSIONS,
        parser_id="github_code_reverse_history_v2",
        chunker="tiered_git_history_v2_incremental",
        fts_table="code_chunk_fts",
        schema=_CODE_SCHEMA,
    ),
    _lane(
        "local_code",
        "Local Code",
        "evi-source-intake --lane local_code",
        aliases=("code", "local", "code folder", "local git worktree"),
        source_types=("local_code_folder", "local_git_worktree"),
        extensions=_CODE_EXTENSIONS,
        parser_id="local_code_snapshot_v2",
        chunker="tiered_git_history_v2_incremental",
        fts_table="code_chunk_fts",
        schema=_CODE_SCHEMA,
    ),
    _lane(
        "chat_lineage",
        "Chat Lineage",
        "evi-source-intake --lane chat_lineage",
        aliases=("chat-lineage", "chat", "chat history", "conversation lineage"),
        source_types=("chat_export", "prompt_response_packet", "lineage_append_packet"),
        extensions=(".docx", ".json", ".jsonl", ".md", ".txt", ".zip"),
        parser_id="chat_lineage_state_travel_v57",
        chunker="prepare_response_commit_v1",
        fts_table="turn_fts",
        schema=_CORE_SCHEMA
        + (
            "writeback_policy",
            "turn_prepare",
            "prompt_raw_exact",
            "prompt_normalized_summary",
            "response_raw_visible_exact",
            "response_summary",
            "visible_reasoning_summary",
            "file_link_registry",
            "source_normalization_receipt",
            "mode_classification_run",
            "gate_evaluation_run",
            "operator_activation_run",
            "entry_exit_receipt",
            "legacy_project_carry_forward",
            "turn_commit",
            "lineage_head",
            "state_hash_chain",
            "turn_fts",
        ),
        mutation_policy=MUTATION_AUTOMATIC_APPEND_ONLY,
    ),
    _lane(
        "discussion",
        "Discussion",
        "evi-source-intake --lane discussion",
        aliases=("discussion notes", "meeting notes"),
        source_types=("discussion_document", "meeting_notes", "conversation_export"),
        extensions=(".docx", ".md", ".pdf", ".txt"),
        parser_id="discussion_structured_v1",
        chunker="semantic_blocks_v1",
        fts_table="discussion_fts",
        schema=_CORE_SCHEMA
        + (
            "discussion_source",
            "discussion_turn",
            "discussion_item",
            "discussion_decision",
            "discussion_delta",
            "discussion_next_action",
            "discussion_hard_gate",
            "discussion_artifact_reference",
            "discussion_fts",
        ),
    ),
    _lane(
        "analysis",
        "Analysis",
        "evi-source-intake --lane analysis",
        aliases=("analytical notes", "audit analysis"),
        source_types=("analysis_document", "audit_report", "decision_analysis"),
        extensions=(".docx", ".md", ".pdf", ".txt"),
        parser_id="analysis_structured_v1",
        chunker="semantic_blocks_v1",
        fts_table="analysis_fts",
        schema=_CORE_SCHEMA
        + (
            "analysis_source",
            "analysis_claim",
            "analysis_evidence",
            "analysis_supporting_evidence",
            "analysis_risk",
            "analysis_alternative",
            "analysis_open_question",
            "analysis_accepted_decision",
            "analysis_blocked_item",
            "analysis_fts",
        ),
    ),
    _lane(
        "plan",
        "Plan",
        "evi-source-intake --lane plan",
        aliases=("planning", "project plan"),
        source_types=("project_plan", "implementation_plan", "task_plan"),
        extensions=(".docx", ".json", ".md", ".pdf", ".txt"),
        parser_id="plan_structured_v1",
        chunker="semantic_blocks_v1",
        fts_table="plan_fts",
        schema=_CORE_SCHEMA
        + (
            "plan_source",
            "plan_phase",
            "plan_milestone",
            "plan_task",
            "plan_owner",
            "plan_status",
            "plan_dependency",
            "plan_blocker",
            "plan_next_action",
            "plan_acceptance_criteria",
            "plan_fts",
        ),
    ),
    _lane(
        "mode",
        "Mode",
        "evi-mode",
        aliases=("operating mode", "mode contract"),
        source_types=("mode_contract", "operating_rules", "control_prompt"),
        extensions=(".docx", ".json", ".md", ".txt"),
        parser_id="mode_contract_v1",
        chunker="rule_blocks_v1",
        fts_table="mode_fts",
        schema=_CORE_SCHEMA
        + (
            "mode_source",
            "mode_scope",
            "mode_trigger",
            "mode_rule",
            "mode_gate",
            "mode_allowed_action",
            "mode_blocked_action",
            "mode_response_template",
            "mode_priority",
            "mode_supersede_ledger",
            "mode_fts",
        ),
    ),
    _lane(
        "docs",
        "Docs",
        "evi-source-intake --lane docs",
        aliases=("documents", "documentation"),
        source_types=("document", "markdown", "html_document", "xml_document"),
        extensions=(
            ".doc",
            ".docx",
            ".html",
            ".md",
            ".odt",
            ".rst",
            ".rtf",
            ".txt",
            ".xml",
        ),
        parser_id="document_structure_v1",
        chunker="document_hierarchy_v1",
        fts_table="doc_fts",
        schema=_CORE_SCHEMA
        + (
            "doc_file",
            "doc_structure",
            "doc_heading",
            "doc_paragraph",
            "doc_table_extract",
            "doc_chunk",
            "doc_image_reference",
            "docling_extraction",
            "source_structure_signature",
            "doc_fts",
        ),
    ),
    _lane(
        "data_excel",
        "Data / Excel / CSV",
        "evi-source-intake --lane data_excel",
        aliases=("excel", "data", "excel csv", "spreadsheet data"),
        source_types=("spreadsheet", "delimited_data", "structured_data"),
        extensions=(
            ".csv",
            ".json",
            ".jsonl",
            ".hyper",
            ".parquet",
            ".tsv",
            ".xls",
            ".xlsm",
            ".xlsx",
        ),
        parser_id="office_data_structural_v1",
        chunker="table_structure_v1",
        fts_table="data_fts",
        schema=_CORE_SCHEMA
        + (
            "data_source",
            "sheet_workbook",
            "sheet_tab",
            "sheet_range",
            "sheet_cell_sample",
            "sheet_table",
            "sheet_formula",
            "sheet_formula_dependency_edge",
            "sheet_chart_metadata",
            "csv_header",
            "csv_row_sample",
            "json_structure",
            "json_record_sample",
            "parquet_schema",
            "parquet_row_sample",
            "duckdb_tabular_stage_receipt",
            "polars_tabular_stage_receipt",
            "openpyxl_workbook_inspection",
            "pandas_workbook_inspection",
            "tableau_hyper_inspection",
            "docling_extraction",
            "data_chunk",
            "data_structure_signature",
            "data_fts",
        ),
    ),
    _lane(
        "ppt",
        "PPT / Presentation",
        "evi-source-intake --lane ppt",
        aliases=("presentation", "powerpoint"),
        source_types=("presentation", "slide_deck"),
        extensions=(".odp", ".ppt", ".pptx"),
        parser_id="presentation_structure_v1",
        chunker="slide_structure_v1",
        fts_table="ppt_fts",
        schema=_CORE_SCHEMA
        + (
            "ppt_file",
            "ppt_slide",
            "ppt_shape",
            "ppt_text_block",
            "ppt_notes",
            "ppt_table",
            "ppt_image_reference",
            "docling_extraction",
            "ppt_slide_relationship",
            "ppt_chunk",
            "ppt_structure_signature",
            "ppt_fts",
        ),
    ),
    _lane(
        "pdf_ocr",
        "PDF / OCR",
        "evi-source-intake --lane pdf_ocr",
        aliases=("pdf", "portable document"),
        source_types=("pdf_document", "scanned_pdf"),
        extensions=(".pdf",),
        parser_id="pdf_ocr_structural_v2",
        chunker="page_block_v1",
        fts_table="pdf_fts",
        schema=_CORE_SCHEMA
        + (
            "pdf_file",
            "pdf_page",
            "pdf_text_block",
            "pdf_image_block",
            "pdf_ocr_run",
            "pdf_ocr_block",
            "pdf_ocr_line",
            "pdf_review_region",
            "docling_extraction",
            "pdf_structure_signature",
            "pdf_fts",
        ),
    ),
    _lane(
        "images_ocr",
        "Images / OCR",
        "evi-source-intake --lane images_ocr",
        aliases=("images", "image", "image ocr"),
        source_types=("image", "scanned_image"),
        extensions=(".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"),
        parser_id="image_ocr_structural_v2",
        chunker="ocr_region_v1",
        fts_table="image_ocr_fts",
        schema=_CORE_SCHEMA
        + (
            "image_file",
            "image_metadata",
            "image_ocr_run",
            "image_ocr_block",
            "image_ocr_line",
            "image_review_region",
            "image_ocr_fts",
        ),
    ),
    _lane(
        "artifacts",
        "Artifacts",
        "evi-source-intake --lane artifacts",
        aliases=("project artifacts", "artifact vault"),
        source_types=("project_artifact", "structured_artifact", "notebook"),
        extensions=(
            ".csv",
            ".html",
            ".ipynb",
            ".json",
            ".jsonl",
            ".md",
            ".mmd",
            ".avi",
            ".flac",
            ".m4a",
            ".mkv",
            ".mov",
            ".mp3",
            ".mp4",
            ".ogg",
            ".parquet",
            ".svg",
            ".txt",
            ".wav",
            ".webm",
            ".xml",
        ),
        parser_id="artifact_structure_v1",
        chunker="artifact_blocks_v1",
        fts_table="artifact_fts",
        schema=_CORE_SCHEMA
        + (
            "project_artifact",
            "artifact_metadata",
            "artifact_text_extract",
            "artifact_relation_edge",
            "artifact_review_required",
            "artifact_media_probe",
            "artifact_fts",
        ),
    ),
    _lane(
        "custom",
        "Custom",
        "evi-source-intake --lane custom",
        aliases=("custom source", "generic", "other"),
        source_types=("custom_file", "custom_folder"),
        extensions=(
            ".csv",
            ".db",
            ".docx",
            ".html",
            ".json",
            ".jsonl",
            ".md",
            ".pdf",
            ".sqlite",
            ".sqlite3",
            ".tsv",
            ".txt",
            ".xml",
            ".yaml",
            ".yml",
            ".zip",
        ),
        parser_id="custom_best_effort_v1",
        chunker="bounded_text_v1",
        fts_table="custom_fts",
        schema=_CORE_SCHEMA
        + (
            "custom_source",
            "custom_item",
            "custom_evidence",
            "custom_decision",
            "custom_next_action",
            "custom_fts",
        ),
    ),
    _lane(
        "brain_loader",
        "SQLite PV Candidate Loader",
        "evi-source-intake --lane brain_loader",
        aliases=("brain-loader", "Brain Loader", "load brain", "brain import"),
        source_types=("sqlite_brain_package", "brain_folder", "brain_database"),
        extensions=(".db", ".sqlite", ".sqlite3", ".zip"),
        parser_id="brain_package_loader_v1",
        chunker="package_member_v1",
        fts_table="brain_loader_fts",
        schema=_CORE_SCHEMA
        + (
            "brain_loader_source",
            "brain_loader_package",
            "brain_loader_member",
            "brain_loader_manifest",
            "brain_loader_pointer",
            "brain_loader_database",
            "brain_loader_schema_object",
            "brain_loader_relationship",
            "brain_loader_receipt",
            "sqlalchemy_schema_inspection",
            "brain_loader_fts",
        ),
    ),
    _lane(
        "research",
        "Research",
        "evi-source-intake --lane research",
        aliases=("research evidence", "research sources"),
        source_types=("research_document", "research_dataset", "research_note"),
        extensions=(
            ".csv",
            ".docx",
            ".json",
            ".md",
            ".parquet",
            ".pdf",
            ".txt",
            ".xlsx",
        ),
        parser_id="research_evidence_v1",
        chunker="claim_evidence_v1",
        fts_table="research_fts",
        schema=_CORE_SCHEMA
        + (
            "research_source",
            "research_question",
            "research_hypothesis",
            "research_method",
            "research_evidence",
            "research_finding",
            "research_limitation",
            "research_citation",
            "research_open_question",
            "research_receipt",
            "research_fts",
        ),
    ),
    _lane(
        "project_engulf",
        "Project Engulf",
        "evi-source-intake --lane project_engulf",
        aliases=("project-engulf", "engulf project", "project import"),
        source_types=("project_folder", "project_archive"),
        extensions=(".zip",),
        parser_id="project_engulf_v1",
        chunker="project_structure_v1",
        fts_table="project_engulf_fts",
        schema=_CORE_SCHEMA
        + (
            "project_engulf_source",
            "project_engulf_file",
            "project_engulf_component",
            "project_engulf_relationship",
            "project_engulf_conflict",
            "project_engulf_sector_target",
            "project_engulf_chunk",
            "project_engulf_origin",
            "project_engulf_schema_mapping",
            "project_engulf_object_decision",
            "project_engulf_run",
            "project_engulf_topology_update",
            "project_engulf_receipt",
            "project_engulf_fts",
        ),
    ),
    _lane(
        "sqlite_brain",
        "SQLite Brain",
        "evi-source-intake --lane sqlite_brain",
        aliases=("sqlite-brain", "sqlite", "sqlitebrain", "sqlite brain import"),
        source_types=("sqlite_database", "sqlite_brain_package"),
        extensions=(".db", ".sqlite", ".sqlite3", ".zip"),
        parser_id="sqlite_brain_inspector_v1",
        chunker="sqlite_schema_row_v1",
        fts_table="loaded_sqlite_brain_fts",
        schema=_CORE_SCHEMA
        + (
            "loaded_sqlite_brain_source",
            "loaded_sqlite_brain_database",
            "loaded_sqlite_brain_schema_object",
            "loaded_sqlite_brain_table_stat",
            "loaded_sqlite_brain_foreign_key",
            "loaded_sqlite_brain_fts_table",
            "loaded_sqlite_brain_relationship",
            "loaded_sqlite_brain_integrity_result",
            "loaded_sqlite_brain_package_pointer",
            "loaded_sqlite_brain_compatibility",
            "loaded_sqlite_brain_sector_mapping",
            "loaded_sqlite_brain_receipt",
            "sqlalchemy_schema_inspection",
            "loaded_sqlite_brain_fts",
        ),
    ),
)


def _lane_schema_registry_payload() -> tuple[
    dict[str, Any], MappingProxyType[str, dict[str, Any]], str
]:
    if not LANE_SCHEMA_REGISTRY_PATH.is_file():
        raise LaneRegistryError(
            f"Lane schema registry is missing: {LANE_SCHEMA_REGISTRY_PATH.name}"
        )
    try:
        payload = json.loads(LANE_SCHEMA_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneRegistryError("Lane schema registry is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "registry_version",
        "base_schema",
        "entity_table_template",
        "lanes",
    }:
        raise LaneRegistryError("Lane schema registry has an unexpected root shape.")
    if (
        payload.get("schema") != LANE_SCHEMA_REGISTRY_SCHEMA
        or payload.get("registry_version") != 1
    ):
        raise LaneRegistryError("Lane schema registry identity is unsupported.")
    base = payload.get("base_schema")
    template = payload.get("entity_table_template")
    if not isinstance(base, dict) or set(base) != {"schema_id", "tables", "owner"}:
        raise LaneRegistryError("Lane base-schema contract is malformed.")
    if base != {
        "schema_id": "evidence-lane.universal-lane.v5",
        "tables": sorted(CORE_SCHEMA_TABLES),
        "owner": "lane_engine.py:_create_lane_schema",
    }:
        raise LaneRegistryError("Lane base-schema bytes disagree with runtime authority.")
    expected_template = {
        "template_id": "GENERIC_ENTITY_RECORD_V1",
        "strict": True,
        "columns": [
            {"name": "record_id", "declaration": "INTEGER PRIMARY KEY"},
            {
                "name": "source_id",
                "declaration": (
                    "INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE"
                ),
            },
            {"name": "locator", "declaration": "TEXT NOT NULL"},
            {"name": "payload_json", "declaration": "TEXT NOT NULL"},
        ],
    }
    if template != expected_template:
        raise LaneRegistryError("Lane entity-table template bytes are not canonical.")
    rows = payload.get("lanes")
    if not isinstance(rows, list) or len(rows) != len(_DEFINITIONS):
        raise LaneRegistryError("Lane schema registry must contain every canonical lane.")
    definitions = {lane.canonical_lane_id: lane for lane in _DEFINITIONS}
    assets: dict[str, dict[str, Any]] = {}
    entry_keys = {
        "lane_id",
        "schema_id",
        "schema_version",
        "base_schema_id",
        "fts_table",
        "tables",
        "lane_table_builder",
        "extension_namespace",
        "mutation_policy",
        "migration_ledger",
        "sqlite_master_projection_sha256",
    }
    migration_keys = {
        "migration_id",
        "sequence",
        "from_version",
        "to_version",
        "operation",
        "additive_only",
    }
    rebuild_migration_keys = migration_keys | {"rebuild_required"}
    for raw in rows:
        if not isinstance(raw, dict) or set(raw) != entry_keys:
            raise LaneRegistryError("A lane schema entry has an unexpected shape.")
        lane_id = str(raw.get("lane_id") or "")
        definition = definitions.get(lane_id)
        if definition is None or lane_id in assets:
            raise LaneRegistryError(f"Unknown or duplicate lane schema: {lane_id!r}")
        version = raw.get("schema_version")
        tables = raw.get("tables")
        migrations = raw.get("migration_ledger")
        if not isinstance(version, int) or version < 1:
            raise LaneRegistryError(f"Invalid lane schema version: {lane_id}")
        if (
            raw.get("schema_id")
            != f"evidence-lane.lane-schema.{lane_id}.v{version:03d}"
            or raw.get("base_schema_id") != base["schema_id"]
            or raw.get("fts_table") != definition.fts_table
            or raw.get("mutation_policy") != definition.mutation_policy
            or raw.get("extension_namespace")
            != f"evidence_lane.{lane_id}.extensions"
            or raw.get("lane_table_builder")
            != (
                "GIT_HISTORY_V2_PLUS_GENERIC_ENTITY_RECORD_V1"
                if lane_id in PRIMARY_CODE_LANES
                else "GENERIC_ENTITY_RECORD_V1"
            )
            or not isinstance(tables, list)
            or tuple(tables) != definition.schema_contract
            or len(tables) != len(set(tables))
            or not all(
                isinstance(table, str) and _SAFE_SCHEMA_NAME_RE.fullmatch(table)
                for table in tables
            )
            or not isinstance(migrations, list)
            or not migrations
            or not _SHA256_RE.fullmatch(
                str(raw.get("sqlite_master_projection_sha256") or "")
            )
        ):
            raise LaneRegistryError(f"Lane schema contract mismatch: {lane_id}")
        previous_version = 0
        for sequence, migration in enumerate(migrations, start=1):
            if (
                not isinstance(migration, dict)
                or frozenset(migration)
                not in {frozenset(migration_keys), frozenset(rebuild_migration_keys)}
                or migration.get("sequence") != sequence
                or migration.get("from_version") != previous_version
                or not isinstance(migration.get("to_version"), int)
                or migration["to_version"] <= previous_version
                or not isinstance(migration.get("additive_only"), bool)
                or (
                    migration.get("additive_only") is not True
                    and migration.get("rebuild_required") is not True
                )
                or not str(migration.get("migration_id") or "").startswith(
                    f"{lane_id}."
                )
                or not str(migration.get("operation") or "")
            ):
                raise LaneRegistryError(
                    f"Lane migration ledger is not additive and contiguous: {lane_id}"
                )
            previous_version = int(migration["to_version"])
        if previous_version != version:
            raise LaneRegistryError(
                f"Lane migration head does not equal its schema version: {lane_id}"
            )
        entry = json.loads(canonical_json_bytes(raw).decode("utf-8"))
        entry["contract_sha256"] = sha256_bytes(canonical_json_bytes(raw))
        assets[lane_id] = entry
    if tuple(assets) != tuple(lane.canonical_lane_id for lane in _DEFINITIONS):
        raise LaneRegistryError("Lane schema registry order is not canonical.")
    return (
        payload,
        MappingProxyType(assets),
        sha256_file(LANE_SCHEMA_REGISTRY_PATH),
    )


(
    _LANE_SCHEMA_REGISTRY_PAYLOAD,
    _LANE_SCHEMA_ASSETS,
    LANE_SCHEMA_REGISTRY_SHA256,
) = _lane_schema_registry_payload()


def lane_schema_asset(lane_id: str) -> dict[str, Any]:
    """Return one detached, hash-bound versioned lane schema contract."""

    try:
        asset = _LANE_SCHEMA_ASSETS[lane_id]
    except KeyError as exc:
        raise LaneRegistryError(f"Unknown lane schema: {lane_id!r}") from exc
    return json.loads(canonical_json_bytes(asset).decode("utf-8"))


def lane_schema_registry_contract() -> dict[str, Any]:
    """Return the bounded public identity of the complete schema registry."""

    return {
        "schema": LANE_SCHEMA_REGISTRY_SCHEMA,
        "registry_version": _LANE_SCHEMA_REGISTRY_PAYLOAD["registry_version"],
        "asset_path": "schemas/lane-schema-registry.v001.json",
        "asset_sha256": LANE_SCHEMA_REGISTRY_SHA256,
        "lane_count": len(_LANE_SCHEMA_ASSETS),
        "base_schema_id": _LANE_SCHEMA_REGISTRY_PAYLOAD["base_schema"][
            "schema_id"
        ],
        "entity_table_template_id": _LANE_SCHEMA_REGISTRY_PAYLOAD[
            "entity_table_template"
        ]["template_id"],
        "extension_model": "PER_LANE_NAMESPACED_ADDITIVE_VERSIONING",
    }


def _lane_schema_evolution_policy_payload() -> tuple[dict[str, Any], str]:
    if not LANE_SCHEMA_EVOLUTION_POLICY_PATH.is_file():
        raise LaneRegistryError(
            "Lane schema evolution policy is missing: "
            f"{LANE_SCHEMA_EVOLUTION_POLICY_PATH.name}"
        )
    try:
        payload = json.loads(
            LANE_SCHEMA_EVOLUTION_POLICY_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneRegistryError(
            "Lane schema evolution policy is not valid UTF-8 JSON."
        ) from exc
    expected = {
        "schema": LANE_SCHEMA_EVOLUTION_POLICY_SCHEMA,
        "policy_version": 3,
        "ledger": {
            "schema": "evidence-lane.lane-schema-migration-ledger.v1",
            "table": "lane_schema_migration",
            "append_only": True,
            "hash_chained": True,
            "updates_forbidden": True,
            "deletes_forbidden": True,
        },
        "migration": {
            "schema": "evidence-lane.lane-schema-migration-plan.v1",
            "allowed_operations": [
                "CREATE_TABLE",
                "ADD_COLUMN",
                "CREATE_INDEX",
            ],
            "raw_sql_allowed": False,
            "additive_only": True,
            "single_version_increment": True,
            "physical_name_prefix_template": "elx_{lane_id}_",
            "migration_id_prefix_template": "{lane_id}.extension.",
        },
        "compatibility": {
            "existing_objects_must_remain": True,
            "existing_columns_must_remain_byte_compatible": True,
            "existing_indexes_must_remain": True,
            "foreign_key_check_required": True,
            "integrity_check_required": True,
            "base_schema_builder_parity_required": True,
        },
        "fts_rebuild": {
            "explicit_request_only": True,
            "preserve_rowids": True,
            "before_after_content_hash_required": True,
            "before_after_row_count_required": True,
        },
        "core_rebuild": {
            "allowed_operations": [
                "REBUILD_COMPACT_CONTENT_CAS_AND_CONTENTLESS_FTS",
                "REBUILD_REFERENCE_ONLY_CHUNKS_AND_COMPACT_AUTHORITY_INDEX",
            ],
            "explicit_user_authorization_required": True,
            "rebuild_from_exact_source_hashes": True,
            "exact_byte_reconstruction_required": True,
            "integrity_and_foreign_key_checks_required": True,
            "accepted_artifact_in_place_mutation_allowed": False,
            "atomic_generation_swap_required": True,
            "superseded_storage_route_retained": False,
        },
        "protected_lanes": {
            "lane_ids": ["github_code", "local_code"],
            "explicit_user_confirmation_required": True,
            "confirmation_template": (
                "AUTHORIZE_LANE_SCHEMA_EVOLUTION::"
                "{lane_id}::{migration_id}::{ddl_sha256}"
            ),
            "missing_or_mismatched_confirmation_effect": (
                "FAIL_CLOSED_BEFORE_SQLITE_WRITE"
            ),
        },
    }
    if payload != expected:
        raise LaneRegistryError(
            "Lane schema evolution policy bytes are not canonical."
        )
    return payload, sha256_file(LANE_SCHEMA_EVOLUTION_POLICY_PATH)


(
    _LANE_SCHEMA_EVOLUTION_POLICY,
    LANE_SCHEMA_EVOLUTION_POLICY_SHA256,
) = _lane_schema_evolution_policy_payload()


def lane_schema_evolution_contract(lane_id: str) -> dict[str, Any]:
    """Return lane extension rules plus the authorized core-rebuild boundary."""

    if lane_id not in _LANE_SCHEMA_ASSETS:
        raise LaneRegistryError(f"Unknown lane schema: {lane_id!r}")
    policy = json.loads(
        canonical_json_bytes(_LANE_SCHEMA_EVOLUTION_POLICY).decode("utf-8")
    )
    policy.update(
        {
            "asset_path": "schemas/lane-schema-evolution.v001.json",
            "asset_sha256": LANE_SCHEMA_EVOLUTION_POLICY_SHA256,
            "lane_id": lane_id,
            "extension_namespace": _LANE_SCHEMA_ASSETS[lane_id][
                "extension_namespace"
            ],
            "physical_name_prefix": f"elx_{lane_id}_",
            "migration_id_prefix": f"{lane_id}.extension.",
            "explicit_user_confirmation_required": (
                lane_id in PRIMARY_CODE_LANES
            ),
        }
    )
    return policy


def _lane_artifact_role_registry_payload() -> tuple[
    dict[str, Any], MappingProxyType[str, dict[str, Any]], str
]:
    if not LANE_ARTIFACT_ROLE_REGISTRY_PATH.is_file():
        raise LaneRegistryError(
            "Lane artifact-role registry is missing: "
            f"{LANE_ARTIFACT_ROLE_REGISTRY_PATH.name}"
        )
    try:
        payload = json.loads(
            LANE_ARTIFACT_ROLE_REGISTRY_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneRegistryError(
            "Lane artifact-role registry is not valid UTF-8 JSON."
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "registry_version",
        "core_roles",
        "extension_policy",
        "role_catalog",
        "lanes",
    }:
        raise LaneRegistryError(
            "Lane artifact-role registry has an unexpected root shape."
        )
    if (
        payload.get("schema") != LANE_ARTIFACT_ROLE_REGISTRY_SCHEMA
        or payload.get("registry_version") != 1
    ):
        raise LaneRegistryError("Lane artifact-role registry identity is unsupported.")
    expected_core_roles = [
        {
            "role_id": "sqlite_authority",
            "classification": "REQUIRED",
            "path_template": "{sqlite_filename}",
            "seal": "FOUR_FILE_CONTRACT",
        },
        {
            "role_id": "mermaid_projection",
            "classification": "REQUIRED",
            "path_template": "{mmd_filename}",
            "seal": "FOUR_FILE_CONTRACT",
        },
        {
            "role_id": "dot_projection",
            "classification": "REQUIRED",
            "path_template": "{dot_filename}",
            "seal": "FOUR_FILE_CONTRACT",
        },
        {
            "role_id": "toolchain_identity",
            "classification": "REQUIRED",
            "path_template": "tools.json",
            "seal": "FOUR_FILE_CONTRACT",
        },
        {
            "role_id": "pointer_evidence",
            "classification": "REQUIRED",
            "path_template": "lane_pointer.json",
            "seal": "EVIDENCE_ARTIFACTS",
        },
        {
            "role_id": "refresh_receipt",
            "classification": "REQUIRED",
            "path_template": "refresh_receipt.json",
            "seal": "EVIDENCE_ARTIFACTS",
        },
        {
            "role_id": "lane_manifest",
            "classification": "REQUIRED",
            "path_template": "lane_manifest.json",
            "seal": "SELF_MANIFEST",
        },
    ]
    if payload.get("core_roles") != expected_core_roles:
        raise LaneRegistryError("Lane core artifact roles are not canonical.")
    expected_extension_policy = {
        "root_template": "extensions/{extension_id}",
        "role_id_template": (
            "evidence_lane.{lane_id}.extensions.{extension_id}.{role_name}"
        ),
        "classifications": ["REQUIRED", "CONDITIONAL", "OPTIONAL"],
        "undeclared_files": "FAIL_CLOSED",
        "core_override": "FORBIDDEN",
        "path_escape": "FORBIDDEN",
        "symlink": "FORBIDDEN",
        "unrelated_lane_effect": "NONE",
    }
    if payload.get("extension_policy") != expected_extension_policy:
        raise LaneRegistryError("Lane artifact extension policy is not canonical.")
    catalog_payload = payload.get("role_catalog")
    if not isinstance(catalog_payload, dict) or not catalog_payload:
        raise LaneRegistryError("Lane artifact role catalog is empty or malformed.")
    role_catalog: dict[str, dict[str, Any]] = {}
    for role_name, role in catalog_payload.items():
        if (
            not _SAFE_SCHEMA_NAME_RE.fullmatch(str(role_name))
            or not isinstance(role, dict)
            or set(role) != {"classification", "condition"}
            or role.get("classification")
            not in {"REQUIRED", "CONDITIONAL", "OPTIONAL"}
            or (
                role["classification"] == "CONDITIONAL"
                and not str(role.get("condition") or "")
            )
            or (
                role["classification"] in {"REQUIRED", "OPTIONAL"}
                and role.get("condition") is not None
            )
        ):
            raise LaneRegistryError(
                f"Lane artifact role is malformed: {role_name!r}"
            )
        role_catalog[str(role_name)] = dict(role)
    lane_rows = payload.get("lanes")
    if not isinstance(lane_rows, list) or len(lane_rows) != len(_DEFINITIONS):
        raise LaneRegistryError(
            "Lane artifact registry must contain every canonical lane."
        )
    assets: dict[str, dict[str, Any]] = {}
    for raw, definition in zip(lane_rows, _DEFINITIONS, strict=True):
        if not isinstance(raw, dict) or set(raw) != {
            "lane_id",
            "conditional_roles",
            "optional_roles",
        }:
            raise LaneRegistryError("A lane artifact profile has an unexpected shape.")
        lane_id = str(raw.get("lane_id") or "")
        conditional = raw.get("conditional_roles")
        optional = raw.get("optional_roles")
        if (
            lane_id != definition.canonical_lane_id
            or not isinstance(conditional, list)
            or len(conditional) != len(set(conditional))
            or not isinstance(optional, list)
            or len(optional) != len(set(optional))
            or set(conditional) & set(optional)
            or any(
                name not in role_catalog
                or role_catalog[name]["classification"] != "CONDITIONAL"
                for name in conditional
            )
            or any(
                name not in role_catalog
                or role_catalog[name]["classification"] != "OPTIONAL"
                for name in optional
            )
        ):
            raise LaneRegistryError(
                f"Lane artifact profile is malformed: {lane_id!r}"
            )
        assets[lane_id] = json.loads(
            canonical_json_bytes(raw).decode("utf-8")
        )
    return (
        payload,
        MappingProxyType(assets),
        sha256_file(LANE_ARTIFACT_ROLE_REGISTRY_PATH),
    )


(
    _LANE_ARTIFACT_ROLE_REGISTRY,
    _LANE_ARTIFACT_ROLE_PROFILES,
    LANE_ARTIFACT_ROLE_REGISTRY_SHA256,
) = _lane_artifact_role_registry_payload()


def lane_artifact_contract(lane_id: str) -> dict[str, Any]:
    """Return one detached concrete required/conditional/optional contract."""

    try:
        lane = next(
            definition
            for definition in _DEFINITIONS
            if definition.canonical_lane_id == lane_id
        )
        profile = _LANE_ARTIFACT_ROLE_PROFILES[lane_id]
    except (KeyError, StopIteration) as exc:
        raise LaneRegistryError(f"Unknown lane artifact contract: {lane_id!r}") from exc
    path_values = {
        "sqlite_filename": lane.sqlite_filename,
        "mmd_filename": lane.mmd_filename,
        "dot_filename": lane.dot_filename,
    }
    required_roles = [
        {
            "role_id": row["role_id"],
            "classification": row["classification"],
            "path": str(row["path_template"]).format(**path_values),
            "seal": row["seal"],
        }
        for row in _LANE_ARTIFACT_ROLE_REGISTRY["core_roles"]
    ]
    catalog_payload = _LANE_ARTIFACT_ROLE_REGISTRY["role_catalog"]
    extension_required_roles = [
        {"role_name": name, **role}
        for name, role in catalog_payload.items()
        if role["classification"] == "REQUIRED"
    ]
    conditional_roles = [
        {"role_name": name, **catalog_payload[name]}
        for name in profile["conditional_roles"]
    ]
    optional_roles = [
        {"role_name": name, **catalog_payload[name]}
        for name in profile["optional_roles"]
    ]
    body = {
        "schema": "evidence-lane.lane-artifact-role-contract.v1",
        "registry_sha256": LANE_ARTIFACT_ROLE_REGISTRY_SHA256,
        "lane_id": lane_id,
        "required_roles": required_roles,
        "extension_required_roles": extension_required_roles,
        "conditional_roles": conditional_roles,
        "optional_roles": optional_roles,
        "extension_policy": _LANE_ARTIFACT_ROLE_REGISTRY[
            "extension_policy"
        ],
    }
    return {
        **json.loads(canonical_json_bytes(body).decode("utf-8")),
        "contract_sha256": sha256_bytes(canonical_json_bytes(body)),
    }

CANONICAL_LANE_IDS = tuple(lane.canonical_lane_id for lane in _DEFINITIONS)


def _normalize(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not normalized:
        raise LaneRegistryError("A lane alias must contain an alphanumeric character.")
    return normalized


def _validate() -> tuple[
    MappingProxyType[str, LaneDefinition],
    MappingProxyType[str, tuple[str, ...]],
]:
    registry: dict[str, LaneDefinition] = {}
    aliases: dict[str, set[str]] = {}
    sqlite_names: set[str] = set()
    for lane in _DEFINITIONS:
        if lane.canonical_lane_id in registry:
            raise LaneRegistryError(f"Duplicate lane ID: {lane.canonical_lane_id}")
        if lane.sqlite_filename in sqlite_names:
            raise LaneRegistryError(
                f"Duplicate lane SQLite name: {lane.sqlite_filename}"
            )
        registry[lane.canonical_lane_id] = lane
        sqlite_names.add(lane.sqlite_filename)
        for alias in lane.aliases:
            aliases.setdefault(_normalize(alias), set()).add(lane.canonical_lane_id)
    # The legacy alias /code is deliberately shared by two mutually exclusive modes.
    ambiguous = {
        alias: values
        for alias, values in aliases.items()
        if len(values) > 1 and not (alias == "code" and values == PRIMARY_CODE_LANES)
    }
    if ambiguous:
        raise LaneRegistryError(f"Ambiguous lane aliases: {ambiguous}")
    return (
        MappingProxyType(registry),
        MappingProxyType(
            {alias: tuple(sorted(values)) for alias, values in aliases.items()}
        ),
    )


LANE_REGISTRY, LANE_ALIAS_INDEX = _validate()


def resolve_lane_id(alias: str, *, code_mode: str | None = None) -> str:
    matches = LANE_ALIAS_INDEX.get(_normalize(alias))
    if not matches:
        raise LaneRegistryError(
            f"Unknown lane {alias!r}; expected one of {', '.join(CANONICAL_LANE_IDS)}."
        )
    if len(matches) == 1:
        return matches[0]
    if code_mode in PRIMARY_CODE_LANES:
        return str(code_mode)
    raise LaneRegistryError(
        "Use /evi-source-intake with an exact github_code or local_code override; "
        "the legacy code alias requires exactly one code mode."
    )


def get_lane(alias: str, *, code_mode: str | None = None) -> LaneDefinition:
    return LANE_REGISTRY[resolve_lane_id(alias, code_mode=code_mode)]


_SEMANTIC_PATH_LANES = (
    ("chat_lineage", ("chat", "lineage", "conversation", "prompt")),
    ("discussion", ("discussion", "meeting", "minutes")),
    ("analysis", ("analysis", "audit", "forensic")),
    ("plan", ("plan", "roadmap", "milestone")),
    ("mode", ("mode", "rules", "contract", "policy")),
    ("research", ("research", "study", "hypothesis", "citation")),
    ("artifacts", ("artifact", "output", "receipt")),
    ("brain_loader", ("brain_loader", "brain-package", "brain_package")),
    ("project_engulf", ("engulf", "project_archive", "project-import")),
)


def route_source(
    relative_path: str,
    *,
    code_mode: str,
    explicit_lane: str | None = None,
) -> str:
    """Resolve one source to one sector; never duplicate a sector write."""

    if code_mode not in PRIMARY_CODE_LANES:
        raise LaneRegistryError("code_mode must be github_code or local_code")
    if explicit_lane:
        return resolve_lane_id(explicit_lane, code_mode=code_mode)
    normalized = relative_path.replace("\\", "/").lower()
    suffix = Path(normalized).suffix.lower()
    filename = Path(normalized).name
    name_tokens = normalized.replace("-", "_")

    for lane_id, tokens in _SEMANTIC_PATH_LANES:
        if any(token in name_tokens for token in tokens):
            return lane_id
    if suffix == ".pdf":
        return "pdf_ocr"
    if suffix in LANE_REGISTRY["images_ocr"].extensions:
        return "images_ocr"
    if filename in _CODE_MANIFEST_NAMES:
        return code_mode
    if suffix in LANE_REGISTRY["data_excel"].extensions:
        return "data_excel"
    if suffix in LANE_REGISTRY["ppt"].extensions:
        return "ppt"
    if suffix in (".db", ".sqlite", ".sqlite3"):
        return "sqlite_brain"
    if suffix in _CODE_EXTENSIONS:
        return code_mode
    if suffix in LANE_REGISTRY["docs"].extensions:
        return "docs"
    if suffix == ".zip":
        return "custom"
    return "custom"


def route_batch(
    paths: Iterable[str],
    *,
    code_mode: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    exact_overrides = overrides or {}
    return {
        path: route_source(
            path,
            code_mode=code_mode,
            explicit_lane=exact_overrides.get(path),
        )
        for path in sorted(set(paths))
    }


def catalog() -> list[dict[str, object]]:
    return [LANE_REGISTRY[lane_id].as_dict() for lane_id in CANONICAL_LANE_IDS]

"""One immutable V1/V3-fused registry for all Evidence Lane sectors."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

MUTATION_AUTOMATIC_APPEND_ONLY = "automatic_append_only"
MUTATION_NAMED_GRANT_RELOCK = "explicit_named_one_turn_grant_receipt_snapshot_relock"
PRIMARY_CODE_LANES = frozenset({"github_code", "local_code"})


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
        payload.update(
            {
                "sqlite_filename": self.sqlite_filename,
                "mmd_filename": self.mmd_filename,
                "dot_filename": self.dot_filename,
                "mmd_node_id": self.mmd_node_id,
            }
        )
        return payload


_CORE_SCHEMA = (
    "lane_meta",
    "lane_pointer",
    "source_registry",
    "source_tombstone",
    "chunk_index",
    "structured_fact",
    "parser_capability",
    "tfidf_term",
    "tfidf_vector",
    "refresh_receipt",
    "mutation_receipt",
)

_CODE_SCHEMA = _CORE_SCHEMA + (
    "sector_meta",
    "sector_head",
    "artifact_registry",
    "relation_edge",
    "code_source_registry",
    "code_file_snapshot",
    "code_chunk",
    "code_symbol",
    "code_import",
    "code_route",
    "code_dependency",
    "code_route_api_boundary",
    "code_config_build_test_chunk",
    "code_index_checkpoint",
    "code_source_active_head",
    "code_workflow_edge",
    "code_semantic_diff",
    "code_synthetic_snapshot_file",
    "code_snapshot_history",
    "code_good_snapshot",
    "snapshot_git_bridge",
    "git_commit_registry",
    "git_commit_parent",
    "git_file_change",
    "git_patch_hunk",
    "git_exact_line_change",
    "git_ref_registry",
    "git_push_event",
    "git_route_impact",
    "git_symbol_impact",
    "git_dependency_impact",
    "git_test_impact",
    "git_artifact_impact",
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
        "evi-02-git",
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
        "evi-03-local",
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
        "evi-05-chat-lineage",
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
        "evi-06-discussion",
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
        "evi-07-analysis",
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
        "evi-08-plan",
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
        "evi-09-docs",
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
            "source_structure_signature",
            "doc_fts",
        ),
    ),
    _lane(
        "data_excel",
        "Data / Excel / CSV",
        "evi-10-data-excel",
        aliases=("excel", "data", "excel csv", "spreadsheet data"),
        source_types=("spreadsheet", "delimited_data", "structured_data"),
        extensions=(
            ".csv",
            ".json",
            ".jsonl",
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
            "data_chunk",
            "data_structure_signature",
            "data_fts",
        ),
    ),
    _lane(
        "ppt",
        "PPT / Presentation",
        "evi-11-ppt",
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
            "ppt_slide_relationship",
            "ppt_chunk",
            "ppt_structure_signature",
            "ppt_fts",
        ),
    ),
    _lane(
        "pdf_ocr",
        "PDF / OCR",
        "evi-12-pdf-ocr",
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
            "pdf_structure_signature",
            "pdf_fts",
        ),
    ),
    _lane(
        "images_ocr",
        "Images / OCR",
        "evi-13-images-ocr",
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
        "evi-14-artifacts",
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
            ".parquet",
            ".svg",
            ".txt",
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
            "artifact_fts",
        ),
    ),
    _lane(
        "custom",
        "Custom",
        "evi-15-custom",
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
        "evi-04-sqlite-pv-candidate-loader",
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
            "brain_loader_fts",
        ),
    ),
    _lane(
        "research",
        "Research",
        "evi-16-research",
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
        "evi-17-project-engulf",
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
        "evi-18-sqlite-brain",
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
            "loaded_sqlite_brain_fts",
        ),
    ),
)

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
        "Use /evi-02-git or /evi-03-local; the legacy code alias requires "
        "exactly one mode: github_code or local_code."
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

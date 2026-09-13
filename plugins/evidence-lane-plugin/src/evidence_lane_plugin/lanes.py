"""Separate authority and sector contracts, adapted from the admitted lane registry.

This catalog defines ownership and routing. An entry does not establish that its
parsers, mutations or native host routes have passed runtime qualification.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

from .code_schema_contract import CODE_TABLES
from .hashing import canonical_json_bytes, sha256_bytes

MUTATION_AUTOMATIC_APPEND_ONLY = 'verified_workflow_append'
MUTATION_NAMED_GRANT_RELOCK = 'selected_project_writer_and_workflow_contract'
PRIMARY_CODE_LANES = frozenset({'github_code', 'local_code'})
LANE_SCHEMA_REGISTRY_SCHEMA = 'evidence-lane.lane-schema-registry.v4'
LANE_ARTIFACT_ROLE_REGISTRY_SCHEMA = 'evidence-lane.lane-artifact-role-registry.v4'
LANE_SCHEMA_EVOLUTION_POLICY_SCHEMA = 'evidence-lane.lane-schema-evolution-policy.v4'
BASE_REGISTRY_SHA256 = 'b63a8708b7c27eef09c7680ab97ca3410554f4afacf819d39e0be1e824193376'


class LaneRegistryError(ValueError):
    """The canonical lane, ownership or source routing contract is invalid."""


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
    kind: Literal['authority', 'sector'] = 'sector'
    schema_owners: tuple[str, ...] = ()
    source_origins: tuple[str, ...] = ()
    operations: tuple[str, ...] = ('intake', 'query', 'refresh')
    natural_extensions: tuple[str, ...] = ()
    relationship_views: tuple[str, ...] = ()
    pointer_semantics: str | None = None

    @property
    def sqlite_filename(self) -> str:
        # Retain the existing sector filenames. Authority roles never use a sector filename.
        return f'{self.canonical_lane_id}_{self.kind}_v001.sqlite'

    @property
    def folder(self) -> str:
        # A selected project directory is already the complete PV root.  Every
        # initialized lane is therefore a direct child; kind remains a catalog
        # and permission distinction rather than another filesystem wrapper.
        return self.canonical_lane_id

    @property
    def database_relative_path(self) -> str:
        return f'{self.folder}/{self.sqlite_filename}'

    @property
    def files_relative_path(self) -> str:
        # Keep the historical property name for internal API compatibility,
        # while the physical directory says what it actually owns.  It is
        # created lazily on the first content-addressed write.
        return f'{self.folder}/objects'

    @property
    def schema_history_relative_path(self) -> str:
        # Migration history is projected as one direct lane file.  The SQLite
        # tables remain authoritative; identical empty schema folders are not
        # stamped into every lane.
        return f'{self.folder}/schema-history.v4.json'

    @property
    def mmd_filename(self) -> str:
        return f'{self.canonical_lane_id}.mmd'

    @property
    def dot_filename(self) -> str:
        return f'{self.canonical_lane_id}.dot'

    @property
    def mmd_node_id(self) -> str:
        return f'lane_{self.canonical_lane_id}'

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(folder=self.folder, sqlite_filename=self.sqlite_filename,
                      database_relative_path=self.database_relative_path,
                      files_relative_path=self.files_relative_path,
                      objects_relative_path=self.files_relative_path,
                      schema_history_relative_path=self.schema_history_relative_path,
                      mmd_filename=self.mmd_filename, dot_filename=self.dot_filename,
                      mmd_node_id=self.mmd_node_id,
                      qualification='definition_only_until_owning_workflow_verified')
        return result


def _lane(lane_id, label, command, *, aliases=(), source_types=(), extensions=(),
          parser_id, chunker, fts_table, schema=(), mutation_policy=MUTATION_NAMED_GRANT_RELOCK):
    return LaneDefinition(lane_id, label, command, (lane_id, label, command, *aliases),
                          source_types, extensions, parser_id, chunker, fts_table, schema,
                          mutation_policy)


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
    ("code_repo", "Repo", "code_repo"),
    ("git_commit", "Commit", "code_git_reference"),
    ("code_file", "File", "code_file"),
    ("code_symbol", "Symbol", "code_symbol"),
    ("app_route", "Route", "code_route"),
    ("dependency_item", "Dependency", "code_dependency"),
    ("project_artifact", "Artifact", "code_snapshot"),
)



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

_CODE_SCHEMA = CODE_TABLES

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



_RETAINED_DEFINITIONS = (
_lane(
        "github_code",
        "GitHub Code",
        "manage-project-sources --lane github_code",
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
        "manage-project-sources --lane local_code",
        aliases=("code", "local", "code folder", "local git worktree"),
        source_types=("local_code_folder", "local_git_worktree"),
        extensions=_CODE_EXTENSIONS,
        parser_id="local_code_snapshot_v2",
        chunker="tiered_git_history_v2_incremental",
        fts_table="code_chunk_fts",
        schema=_CODE_SCHEMA,
    ),
_lane(
        "docs",
        "Docs",
        "manage-project-sources --lane docs",
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
        "manage-project-sources --lane data_excel",
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
        "manage-project-sources --lane ppt",
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
        "manage-project-sources --lane pdf_ocr",
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
        "manage-project-sources --lane images_ocr",
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
        "manage-project-sources --lane artifacts",
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
        "manage-project-sources --lane custom",
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
        "research",
        "Research",
        "manage-project-sources --lane research",
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
)


# This preserves the admitted sector definitions, then narrows only the roles
# explicitly moved or split by the v4 contract. Declared source schemas are inputs
# to each profile's adaptation; they are not a second enabled v3 runtime.
_SECTOR_ADAPTATIONS: dict[str, dict[str, Any]] = {
    'github_code': {'relationship_views': ('repository_commit_file_symbol_route_dependency_artifact',),
                        'parser_id': 'code_git_blob_snapshot_v4', 'chunker_version': 'line_80_overlap8_v4',
                        'operations': ('index_git_checkpoint', 'query', 'read_lines', 'impact', 'refresh_git_checkpoint', 'export_selected_view'),
                        'pointer_semantics': 'Exact indexed repository and commit selection'},
    'local_code': {'relationship_views': ('repository_commit_file_symbol_route_dependency_artifact',),
                       'parser_id': 'code_worktree_snapshot_v4', 'chunker_version': 'line_80_overlap8_v4',
                       'operations': ('index', 'query', 'read_lines', 'impact', 'replace_utf8', 'refresh', 'export_selected_view'),
                       'pointer_semantics': 'Exact indexed working-tree source hashes'},
    'docs': {'natural_extensions': ('.docx', '.dotx', '.odt', '.md', '.html', '.pdf', '.png'),
                 'extensions': ('.doc', '.docx', '.dotx', '.html', '.htm', '.md', '.odt', '.rst', '.rtf', '.txt', '.xml'),
                 'parser_id': 'bounded_native_document_v4', 'chunker_version': 'paragraph_4096_v4',
                 'fts_table': 'doc_chunk_fts',
                 'schema_contract': ('doc_file', 'doc_version', 'doc_current', 'doc_structure', 'doc_paragraph',
                     'doc_heading', 'doc_table_extract', 'doc_image_reference', 'doc_relationship', 'doc_content_control',
                     'doc_revision', 'doc_field', 'doc_hyperlink', 'doc_bookmark', 'doc_embedded_object',
                     'doc_chunk', 'doc_chunk_fts', 'doc_render', 'doc_export', 'docling_extraction'),
                 'operations': ('index', 'query', 'read_bytes', 'generate_docx', 'edit_paragraph', 'render', 'export', 'refresh', 'export_selected_view'),
                 'pointer_semantics': 'Exact immutable document and native structure item locators; page renders are separate',
                 'relationship_views': ('document_structure_and_linked_assets',)},
    'data_excel': {'display_label': 'Excel', 'aliases': ('data_excel', 'Excel', 'spreadsheet', 'workbook'),
                       'extensions': ('.xls', '.xlsm', '.xlsx', '.xlsb', '.ods', '.xltx', '.xltm'),
                       'source_types': ('spreadsheet',), 'natural_extensions': ('.xlsx', '.xlsm', '.xls', '.xlsb', '.ods', '.xltx', '.xltm', '.pdf', '.png'),
                       'parser_id': 'spreadsheet_native_v4', 'chunker_version': 'typed_cell_text_v4', 'fts_table': 'sheet_chunk_fts',
                       'schema_contract': ('sheet_source', 'sheet_version', 'sheet_current', 'sheet_workbook', 'sheet_tab',
                           'sheet_cell_sample', 'sheet_formula', 'sheet_defined_name', 'sheet_range', 'sheet_validation',
                           'sheet_hyperlink', 'sheet_relationship', 'sheet_table', 'sheet_chart_metadata',
                           'sheet_chunk', 'sheet_chunk_fts', 'sheet_export', 'sheet_derivative'),
                       'operations': ('index', 'query', 'read_bytes', 'generate_xlsx', 'edit_cells', 'recalculate', 'render',
                                      'export', 'refresh', 'library_inspection', 'export_selected_view'),
                       'relationship_views': ('workbook_sheet_formula_dependencies',)},
    'ppt': {'extensions': ('.odp', '.ppt', '.pptx', '.pptm', '.potx', '.ppsx'),
                'natural_extensions': ('.pptx', '.pptm', '.potx', '.ppsx', '.odp', '.ppt', '.pdf', '.png'),
                'parser_id': 'bounded_native_presentation_v4', 'chunker_version': 'slide_paragraph_4096_v4',
                'fts_table': 'ppt_chunk_fts',
                'schema_contract': ('ppt_file', 'ppt_version', 'ppt_current', 'ppt_structure', 'ppt_slide', 'ppt_shape',
                    'ppt_text_block', 'ppt_notes', 'ppt_table', 'ppt_image_reference', 'ppt_slide_relationship', 'ppt_chart',
                    'ppt_chunk', 'ppt_chunk_fts', 'ppt_render', 'ppt_export', 'ppt_enrichment'),
                'operations': ('index', 'query', 'read_bytes', 'generate_pptx', 'edit_text', 'reorder_slides',
                    'render', 'export', 'refresh', 'enrich', 'convert_legacy', 'export_selected_view'),
                'pointer_semantics': 'Exact presentation snapshot, declared slide number and native object locators; rendered pages are separate',
                'relationship_views': ('slide_order_shapes_notes_and_assets',)},
    'pdf_ocr': {'natural_extensions': ('.pdf', '.txt', '.png'),
        'parser_id': 'bounded_native_pdf_forms_page_ocr_v4', 'chunker_version': 'pdf_native_text_4096_v4',
        'fts_table': 'pdf_chunk_fts',
        'schema_contract': ('pdf_file', 'pdf_version', 'pdf_current', 'pdf_structure', 'pdf_page',
            'pdf_text_block', 'pdf_text_line', 'pdf_image', 'pdf_table', 'pdf_link', 'pdf_annotation',
            'pdf_form_field', 'pdf_widget', 'pdf_outline', 'pdf_attachment', 'pdf_chunk', 'pdf_chunk_fts',
            'pdf_export', 'pdf_render', 'pdf_ocr_run', 'pdf_ocr_line', 'pdf_review_region', 'docling_extraction'),
        'operations': ('index', 'refresh', 'query', 'read_bytes', 'generate_pdf', 'edit_forms_metadata_pages',
            'render_selected_pages', 'ocr_selected_pages', 'export'),
        'pointer_semantics': 'Exact PDF snapshot, page and native item locators; raster/OCR/Docling derivatives keep independent source bindings.',
        'relationship_views': ('pages_forms_annotations_and_extraction_provenance',)},
    'images_ocr': {'extensions': ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.gif', '.webp',
                                  '.svg', '.mp3', '.wav', '.flac', '.m4a', '.ogg', '.mp4', '.mov', '.webm'),
                       'source_types': ('image', 'audio', 'video', 'media_metadata'),
                       'natural_extensions': ('.png', '.jpg', '.webp', '.wav', '.mp4', '.txt'),
                       'relationship_views': ('media_derivation_and_source_provenance',)},
    'research': {'natural_extensions': ('.md', '.json', '.bib'),
                     'relationship_views': ('questions_claims_evidence_citations_limitations',)},
    'artifacts': {'natural_extensions': ('*declared_by_producer',),
                      'relationship_views': ('producer_source_output_and_verification',),
                      'pointer_semantics': 'Exact produced artifact and verification receipt'},
    'custom': {'aliases': ('custom', 'Custom', 'custom source', 'generic', 'other', 'sqlite',
                           'selected sqlite', 'sqlite database'),
                   'source_types': ('custom_file', 'custom_folder', 'selected_sqlite_database'),
                   'extensions': ('.db', '.sqlite', '.sqlite3', '.zip'),
                   'natural_extensions': ('*declared_by_adapter',),
                   'relationship_views': ('adapter_declared_schema_and_source_lineage',)},
}
_SECTORS = tuple(replace(lane, schema_owners=(('media' if lane.canonical_lane_id == 'images_ocr' else lane.canonical_lane_id.replace('_', '')),),
                         source_origins=(f'authorities/project_sectors/{lane.canonical_lane_id}/schema.sql',),
                         **_SECTOR_ADAPTATIONS[lane.canonical_lane_id]) for lane in _RETAINED_DEFINITIONS)


def _sector(lane_id, label, extensions, entities, view, *, origin, aliases=()):
    return LaneDefinition(lane_id, label, f'manage-project-sources --lane {lane_id}',
                          (lane_id, label, *aliases), (lane_id,), extensions,
                          f'{lane_id}_profile', 'profile_declared', f'{lane_id}_fts', entities,
                          schema_owners=((lane_id.replace('_', '')),),
                          source_origins=(origin,) if origin else (),
                          natural_extensions=extensions, relationship_views=(view,))


_ADDITIONAL_SECTORS = (
    _sector('data', 'Structured data', ('.csv', '.tsv', '.json', '.jsonl', '.parquet', '.arrow', '.feather'),
            ('data_source', 'data_table', 'data_column', 'data_sample', 'data_lineage'),
            'source_schema_columns_samples_and_transform_lineage',
            origin='authorities/project_sectors/data/schema.sql'),
    _sector('tableau', 'Tableau', ('.twb', '.twbx', '.tds', '.tdsx', '.hyper', '.tde'),
            ('tableau_workbook', 'tableau_datasource', 'tableau_sheet', 'tableau_calculation', 'tableau_relationship'),
            'workbooks_sources_sheets_calculations_and_model_links',
            origin='authorities/project_sectors/tableau/schema.sql'),
    _sector('power_bi', 'Power BI', ('.pbip', '.pbix', '.pbit', '.pbir', '.pbism', '.tmdl', '.bim'),
            ('powerbi_project', 'powerbi_report', 'powerbi_model', 'powerbi_table', 'powerbi_measure', 'powerbi_relationship'),
            'projects_reports_models_measures_and_relationships',
            origin='authorities/project_sectors/power_bi/schema.sql'),
)

_TABULAR_ADAPTATIONS = {
    'data': {'parser_id': 'structured_data_native_v4', 'chunker_version': 'typed_row_text_v4', 'fts_table': 'data_chunk_fts',
        'schema_contract': ('data_source', 'data_version', 'data_current', 'data_table', 'data_column', 'data_sample',
            'data_lineage', 'data_chunk', 'data_chunk_fts', 'data_export', 'data_derivative'),
        'operations': ('index', 'query', 'read_bytes', 'generate', 'transform', 'library_inspection', 'export', 'refresh', 'export_selected_view')},
}
_ADDITIONAL_SECTORS = tuple(replace(lane, **_TABULAR_ADAPTATIONS.get(lane.canonical_lane_id, {})) for lane in _ADDITIONAL_SECTORS)
# Revisions 24 and 25 narrow Office to Word, PowerPoint and Excel. Keep only the
# ids as a fail-closed negative guard; no retired profile or schema is packaged.
RETIRED_OFFICE_LANE_IDS = frozenset({'onenote', 'access', 'visio', 'outlook', 'project', 'publisher'})


def _authority(lane_id, label, owners, tables, operations, view, origin, *, aliases=(), pointer=None):
    return LaneDefinition(lane_id, label, f'evi {lane_id}', (lane_id, label, *aliases), (), (),
                          'owning_workflow', 'authority_owned', '', tables,
                          MUTATION_AUTOMATIC_APPEND_ONLY, 'authority', owners, (origin,), operations,
                          (), (view,), pointer)


_AUTHORITIES = (
    _authority('plan', 'Plan', ('plan', 'steer', 'jobs', 'delta', 'validation', 'validationrun'),
               ('plan_current', 'plan_dependencies', 'plan_events', 'plan_revisions', 'plan_tasks',
                'validation_policy_current', 'validation_policy_revisions', 'validationrun_summaries'),
               ('create', 'read', 'replace', 'transition', 'verify', 'configure_validation'), 'tasks_dependencies_revisions_status_transitions',
               'src/evidence_lane_plugin/plan_runtime.py', pointer='Exact Plan revision and immutable dependency snapshot'),
    _authority('chat_lineage', 'ChatLineage', ('lineage', 'continuation', 'prompt', 'turn'),
               ('lineage_chunks', 'lineage_events', 'lineage_fts'),
               ('append_visible_event', 'query', 'bind_capture', 'handoff', 'verify'), 'tasks_visible_events_ancestry_steers_handoffs',
               'src/evidence_lane_plugin/lineage.py', aliases=('lineage',)),
    _authority('canon', 'task exchange authority', ('canon',), ('canon_contract_current', 'canon_contracts', 'canon_events', 'canon_exchanges', 'canon_participants', 'canon_supersessions', 'canon_task_edges', 'canon_task_edge_bindings'),
               ('offer', 'inspect', 'decide', 'supersede', 'return_result', 'verify'), 'typed_exchanges_consequences_and_receiver_decisions',
               'authorities/canon_input/schema.sql', aliases=('canon_input',)),
    _authority('memory', 'Project Memory', ('memory',), ('memory_checkpoints', 'memory_edges', 'memory_events', 'memory_fts', 'memory_locators'),
               ('append', 'search', 'link', 'verify'), 'records_sources_links_and_attribution',
               'authorities/project_memory/schema.sql', aliases=('project_memory',),
               pointer='Exact bounded attributed Memory slice, never host MEMORY.md'),
    _authority('learning', 'Learning', ('learning',), ('learning_controls', 'learning_current', 'learning_events', 'learning_fts', 'learning_versions'),
               ('record_verified_exit', 'query', 'revoke', 'verify'), 'verified_delta_results_lessons_and_provenance',
               'authorities/agent_learning/schema.sql', aliases=('agent_learning',)),
    _authority('sources', 'Sources', ('sources', 'restoration', 'gitbranch', 'sourceroutes', 'sourcematerialization', 'customlanes'), ('customlanes_contracts', 'customlanes_current', 'sourceroutes_receipts', 'sourceroutes_requests', 'sourceroutes_preparations', 'sourcematerialization_runs', 'gitbranch_history', 'gitbranch_current', 'gitbranch_sync', 'gitbranch_enrollment', 'registry_meta', 'intake_batch', 'source_object', 'source_occurrence', 'source_member', 'source_relation', 'source_policy_receipt', 'source_exclusion_summary', 'source_archive_receipt', 'source_provenance', 'source_assertion_set', 'source_sqlite_asset', 'source_sqlite_schema_object', 'source_sqlite_table_stat', 'source_sqlite_receipt', 'source_sqlite_foreign_key', 'source_custom_schema', 'source_custom_schema_mapping', 'source_custom_schema_receipt', 'source_identity_entity', 'source_identity_assertion', 'source_identity_relation', 'source_identity_receipt', 'source_graph_snapshot', 'source_graph_node', 'source_graph_edge', 'source_graph_file_coverage', 'source_graph_diff', 'source_graph_impact', 'source_git_snapshot', 'source_git_ref', 'source_git_commit', 'source_git_parent', 'source_git_object', 'source_git_object_path', 'source_git_tree_entry', 'source_git_file_change', 'source_git_rename', 'source_git_hunk', 'source_git_changed_line', 'source_git_impact', 'registry_event', 'source_authority_fts'),
               ('register', 'resolve', 'verify', 'refresh_provenance'), 'source_identities_versions_locators_and_sector_references',
               'src/evidence_lane_plugin/source_authority.py', aliases=('source_authority',)),
    _authority('sessions', 'Sessions', ('sessions',), ('sessions_records', 'sessions_current', 'sessions_events'),
               ('boot', 'resume', 'status', 'close', 'verify'), 'session_heads_transitions_and_reported_host_bindings',
               'src/evidence_lane_plugin/session_authority.py', aliases=('session_authority',),
               pointer='Exact authenticated engine session head and reported host binding'),
    _authority('receipts', 'Receipts', ('receipts', 'access', 'extensions', 'accelerator', 'recovery', 'remote', 'hostmemory', 'gitpush', 'capture', 'storage'),
               ('receipts', 'gitpush_events', 'gitpush_current'), ('append', 'query', 'verify'), 'operations_results_grants_runtime_evidence_and_commit_references',
               'authorities/receipt_ledger/schema.sql', aliases=('receipt_ledger',)),
    _authority('universe', 'Universe', ('universe', 'federation'), ('universe_link_events', 'universe_links',
               'federation_identity', 'federation_members', 'federation_member_history', 'federation_mini_brains',
               'federation_lane_heads', 'federation_grants', 'federation_revocations', 'federation_links'),
               ('inspect', 'refresh', 'link_project', 'query_integrity'), 'root_pv_lane_heads_integrity_and_hash_only_project_links',
               'authorities/project_universe/schema.sql', aliases=('project_universe',)),
)
_TABLEAU_ADAPTATION = {
    'parser_id': 'tableau_closed_xml_native_hyper_v4', 'chunker_version': 'typed_item_text_4096_v4', 'fts_table': 'tableau_chunk_fts',
    'schema_contract': ('tableau_file', 'tableau_version', 'tableau_current', 'tableau_structure',
        'tableau_workbook', 'tableau_datasource', 'tableau_sheet', 'tableau_dashboard', 'tableau_story',
        'tableau_column', 'tableau_calculation', 'tableau_relationship', 'tableau_connection', 'tableau_filter',
        'tableau_parameter', 'tableau_mark', 'tableau_layout', 'tableau_package_member', 'tableau_hyper_schema',
        'tableau_hyper_table', 'tableau_hyper_column', 'tableau_hyper_row', 'tableau_opaque',
        'tableau_chunk', 'tableau_chunk_fts', 'tableau_export'),
    'operations': ('index', 'refresh', 'query', 'read_bytes', 'read_package_member', 'generate_hyper', 'edit_xml', 'export'),
    'pointer_semantics': 'Exact snapshot and XML locators or native Hyper schema/table identities; samples and layout metadata are labeled',
}
_ADDITIONAL_SECTORS = tuple(replace(lane, **_TABLEAU_ADAPTATION) if lane.canonical_lane_id == 'tableau' else lane for lane in _ADDITIONAL_SECTORS)
_POWERBI_ADAPTATION = {
    'parser_id': 'powerbi_pbir_schema_tom_pbixray_v4', 'chunker_version': 'typed_item_text_4096_v4', 'fts_table': 'powerbi_chunk_fts',
    'schema_contract': ('powerbi_file', 'powerbi_version', 'powerbi_current', 'powerbi_structure',
        'powerbi_project', 'powerbi_report', 'powerbi_page', 'powerbi_visual', 'powerbi_model', 'powerbi_table',
        'powerbi_column', 'powerbi_measure', 'powerbi_relationship', 'powerbi_partition', 'powerbi_expression',
        'powerbi_role', 'powerbi_hierarchy', 'powerbi_data_source', 'powerbi_perspective', 'powerbi_culture',
        'powerbi_annotation', 'powerbi_bookmark', 'powerbi_filter', 'powerbi_theme', 'powerbi_model_metadata',
        'powerbi_model_row', 'powerbi_reference', 'powerbi_package_member', 'powerbi_opaque',
        'powerbi_chunk', 'powerbi_chunk_fts', 'powerbi_export'),
    'operations': ('index', 'refresh', 'query', 'read_bytes', 'read_package_member', 'read_original_member',
        'generate_bim_or_pbir_project', 'edit_model_or_pbir_members', 'export'),
    'natural_extensions': ('.pbip', '.pbir', '.pbism', '.bim', '.tmdl', '.pbix', '.pbit', '.zip'),
    'pointer_semantics': 'Exact snapshot and report/model locators; source JSON versus native canonical model metadata and bounded stored rows are distinguished.',
}
_ADDITIONAL_SECTORS = tuple(replace(lane, **_POWERBI_ADAPTATION) if lane.canonical_lane_id == 'power_bi' else lane for lane in _ADDITIONAL_SECTORS)
_DEFINITIONS = (*_AUTHORITIES, *(replace(lane,
    schema_owners=(*lane.schema_owners, lane.canonical_lane_id.replace('_', '') + 'selector'),
    schema_contract=(*lane.schema_contract, 'selector_retirement'))
    for lane in (*_SECTORS, *_ADDITIONAL_SECTORS)))
CANONICAL_LANE_IDS = tuple(lane.canonical_lane_id for lane in _DEFINITIONS)
AUTHORITY_LANE_IDS = tuple(lane.canonical_lane_id for lane in _AUTHORITIES)
SECTOR_LANE_IDS = tuple(lane.canonical_lane_id for lane in (*_SECTORS, *_ADDITIONAL_SECTORS))
RETIRED_LANE_IDS = frozenset({'discussion', 'analysis', 'mode', 'brain_loader', 'project_engulf',
                            'sqlite_brain', 'project_overlay', 'connector_brain', 'accepted'}) | RETIRED_OFFICE_LANE_IDS


def _normalize(value: str) -> str:
    normalized = re.sub(r'[^a-z0-9]+', '_', value.strip().lower()).strip('_')
    if not normalized:
        raise LaneRegistryError('A lane alias must contain an alphanumeric character.')
    return normalized


def _validate():
    registry, aliases, paths, owners = {}, {}, set(), {}
    for lane in _DEFINITIONS:
        if lane.canonical_lane_id in registry or not re.fullmatch(r'[a-z][a-z0-9_]*', lane.canonical_lane_id):
            raise LaneRegistryError(f'Invalid or duplicate lane ID: {lane.canonical_lane_id}')
        if lane.canonical_lane_id in RETIRED_LANE_IDS or lane.kind not in {'authority', 'sector'}:
            raise LaneRegistryError(f'Unsupported lane: {lane.canonical_lane_id}')
        path = PurePosixPath(lane.database_relative_path)
        if path.is_absolute() or '..' in path.parts or str(path).casefold() in paths:
            raise LaneRegistryError(f'Invalid or colliding lane database: {path}')
        paths.add(str(path).casefold())
        for owner in lane.schema_owners:
            if owner in owners:
                raise LaneRegistryError(f'Schema owner belongs to more than one lane: {owner}')
            owners[owner] = lane.canonical_lane_id
        registry[lane.canonical_lane_id] = lane
        for alias in lane.aliases:
            aliases.setdefault(_normalize(alias), set()).add(lane.canonical_lane_id)
    ambiguous = {k: v for k, v in aliases.items() if len(v) > 1 and not (k == 'code' and v == PRIMARY_CODE_LANES)}
    if ambiguous:
        raise LaneRegistryError(f'Ambiguous lane aliases: {ambiguous}')
    return MappingProxyType(registry), MappingProxyType({k: tuple(sorted(v)) for k, v in aliases.items()}), MappingProxyType(owners)


LANE_REGISTRY, LANE_ALIAS_INDEX, SCHEMA_OWNER_LANES = _validate()

# Preserve the admitted Sources registry's names. These prefixes belong to one
# owner in one database; they do not grant other owners access to its tables.
SCHEMA_OWNER_PREFIXES = MappingProxyType({'sources': ('source_', 'intake_', 'registry_', 'sources_'),
                                        'artifacts': ('artifact_', 'project_artifact'),
                                        'pdfocr': ('pdf_', 'docling_'),
                                        'localcode': ('code_',), 'githubcode': ('code_',), 'docs': ('doc_', 'docling_'),
                                        'dataexcel': ('sheet_',), 'msaccess': ('access_',),
                                        **{lane_id.replace('_', '') + 'selector': ('selector_',)
                                           for lane_id in SECTOR_LANE_IDS}})


def owns_schema_object(owner: str, name: str) -> bool:
    return name.startswith(SCHEMA_OWNER_PREFIXES.get(owner, (owner + '_',)))


def resolve_lane_id(alias: str, *, code_mode: str | None = None) -> str:
    if is_named_custom_lane(alias):
        return alias
    matches = LANE_ALIAS_INDEX.get(_normalize(alias))
    if not matches:
        raise LaneRegistryError(f'Unknown or removed lane: {alias!r}')
    if len(matches) == 1:
        return matches[0]
    if code_mode in matches:
        return str(code_mode)
    raise LaneRegistryError('The code alias requires an explicit local_code or github_code mode.')


def get_lane(alias: str, *, code_mode: str | None = None) -> LaneDefinition:
    if is_named_custom_lane(alias):
        return replace(LANE_REGISTRY['custom'], canonical_lane_id=alias,
                       display_label=alias.removeprefix('custom__'), aliases=(alias,),
                       command=alias)
    return LANE_REGISTRY[resolve_lane_id(alias, code_mode=code_mode)]


CUSTOM_INSTANCE_PATTERN = r'custom__[a-z][a-z0-9_]{0,39}'
MAX_CUSTOM_INSTANCES = 256


def is_named_custom_lane(lane_id: str) -> bool:
    """Validate a canonical instance identity without changing the global catalog."""
    return isinstance(lane_id, str) and re.fullmatch(CUSTOM_INSTANCE_PATTERN, lane_id) is not None


def lane_family(lane_id: str) -> str:
    return 'custom' if is_named_custom_lane(lane_id) else lane_id


def lane_for_schema_owner(owner: str) -> LaneDefinition:
    try:
        return LANE_REGISTRY[SCHEMA_OWNER_LANES[owner]]
    except KeyError:
        raise LaneRegistryError(f'No lane owns schema {owner!r}; select a registered owner.') from None


def route_source(relative_path: str, *, code_mode: str, explicit_lane: str | None = None) -> str:
    """Resolve one source to one sector; source filenames never confer authority."""
    if code_mode not in PRIMARY_CODE_LANES:
        raise LaneRegistryError('code_mode must be github_code or local_code')
    if explicit_lane:
        lane = get_lane(explicit_lane, code_mode=code_mode)
        if lane.kind != 'sector':
            raise LaneRegistryError('Source intake cannot write an authority lane; use its owning workflow.')
        return lane.canonical_lane_id
    normalized = relative_path.replace('\\', '/').lower()
    filename, suffix = Path(normalized).name, Path(normalized).suffix
    if filename in _CODE_MANIFEST_NAMES:
        return code_mode
    # Specialty formats precede document and code fallbacks. JSON project/model
    # files need an explicit profile or bounded package inspection, not guessing.
    order = ('tableau',
             'power_bi', 'pdf_ocr', 'images_ocr', 'data_excel', 'ppt', 'data')
    for lane_id in order:
        if suffix in LANE_REGISTRY[lane_id].extensions:
            return lane_id
    if suffix in ('.db', '.sqlite', '.sqlite3'):
        return 'custom'
    if suffix in _CODE_EXTENSIONS:
        return code_mode
    if suffix in LANE_REGISTRY['docs'].extensions:
        return 'docs'
    return 'custom'


def route_batch(paths: Iterable[str], *, code_mode: str, overrides: dict[str, str] | None = None) -> dict[str, str]:
    exact = overrides or {}
    return {path: route_source(path, code_mode=code_mode, explicit_lane=exact.get(path)) for path in sorted(set(paths))}


def lane_schema_asset(lane_id: str) -> dict[str, Any]:
    lane = get_lane(lane_id)
    result = {'schema': LANE_SCHEMA_REGISTRY_SCHEMA, 'lane_id': lane.canonical_lane_id,
                  'kind': lane.kind, 'schema_id': f'evidence-lane.{lane.kind}.{lane.canonical_lane_id}.v4',
                  'schema_version': 4, 'database_relative_path': lane.database_relative_path,
                  'schema_history_relative_path': lane.schema_history_relative_path,
                  'owners': list(lane.schema_owners), 'tables': list(lane.schema_contract),
                  'prefixes_by_owner': {owner: list(SCHEMA_OWNER_PREFIXES.get(owner, (owner + '_',))) for owner in lane.schema_owners},
                  'original_schema_sources': list(lane.source_origins),
                  'qualification': 'definition_only_until_owning_workflow_verified',
                  'migration_rule': 'Lane-owned ordered digest-verified history; explicit migration preserves source state'}
    result['contract_sha256'] = sha256_bytes(canonical_json_bytes(result))
    return result


def lane_artifact_contract(lane_id: str) -> dict[str, Any]:
    lane = get_lane(lane_id)
    result = {'schema': LANE_ARTIFACT_ROLE_REGISTRY_SCHEMA, 'lane_id': lane.canonical_lane_id,
                  'folder': lane.folder, 'objects_root': lane.files_relative_path,
                  'required_roles': [{'role_id': 'sqlite_authority', 'path': lane.sqlite_filename}],
                  'schema_history': {'path': 'schema-history.v4.json', 'condition': 'migration_applied'},
                  'natural_artifacts': {'extensions': list(lane.natural_extensions), 'condition': 'declared_operation_produced_output'},
                  'relationship_views': list(lane.relationship_views),
                  'graph_exports': {'condition': 'declared_consumer_requested_view', 'mmd': lane.mmd_filename,
                                     'dot': lane.dot_filename, 'both_required': False},
                  'pointer': {'condition': 'declared_view_consumer', 'meaning': lane.pointer_semantics},
                  'refresh': 'Invalidate the affected lane view when its source or contract changes; publish only at its verified lane head',
                  'validation': 'Bind project/lane/head/contract/hash/scope; enforce semantic agreement for equivalent views only',
                  'toolchain': 'Shared installations selected by operation; no mandatory tools.json per lane'}
    result['contract_sha256'] = sha256_bytes(canonical_json_bytes(result))
    return result


def lane_schema_evolution_contract(lane_id: str) -> dict[str, Any]:
    lane = get_lane(lane_id)
    return {'schema': LANE_SCHEMA_EVOLUTION_POLICY_SCHEMA, 'lane_id': lane.canonical_lane_id,
                'database': lane.database_relative_path, 'history': lane.schema_history_relative_path,
                'immutable_applied_migrations': True, 'source_preserving_migration': True,
                'publication': 'project evidence head coordinator selects verified heads after the coordinated commit',
                'implicit_legacy_migration': False}


def lane_schema_registry_contract() -> dict[str, Any]:
    return {'schema': LANE_SCHEMA_REGISTRY_SCHEMA, 'registry_version': 4,
                'authorities': list(AUTHORITY_LANE_IDS), 'sectors': list(SECTOR_LANE_IDS),
                'instance_templates': {'custom': {'canonical_id_pattern': CUSTOM_INSTANCE_PATTERN,
                    'max_instances_per_project': MAX_CUSTOM_INSTANCES, 'registration_authority': 'sources',
                    'registration_owner': 'customlanes', 'global_catalog_mutated': False,
                    'schema_owner_requires_explicit_instance_store': True,
                    'storage': '<instance_id>/<instance_id>_sector_v001.sqlite with lazy objects and direct schema-history.v4.json'}},
                'root_pv': 'Project identity and exact lane-head references only; no universal business database',
                'coordination': {'writer_scope': 'one_project', 'schema_owners': ['writer'],
                                 'shared_lane_mechanisms': ['views'],
                                 'shared_mechanism_requires_explicit_lane': True,
                                 'business_records_in_root': False,
                                 'unpublished_lane_changes': 'recoverable_until_coordinated_root_publication'},
                'lanes': [lane_schema_asset(lane) for lane in CANONICAL_LANE_IDS]}


def catalog() -> list[dict[str, Any]]:
    return [lane.as_dict() for lane in _DEFINITIONS]


LANE_SCHEMA_REGISTRY_SHA256 = sha256_bytes(canonical_json_bytes(lane_schema_registry_contract()))
LANE_ARTIFACT_ROLE_REGISTRY_SHA256 = sha256_bytes(canonical_json_bytes([lane_artifact_contract(k) for k in CANONICAL_LANE_IDS]))
LANE_SCHEMA_EVOLUTION_POLICY_SHA256 = sha256_bytes(canonical_json_bytes([lane_schema_evolution_contract(k) for k in CANONICAL_LANE_IDS]))

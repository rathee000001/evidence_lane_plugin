export type LaneToolIcon =
  | "database"
  | "docker"
  | "git"
  | "media"
  | "node"
  | "package"
  | "pulse"
  | "python"
  | "terminal";

export type LaneTool = {
  name: string;
  icon: LaneToolIcon;
  role: string;
  availability: "required" | "optional";
};

export type LaneRuntimeContract = {
  command: string;
  mutation: "automatic_append_only" | "explicit_named_one_turn_grant_receipt_snapshot_relock";
  tools: readonly LaneTool[];
  schemaAdditions: readonly string[];
};

export const universalLaneSchema = [
  "lane_meta",
  "lane_pointer",
  "source_registry",
  "source_tombstone",
  "chunk_index",
  "chunk_content_cas",
  "chunk_history",
  "structured_fact",
  "parser_capability",
  "tfidf_term",
  "tfidf_vector",
  "refresh_receipt",
  "mutation_receipt",
] as const;

const coreTools: readonly LaneTool[] = [
  { name: "hashlib / pathlib", icon: "python", role: "exact source hashing", availability: "required" },
  { name: "SQLite FTS5", icon: "database", role: "facts, BM25, and receipts", availability: "required" },
  { name: "TF-IDF", icon: "pulse", role: "deterministic term statistics", availability: "required" },
  { name: "Mermaid emitter", icon: "media", role: "human-readable topology", availability: "required" },
  { name: "Graphviz DOT emitter", icon: "media", role: "machine-comparable topology", availability: "required" },
  { name: "mmdc", icon: "terminal", role: "derived Mermaid render", availability: "optional" },
  { name: "Graphviz dot", icon: "terminal", role: "derived DOT render", availability: "optional" },
];

const structuredTools: readonly LaneTool[] = [
  { name: "Python parser", icon: "python", role: "bounded structural extraction", availability: "required" },
  { name: "Package sealer", icon: "package", role: "four-file lane package", availability: "required" },
  ...coreTools,
];

const codeSchemaAdditions = [
  "sector_meta", "sector_head", "artifact_registry", "relation_edge",
  "code_source_registry", "code_file_snapshot", "code_chunk", "code_symbol",
  "code_import", "code_route", "code_dependency", "code_route_api_boundary",
  "code_config_build_test_chunk", "code_index_checkpoint", "code_source_active_head",
  "code_workflow_edge", "code_semantic_diff", "code_synthetic_snapshot_file",
  "code_snapshot_history", "code_good_snapshot", "snapshot_git_bridge",
  "git_commit_registry", "git_commit_parent", "git_file_change", "git_patch_hunk",
  "git_exact_line_change", "git_ref_registry", "git_push_event", "git_route_impact",
  "git_symbol_impact", "git_dependency_impact", "git_test_impact", "git_artifact_impact",
  "git_blob_cas", "git_content_chunk_cas", "git_chunk_occurrence", "git_history_fts",
  "code_chunk_fts",
] as const;

const codeTools: readonly LaneTool[] = [
  { name: "Git", icon: "git", role: "refs, commits, parents, blobs, and changes", availability: "required" },
  { name: "Python", icon: "python", role: "polyglot source projection", availability: "required" },
  { name: "SQLite / CAS", icon: "database", role: "history and semantic index", availability: "required" },
  { name: "Node / TypeScript", icon: "node", role: "JS/TS manifests and routes", availability: "required" },
  { name: "pytest", icon: "terminal", role: "executable contract tests", availability: "required" },
  { name: "Ruff", icon: "terminal", role: "Python quality gate", availability: "required" },
  { name: "MyPy", icon: "terminal", role: "typed source gate", availability: "required" },
  { name: "Package sealer", icon: "package", role: "candidate and receipts", availability: "required" },
  ...coreTools,
];

export const laneRuntimeContracts: Readonly<Record<string, LaneRuntimeContract>> = {
  github_code: {
    command: "evi-source-intake --lane github_code",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: codeTools,
    schemaAdditions: codeSchemaAdditions,
  },
  local_code: {
    command: "evi-source-intake --lane local_code",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: codeTools,
    schemaAdditions: codeSchemaAdditions,
  },
  chat_lineage: {
    command: "evi-source-intake --lane chat_lineage",
    mutation: "automatic_append_only",
    tools: [
      { name: "Secret redactor", icon: "pulse", role: "credential-shaped value removal", availability: "required" },
      { name: "Hash-chain writer", icon: "package", role: "idempotent visible-turn commit", availability: "required" },
      ...structuredTools,
    ],
    schemaAdditions: [
      "writeback_policy", "turn_prepare", "prompt_raw_exact", "prompt_normalized_summary",
      "response_raw_visible_exact", "response_summary", "visible_reasoning_summary",
      "file_link_registry", "source_normalization_receipt", "mode_classification_run",
      "gate_evaluation_run", "operator_activation_run", "entry_exit_receipt",
      "legacy_project_carry_forward", "turn_commit", "lineage_head", "state_hash_chain", "turn_fts",
    ],
  },
  discussion: {
    command: "evi-source-intake --lane discussion",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: structuredTools,
    schemaAdditions: [
      "discussion_source", "discussion_turn", "discussion_item", "discussion_decision",
      "discussion_delta", "discussion_next_action", "discussion_hard_gate",
      "discussion_artifact_reference", "discussion_fts",
    ],
  },
  analysis: {
    command: "evi-source-intake --lane analysis",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: structuredTools,
    schemaAdditions: [
      "analysis_source", "analysis_claim", "analysis_evidence", "analysis_supporting_evidence",
      "analysis_risk", "analysis_alternative", "analysis_open_question",
      "analysis_accepted_decision", "analysis_blocked_item", "analysis_fts",
    ],
  },
  plan: {
    command: "evi-source-intake --lane plan",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: structuredTools,
    schemaAdditions: [
      "plan_source", "plan_phase", "plan_milestone", "plan_task", "plan_owner",
      "plan_status", "plan_dependency", "plan_blocker", "plan_next_action",
      "plan_acceptance_criteria", "plan_fts",
    ],
  },
  mode: {
    command: "evi-mode",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "ENV/UOP classifier", icon: "pulse", role: "ordered law intersection", availability: "required" },
      { name: "PCM / MBA operators", icon: "terminal", role: "Code-mode execution governance", availability: "required" },
      ...structuredTools,
    ],
    schemaAdditions: [
      "mode_source", "mode_scope", "mode_trigger", "mode_rule", "mode_gate",
      "mode_allowed_action", "mode_blocked_action", "mode_response_template",
      "mode_priority", "mode_supersede_ledger", "mode_fts",
    ],
  },
  docs: {
    command: "evi-source-intake --lane docs",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "DOCX OpenXML", icon: "package", role: "hierarchy, text, and tables", availability: "required" },
      { name: "defusedxml", icon: "pulse", role: "safe XML parsing", availability: "required" },
      ...structuredTools,
    ],
    schemaAdditions: [
      "doc_file", "doc_structure", "doc_heading", "doc_paragraph", "doc_table_extract",
      "doc_chunk", "doc_image_reference", "source_structure_signature", "doc_fts",
    ],
  },
  data_excel: {
    command: "evi-source-intake --lane data_excel",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "OpenXML / CSV / JSON", icon: "package", role: "required structural extraction", availability: "required" },
      { name: "openpyxl", icon: "python", role: "workbook fidelity", availability: "optional" },
      { name: "pandas", icon: "python", role: "tabular inspection", availability: "optional" },
      { name: "python-calamine", icon: "python", role: "legacy Excel extraction", availability: "optional" },
      { name: "pyarrow", icon: "database", role: "Parquet schema and samples", availability: "optional" },
      ...coreTools,
    ],
    schemaAdditions: [
      "data_source", "sheet_workbook", "sheet_tab", "sheet_range", "sheet_cell_sample",
      "sheet_table", "sheet_formula", "sheet_formula_dependency_edge", "sheet_chart_metadata",
      "csv_header", "csv_row_sample", "json_structure", "json_record_sample",
      "parquet_schema", "parquet_row_sample", "data_chunk", "data_structure_signature", "data_fts",
    ],
  },
  ppt: {
    command: "evi-source-intake --lane ppt",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "PPTX OpenXML", icon: "media", role: "slides, notes, shapes, and relations", availability: "required" },
      { name: "defusedxml", icon: "pulse", role: "safe relationship parsing", availability: "required" },
      ...structuredTools,
    ],
    schemaAdditions: [
      "ppt_file", "ppt_slide", "ppt_shape", "ppt_text_block", "ppt_notes", "ppt_table",
      "ppt_image_reference", "ppt_slide_relationship", "ppt_chunk", "ppt_structure_signature", "ppt_fts",
    ],
  },
  pdf_ocr: {
    command: "evi-source-intake --lane pdf_ocr",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "pypdf", icon: "media", role: "native PDF text and embedded-image extraction", availability: "required" },
      { name: "pypdfium2", icon: "media", role: "full-page PDF raster fallback", availability: "required" },
      { name: "pdfplumber", icon: "media", role: "structural PDF extraction", availability: "required" },
      { name: "RapidOCR + ONNX", icon: "pulse", role: "local OCR", availability: "optional" },
      { name: "pytesseract + Tesseract", icon: "terminal", role: "secondary local OCR", availability: "optional" },
      { name: "Pillow", icon: "media", role: "page image handling", availability: "optional" },
      { name: "Poppler / Ghostscript", icon: "terminal", role: "local PDF fallbacks", availability: "optional" },
      ...coreTools,
    ],
    schemaAdditions: [
      "pdf_file", "pdf_page", "pdf_text_block", "pdf_image_block", "pdf_ocr_run",
      "pdf_ocr_block", "pdf_ocr_line", "pdf_review_region", "pdf_structure_signature", "pdf_fts",
    ],
  },
  images_ocr: {
    command: "evi-source-intake --lane images_ocr",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "Pillow", icon: "media", role: "pixel and metadata inspection", availability: "required" },
      { name: "RapidOCR + ONNX", icon: "pulse", role: "local OCR", availability: "optional" },
      { name: "pytesseract + Tesseract", icon: "terminal", role: "secondary local OCR", availability: "optional" },
      { name: "OpenCV", icon: "media", role: "optional region preprocessing", availability: "optional" },
      ...coreTools,
    ],
    schemaAdditions: [
      "image_file", "image_metadata", "image_ocr_run", "image_ocr_block",
      "image_ocr_line", "image_review_region", "image_ocr_fts",
    ],
  },
  artifacts: {
    command: "evi-source-intake --lane artifacts",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: structuredTools,
    schemaAdditions: [
      "project_artifact", "artifact_metadata", "artifact_text_extract",
      "artifact_relation_edge", "artifact_review_required", "artifact_fts",
    ],
  },
  custom: {
    command: "evi-source-intake --lane custom",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "Custom schema compiler", icon: "node", role: "bounded user-defined contract", availability: "required" },
      ...structuredTools,
    ],
    schemaAdditions: [
      "custom_source", "custom_item", "custom_evidence", "custom_decision",
      "custom_next_action", "custom_fts",
    ],
  },
  brain_loader: {
    command: "evi-source-intake --lane brain_loader",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "SQLite immutable URI", icon: "database", role: "read-only package inspection", availability: "required" },
      { name: "Safe archive intake", icon: "package", role: "member and manifest verification", availability: "required" },
      ...coreTools,
    ],
    schemaAdditions: [
      "brain_loader_source", "brain_loader_package", "brain_loader_member",
      "brain_loader_manifest", "brain_loader_pointer", "brain_loader_database",
      "brain_loader_schema_object", "brain_loader_relationship", "brain_loader_receipt", "brain_loader_fts",
    ],
  },
  research: {
    command: "evi-source-intake --lane research",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "Citation binder", icon: "package", role: "source/date/claim evidence", availability: "required" },
      ...structuredTools,
    ],
    schemaAdditions: [
      "research_source", "research_question", "research_hypothesis", "research_method",
      "research_evidence", "research_finding", "research_limitation", "research_citation",
      "research_open_question", "research_receipt", "research_fts",
    ],
  },
  project_engulf: {
    command: "evi-source-intake --lane project_engulf",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "Project inventory", icon: "package", role: "files, components, and conflicts", availability: "required" },
      { name: "Git detector", icon: "git", role: "repository identity when present", availability: "optional" },
      ...structuredTools,
    ],
    schemaAdditions: [
      "project_engulf_source", "project_engulf_file", "project_engulf_component",
      "project_engulf_relationship", "project_engulf_conflict", "project_engulf_sector_target",
      "project_engulf_chunk", "project_engulf_origin", "project_engulf_schema_mapping",
      "project_engulf_object_decision", "project_engulf_run", "project_engulf_topology_update",
      "project_engulf_receipt", "project_engulf_fts",
    ],
  },
  sqlite_brain: {
    command: "evi-source-intake --lane sqlite_brain",
    mutation: "explicit_named_one_turn_grant_receipt_snapshot_relock",
    tools: [
      { name: "SQLite immutable URI", icon: "database", role: "integrity, schema, FK, and FTS inspection", availability: "required" },
      { name: "Compatibility mapper", icon: "pulse", role: "version and sector mapping", availability: "required" },
      ...coreTools,
    ],
    schemaAdditions: [
      "loaded_sqlite_brain_source", "loaded_sqlite_brain_database",
      "loaded_sqlite_brain_schema_object", "loaded_sqlite_brain_table_stat",
      "loaded_sqlite_brain_foreign_key", "loaded_sqlite_brain_fts_table",
      "loaded_sqlite_brain_relationship", "loaded_sqlite_brain_integrity_result",
      "loaded_sqlite_brain_package_pointer", "loaded_sqlite_brain_compatibility",
      "loaded_sqlite_brain_sector_mapping", "loaded_sqlite_brain_receipt", "loaded_sqlite_brain_fts",
    ],
  },
};

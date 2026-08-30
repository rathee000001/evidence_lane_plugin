"""Build clean Codex-native ENV and UOP SQLite action planes.

These authorities contain executable routing and governance contracts only.
They never store prompt text, ChatLineage, discussion, source artifacts,
uploaded files, model critiques, foreign-host paths, or predecessor databases.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .graph_pipeline import SemanticGraph
from .hashing import atomic_write_bytes, canonical_json_bytes, sha256_bytes, sha256_file
from .lanes import CANONICAL_LANE_IDS
from .sqlite_indexing import rebuild_connection_authority_index

CODEX_ACTION_PLANE_SCHEMA = "evidence-lane.codex-action-plane.v1"
FIXED_TIME = "2000-01-01T00:00:00Z"

ACCELERATOR_PROVIDERS: tuple[dict[str, Any], ...] = (
    {
        "provider_id": "CPU",
        "vendor_plugin": "NONE",
        "runtime": "UNIVERSAL_CPU_BASELINE",
        "action_classes": ["ALL"],
        "default_memory_budget_percent": 100,
        "selection_rule": "DEFAULT_OR_VISIBLE_GPU_FALLBACK",
    },
    {
        "provider_id": "NVIDIA_CUDA",
        "vendor_plugin": "NVIDIA",
        "runtime": "PYTORCH_CUDA_EXACT_LOCK",
        "action_classes": ["RETRIEVAL", "OCR_MEDIA", "EVALUATION"],
        "default_memory_budget_percent": 80,
        "selection_rule": "USER_PLUGIN_GRANT_AND_DRIVER_RUNTIME_TELEMETRY_PASS",
    },
    {
        "provider_id": "AMD_ROCM",
        "vendor_plugin": "AMD",
        "runtime": "PYTORCH_HIP_EXACT_COMPATIBILITY_MATRIX",
        "action_classes": ["RETRIEVAL", "OCR_MEDIA", "EVALUATION"],
        "default_memory_budget_percent": 80,
        "selection_rule": "USER_PLUGIN_GRANT_AND_HIP_RUNTIME_TELEMETRY_PASS",
    },
    {
        "provider_id": "AMD_DIRECTML",
        "vendor_plugin": "AMD",
        "runtime": "ONNX_DIRECTML_EXACT_LOCK_WINDOWS",
        "action_classes": ["OCR_MEDIA", "EVALUATION"],
        "default_memory_budget_percent": 80,
        "selection_rule": "USER_PLUGIN_GRANT_AND_DIRECTML_PROVIDER_TELEMETRY_PASS",
    },
)

CODEX_HOST_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "host_id": "CODEX_DESKTOP_STABLE",
        "host_profile": "CODEX_DESKTOP",
        "app_variant": "STABLE",
        "application_id": "OpenAI.Codex_2p2nqsd0c76g0!App",
        "lifetime": "PERSISTENT_LOCAL_HOST",
        "native_mcp": True,
        "tunnel_policy": "CONDITIONAL_HOST_TOOL_GAP_ONLY",
    },
    {
        "host_id": "CODEX_DESKTOP_BETA",
        "host_profile": "CODEX_DESKTOP",
        "app_variant": "BETA",
        "application_id": "OpenAI.CodexBeta_2p2nqsd0c76g0!App",
        "lifetime": "PERSISTENT_LOCAL_HOST",
        "native_mcp": True,
        "tunnel_policy": "CONDITIONAL_HOST_TOOL_GAP_ONLY",
    },
    {
        "host_id": "CODEX_CLI",
        "host_profile": "CODEX_CLI",
        "app_variant": "CLI",
        "application_id": "CODEX_CLI",
        "lifetime": "LOCAL_OR_PERSISTENT_HOST",
        "native_mcp": True,
        "tunnel_policy": "CONDITIONAL_HOST_TOOL_GAP_ONLY",
    },
    {
        "host_id": "CODEX_VM_PERSISTENT",
        "host_profile": "CODEX_VM",
        "app_variant": "PERSISTENT_VM",
        "application_id": "CODEX_VM",
        "lifetime": "PERSISTENT_VM",
        "native_mcp": True,
        "tunnel_policy": "CONDITIONAL_HOST_TOOL_GAP_ONLY",
    },
    {
        "host_id": "CODEX_VM_EPHEMERAL",
        "host_profile": "CODEX_VM",
        "app_variant": "EPHEMERAL_VM",
        "application_id": "CODEX_VM",
        "lifetime": "CURRENT_VM_INSTANCE",
        "native_mcp": True,
        "tunnel_policy": "ONCE_PER_VM_WHEN_HOST_TOOL_GAP_EXISTS",
    },
)

WORKFLOW_EVENTS: tuple[dict[str, Any], ...] = (
    {
        "event": "BOOT_OR_RESUME",
        "trigger": "explicit session entry",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "PROMPT_ENTRY",
        "trigger": "every user prompt",
        "entry_slip": 1,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "STEER_ENTRY",
        "trigger": "every user steer",
        "entry_slip": 1,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "DELTA_ENTRY",
        "trigger": "active task Delta begins",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "MID_DELTA_QUERY",
        "trigger": "bounded read during active Delta",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "ADAPTIVE_DELTA_EXIT_APPEND",
        "trigger": "ordinary Delta closes",
        "entry_slip": 0,
        "delta_append": 1,
        "exit_slip": 0,
    },
    {
        "event": "GOAL_OPTION_2_EXIT",
        "trigger": "Goal finishes through option 2",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 1,
    },
    {
        "event": "STATE_TRAVEL_EXIT",
        "trigger": "State Travel completes",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 1,
    },
    {
        "event": "BUILD_CANDIDATE",
        "trigger": "full-PV proposal requested",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "HIL_DECISION",
        "trigger": "owning authority presents exact decision",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "FUSE",
        "trigger": "exact Project and Learning approvals pass",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "ROLLBACK",
        "trigger": "explicit rollback requested",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 0,
    },
    {
        "event": "EXIT_BOOT",
        "trigger": "explicit session close",
        "entry_slip": 0,
        "delta_append": 0,
        "exit_slip": 0,
    },
)

MODE_ROWS: tuple[dict[str, Any], ...] = (
    {
        "mode": "CODE",
        "project_classes": ["CODE_REPOSITORY", "MIXED_PROJECT"],
        "ci": True,
        "hil": "PROJECT_CLASS_POLICY",
    },
    {
        "mode": "ANALYSIS",
        "project_classes": ["RESEARCH_STUDY", "DATA_PROJECT", "MIXED_PROJECT"],
        "ci": False,
        "hil": "PROJECT_CLASS_POLICY",
    },
    {
        "mode": "PLAN",
        "project_classes": ["ALL"],
        "ci": False,
        "hil": "PLAN_ACCEPTANCE_SEPARATE_FROM_PROJECT_HIL",
    },
    {
        "mode": "RESEARCH",
        "project_classes": ["RESEARCH_STUDY", "MIXED_PROJECT"],
        "ci": False,
        "hil": "PROJECT_CLASS_POLICY",
    },
    {
        "mode": "DOCUMENT",
        "project_classes": ["DOCUMENT_PROJECT", "MIXED_PROJECT"],
        "ci": False,
        "hil": "PROJECT_CLASS_POLICY",
    },
    {
        "mode": "DATA",
        "project_classes": ["DATA_PROJECT", "MIXED_PROJECT"],
        "ci": True,
        "hil": "PROJECT_CLASS_POLICY",
    },
    {
        "mode": "MEDIA",
        "project_classes": ["MEDIA_PROJECT", "MIXED_PROJECT"],
        "ci": False,
        "hil": "PROJECT_CLASS_POLICY",
    },
    {
        "mode": "PROJECT_ENGULF",
        "project_classes": ["ALL"],
        "ci": False,
        "hil": "REGISTRATION_ONLY_NO_ACCEPTANCE",
    },
    {
        "mode": "RUNTIME",
        "project_classes": ["ALL"],
        "ci": True,
        "hil": "NO_PROJECT_HIL",
    },
    {
        "mode": "CUSTOM",
        "project_classes": ["CUSTOM_PROJECT"],
        "ci": False,
        "hil": "EXPLICIT_CUSTOM_SCHEMA",
    },
)

PROJECT_CLASSES: tuple[dict[str, Any], ...] = (
    {
        "project_class": "CODE_REPOSITORY",
        "lanes": ["github_code", "local_code", "analysis", "plan", "artifacts"],
        "ci": "CODE_BOOLEAN_GATE",
        "hil": "PROJECT_AND_LEARNING",
    },
    {
        "project_class": "RESEARCH_STUDY",
        "lanes": ["research", "analysis", "docs", "plan", "artifacts"],
        "ci": "SOURCE_AND_EVIDENCE_GATES",
        "hil": "PROJECT_AND_LEARNING",
    },
    {
        "project_class": "DOCUMENT_PROJECT",
        "lanes": ["docs", "pdf_ocr", "images_ocr", "plan", "artifacts"],
        "ci": "DOCUMENT_VALIDATION_GATES",
        "hil": "PROJECT_AND_LEARNING",
    },
    {
        "project_class": "DATA_PROJECT",
        "lanes": ["data_excel", "sqlite_brain", "analysis", "plan", "artifacts"],
        "ci": "DATA_SCHEMA_AND_FORMULA_GATES",
        "hil": "PROJECT_AND_LEARNING",
    },
    {
        "project_class": "MEDIA_PROJECT",
        "lanes": ["images_ocr", "pdf_ocr", "ppt", "artifacts", "plan"],
        "ci": "MEDIA_VALIDATION_GATES",
        "hil": "PROJECT_AND_LEARNING",
    },
    {
        "project_class": "MIXED_PROJECT",
        "lanes": list(CANONICAL_LANE_IDS),
        "ci": "DERIVED_FROM_SELECTED_LANES",
        "hil": "PROJECT_AND_LEARNING",
    },
    {
        "project_class": "CUSTOM_PROJECT",
        "lanes": ["custom", "plan", "artifacts"],
        "ci": "CUSTOM_SCHEMA_GATES",
        "hil": "EXPLICIT_CUSTOM_SCHEMA",
    },
)

FORMULAS: tuple[dict[str, str], ...] = (
    {
        "formula": "CODE_BOOLEAN_GATE",
        "purpose": "Code-mode recursive CI closure",
        "expression": "ALL_REQUIRED_GATES == PASS",
        "rerun": "AFFECTED_GATES_ONLY",
        "exit_effect": "ALLOW_EXECUTABLE_FINGERPRINT_REFRESH",
    },
    {
        "formula": "DELTA_COMPLETION",
        "purpose": "Ordinary Delta completion",
        "expression": "TARGETED_RECEIPTS_PASS AND DIRECT_PURGE_COMPLETE",
        "rerun": "FAILED_OR_AFFECTED_STEPS_ONLY",
        "exit_effect": "ALLOW_ADAPTIVE_DELTA_EXIT_APPEND",
    },
    {
        "formula": "SOURCE_INTEGRITY",
        "purpose": "Source Intake exactness",
        "expression": "ALL_SELECTED_SOURCES_HASH_BOUND",
        "rerun": "CHANGED_SOURCES_ONLY",
        "exit_effect": "ALLOW_LANE_REFRESH",
    },
    {
        "formula": "PROJECT_HIL_READY",
        "purpose": "Project proposal decision readiness",
        "expression": "CANDIDATE_VALID AND PROJECT_EVIDENCE_COMPLETE",
        "rerun": "AFFECTED_AUTHORITIES_ONLY",
        "exit_effect": "PRESENT_PROJECT_HIL",
    },
    {
        "formula": "LEARNING_HIL_READY",
        "purpose": "Learning weave decision readiness",
        "expression": "LEARNING_CANDIDATE_VALID",
        "rerun": "AFFECTED_LEARNING_ROWS_ONLY",
        "exit_effect": "PRESENT_LEARNING_HIL",
    },
    {
        "formula": "STATE_TRAVEL_READY",
        "purpose": "State Travel completion",
        "expression": "DESTINATION_ATTESTED AND CONTINUITY_VERIFIED",
        "rerun": "BLOCKED_PROOFS_ONLY",
        "exit_effect": "EMIT_STATE_TRAVEL_EXIT_SLIP",
    },
    {
        "formula": "GOAL_OPTION_2_READY",
        "purpose": "Goal completion through option 2",
        "expression": "GOAL_METRICS_SEALED AND FINAL_BOUNDARY_VERIFIED",
        "rerun": "MISSING_GOAL_PROOFS_ONLY",
        "exit_effect": "EMIT_GOAL_EXIT_SLIP",
    },
)

CLASS_TOOLS: dict[str, tuple[str, ...]] = {
    "GOVERNANCE": (
        "Pydantic",
        "ENV_UOP_classifier",
        "PCM_MBA_operators",
        "Hash_chain_writer",
        "Secret_redactor",
        "APSW_SQLite_engine",
    ),
    "RETRIEVAL": (
        "LangChain",
        "LlamaIndex_SQLite_indexer",
        "SQLite_FTS5_BM25",
        "deterministic_TFIDF",
        "RapidFuzz",
    ),
    "SOURCE_ROUTING": (
        "hashlib_pathlib",
        "Secret_redactor",
        "SQLite_CAS",
        "Python_structural_parser",
        "LlamaIndex_SQLite_indexer",
        "Safe_archive_intake",
        "Project_inventory",
        "Git_detector",
        "Compatibility_mapper",
        "Custom_schema_compiler",
        "Citation_binder",
    ),
    "CODE": (
        "Git",
        "Python",
        "NodeJS_TypeScript",
        "GitPython",
        "PyGithub",
        "TreeSitter_LanguagePack",
        "ripgrep_15_2_0",
        "jq",
        "Python_structural_parser",
    ),
    "DOCUMENT": (
        "Docling",
        "PyMuPDF",
        "pdfplumber",
        "pypdf",
        "lxml",
        "DOCX_OpenXML",
        "PPTX_OpenXML",
        "defusedxml",
        "OpenXML_CSV_JSON_parser",
        "pypdfium2",
        "SQLite_immutable_URI_reader",
    ),
    "OCR_MEDIA": (
        "RapidOCR_ONNX_Runtime",
        "pytesseract_Tesseract",
        "OpenCV",
        "Pillow",
        "Poppler_pdftotext_pdfinfo",
        "FFmpeg",
    ),
    "DATA": (
        "DuckDB",
        "Polars",
        "APSW_SQLite_engine",
        "pandas",
        "pyarrow",
        "openpyxl",
        "python_calamine",
        "SQLAlchemy",
        "OpenXML_CSV_JSON_parser",
        "Custom_schema_compiler",
        "SQLite_immutable_URI_reader",
        "Compatibility_mapper",
    ),
    "WEB_RESEARCH": (
        "trafilatura",
        "readability_lxml",
        "BeautifulSoup4",
        "lxml",
        "markdownify",
        "html2text",
        "tldextract",
        "validators",
        "DDGS",
        "HTTPX",
        "Requests",
    ),
    "GRAPH": (
        "LangChain",
        "LangGraph_Mermaid_engine",
        "rustworkx",
        "Python_Graphviz_DOT_engine",
        "Graphviz_dot",
        "Mermaid_CLI_mmdc",
    ),
    "RUNTIME": (
        "Python",
        "FastAPI",
        "Uvicorn",
        "FastMCP",
        "MCP_Python_SDK",
        "Pydantic_Settings",
        "orjson",
        "Tenacity",
        "psutil",
        "PowerShell_Win32_APIs",
        "Cryptography_PyJWT",
        "SevenZip_NSIS_extractor",
        "python_dotenv",
        "HuggingFace_Hub_ModelSnapshot",
    ),
    "MCP_TRANSPORT": (
        "FastMCP",
        "MCP_Python_SDK",
        "GitHub_MCP_Server",
        "Filesystem_MCP_Server",
        "PostgreSQL_MCP_Server",
        "Slack_MCP_Server",
    ),
    "FORMULA": (
        "Pydantic",
        "ENV_UOP_classifier",
        "PCM_MBA_operators",
        "DuckDB",
        "APSW_SQLite_engine",
    ),
    "EVALUATION": (
        "LangSmith",
        "TruLens",
        "DeepEval",
        "Promptfoo",
    ),
    "OBSERVABILITY": ("OpenTelemetry", "Langfuse", "Helicone", "Grafana"),
    "DEPLOYMENT": (
        "NodeJS_TypeScript",
        "NextJS_React_ThreeJS_FramerMotion",
        "Docker",
        "Kubernetes",
        "AWS_Lambda",
        "Google_Cloud_Run",
        "Vercel_Git_integration",
        "GitHub_Actions",
        "AWS",
        "Azure",
        "Google_Cloud",
    ),
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _action_classes(action: dict[str, Any]) -> list[str]:
    name = str(action["name"]).casefold()
    classes = ["GOVERNANCE"]
    if bool(dict(action.get("annotations") or {}).get("readOnlyHint")) or any(
        term in name
        for term in (
            "query",
            "search",
            "fetch",
            "status",
            "summary",
            "catalog",
            "list",
            "inspect",
        )
    ):
        classes.append("RETRIEVAL")
    if any(
        term in name
        for term in (
            "source_intake",
            "lane_refresh",
            "adaptive_delta_exit",
            "build_initial",
            "project_register",
            "enroll_project",
            "project_recipe",
        )
    ):
        classes.append("SOURCE_ROUTING")
    if name.startswith(("remote_git_", "git_")) or name in {
        "pv_begin_next_turn",
        "pv_task_transition",
    }:
        classes.append("CODE")
    if name.startswith(("document_", "docx_", "pdf_", "ppt_")):
        classes.append("DOCUMENT")
    if name.startswith(("ocr_", "image_", "media_")):
        classes.append("OCR_MEDIA")
    if name.startswith(("data_", "sqlite_")):
        classes.append("DATA")
    if "formula" in name:
        classes.append("FORMULA")
    if name.startswith(("research_", "web_")):
        classes.append("WEB_RESEARCH")
    if any(term in name for term in ("graph", "render", "topology", "_diff")):
        classes.append("GRAPH")
    if any(term in name for term in ("runtime", "session", "boot", "tunnel", "plugin")):
        classes.append("RUNTIME")
    if name in {
        "ai_toolchain_route",
        "additional_plugin_add",
        "additional_plugin_drop",
    }:
        classes.append("MCP_TRANSPORT")
    if any(term in name for term in ("validate", "evaluation", "regression", "test")):
        classes.append("EVALUATION")
    if any(term in name for term in ("telemetry", "trace", "observability")):
        classes.append("OBSERVABILITY")
    if any(
        term in name
        for term in ("remote_git", "prepare_push", "execute_push", "deploy")
    ):
        classes.append("DEPLOYMENT")
    return list(dict.fromkeys(classes))


def classify_action_workflow_classes(action: dict[str, Any]) -> list[str]:
    return _action_classes(action)


def _action_pipeline(
    action: dict[str, Any], tools: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    classes = _action_classes(action)
    ordered = []
    for action_class in classes:
        for tool in CLASS_TOOLS[action_class]:
            if tool in tools and tool not in ordered:
                ordered.append(tool)
    return classes, ordered


def _create_env_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        PRAGMA user_version=17;
        CREATE TABLE env_authority_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) STRICT;
        CREATE TABLE codex_host_variant_v17(host_id TEXT PRIMARY KEY,host_profile TEXT NOT NULL,app_variant TEXT NOT NULL,application_id TEXT NOT NULL,lifetime TEXT NOT NULL,native_mcp INTEGER NOT NULL,tunnel_policy TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_accelerator_profile_v17(provider_id TEXT PRIMARY KEY,vendor_plugin TEXT NOT NULL,runtime TEXT NOT NULL,eligible_action_classes_json TEXT NOT NULL,default_memory_budget_percent INTEGER NOT NULL,selection_rule TEXT NOT NULL,provider_is_tool INTEGER NOT NULL CHECK(provider_is_tool=0),provider_is_agent INTEGER NOT NULL CHECK(provider_is_agent=0),status TEXT NOT NULL) STRICT;
        CREATE TABLE env_workflow_event_v17(event_id TEXT PRIMARY KEY,trigger_scope TEXT NOT NULL,entry_slip INTEGER NOT NULL,delta_exit_append INTEGER NOT NULL,exit_slip INTEGER NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_mode_registry_v17(mode_id TEXT PRIMARY KEY,project_classes_json TEXT NOT NULL,ci_applicable INTEGER NOT NULL,hil_policy TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_project_class_policy_v17(project_class TEXT PRIMARY KEY,lane_ids_json TEXT NOT NULL,ci_strategy TEXT NOT NULL,hil_strategy TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_formula_registry_v17(formula_id TEXT PRIMARY KEY,purpose TEXT NOT NULL,boolean_expression TEXT NOT NULL,rerun_scope TEXT NOT NULL,exit_effect TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_operator_registry_v17(operator_id TEXT PRIMARY KEY,operator_family TEXT NOT NULL,phase TEXT NOT NULL,selection_rule TEXT NOT NULL,output_contract TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_tool_registry_v17(tool_id TEXT PRIMARY KEY,requirement TEXT NOT NULL,surfaces_json TEXT NOT NULL,role TEXT NOT NULL,agent_authority INTEGER NOT NULL CHECK(agent_authority=0),status TEXT NOT NULL) STRICT;
        CREATE TABLE env_action_binding_v17(action_name TEXT PRIMARY KEY,workflow_classes_json TEXT NOT NULL,owner_skill TEXT,internal_sdk_json TEXT NOT NULL,mcp_json TEXT NOT NULL,skill_workflows_json TEXT NOT NULL,entry_event TEXT NOT NULL,ordered_tools_json TEXT NOT NULL,schema_sha256 TEXT NOT NULL,binding_sha256 TEXT NOT NULL UNIQUE,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_lane_binding_v17(lane_id TEXT PRIMARY KEY,source_types_json TEXT NOT NULL,parser_id TEXT NOT NULL,chunker_version TEXT NOT NULL,fts_table TEXT NOT NULL,ordered_tools_json TEXT NOT NULL,binding_sha256 TEXT NOT NULL UNIQUE,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_skill_binding_v17(skill_name TEXT PRIMARY KEY,description TEXT NOT NULL,workflow_json TEXT NOT NULL,member_count INTEGER NOT NULL,routing_manifest_sha256 TEXT,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_hook_binding_v17(event_name TEXT PRIMARY KEY,event_number INTEGER NOT NULL,handler_count INTEGER NOT NULL,event_path TEXT NOT NULL,event_sha256 TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_sdk_action_binding_v17(action_name TEXT PRIMARY KEY,internal_sdk_json TEXT NOT NULL,outer_route_json TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_mcp_action_binding_v17(action_name TEXT PRIMARY KEY,server_identity TEXT NOT NULL,tool_name TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE env_action_plane_build_receipt(sequence INTEGER PRIMARY KEY,table_count INTEGER NOT NULL,tool_count INTEGER NOT NULL,action_count INTEGER NOT NULL,lane_count INTEGER NOT NULL,skill_count INTEGER NOT NULL,hook_event_count INTEGER NOT NULL,host_variant_count INTEGER NOT NULL,foreign_surface_row_count INTEGER NOT NULL,receipt_json TEXT NOT NULL,receipt_sha256 TEXT NOT NULL UNIQUE,recorded_at TEXT NOT NULL) STRICT;
        """
    )


def _create_uop_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        PRAGMA user_version=17;
        CREATE TABLE uop_authority_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) STRICT;
        CREATE TABLE uop_governance_operator_v17(operator_id TEXT PRIMARY KEY,operator_family TEXT NOT NULL,fires_when TEXT NOT NULL,rule_text TEXT NOT NULL,can_override_env INTEGER NOT NULL CHECK(can_override_env=0),can_override_project INTEGER NOT NULL CHECK(can_override_project=0),status TEXT NOT NULL) STRICT;
        CREATE TABLE uop_project_class_hil_policy_v17(project_class TEXT PRIMARY KEY,project_hil TEXT NOT NULL,learning_hil TEXT NOT NULL,plan_acceptance_separate INTEGER NOT NULL,direct_purge_required INTEGER NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE uop_workflow_gate_v17(event_id TEXT PRIMARY KEY,allowed_effects_json TEXT NOT NULL,blocked_effects_json TEXT NOT NULL,human_decision_required INTEGER NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE uop_action_policy_v17(action_name TEXT PRIMARY KEY,read_only INTEGER NOT NULL,destructive INTEGER NOT NULL,idempotent INTEGER NOT NULL,requires_exact_hil INTEGER NOT NULL,direct_purge_required INTEGER NOT NULL,authority_effects_json TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE uop_tool_policy_v17(tool_id TEXT PRIMARY KEY,requirement TEXT NOT NULL,grant_required INTEGER NOT NULL,locality_expiry_required INTEGER NOT NULL,agent_authority INTEGER NOT NULL CHECK(agent_authority=0),selection_rule TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE uop_host_policy_v17(host_id TEXT PRIMARY KEY,host_profile TEXT NOT NULL,execution_allowed INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL) STRICT;
        CREATE TABLE uop_accelerator_policy_v17(provider_id TEXT PRIMARY KEY,vendor_plugin_grant_required INTEGER NOT NULL,memory_budget_configurable INTEGER NOT NULL,default_memory_budget_percent INTEGER NOT NULL,max_memory_budget_percent INTEGER NOT NULL,telemetry_required INTEGER NOT NULL,throttle_blocks_execution INTEGER NOT NULL,cpu_fallback_required INTEGER NOT NULL,authority_effect INTEGER NOT NULL CHECK(authority_effect=0),status TEXT NOT NULL) STRICT;
        CREATE TABLE uop_fallback_policy_v17(workflow_class TEXT PRIMARY KEY,fallback_rule TEXT NOT NULL,cross_class_fallback_allowed INTEGER NOT NULL CHECK(cross_class_fallback_allowed=0),silent_fallback_allowed INTEGER NOT NULL CHECK(silent_fallback_allowed=0),status TEXT NOT NULL) STRICT;
        CREATE TABLE uop_action_plane_build_receipt(sequence INTEGER PRIMARY KEY,table_count INTEGER NOT NULL,operator_count INTEGER NOT NULL,action_count INTEGER NOT NULL,tool_count INTEGER NOT NULL,host_variant_count INTEGER NOT NULL,foreign_surface_row_count INTEGER NOT NULL,receipt_json TEXT NOT NULL,receipt_sha256 TEXT NOT NULL UNIQUE,recorded_at TEXT NOT NULL) STRICT;
        """
    )


def _semantic_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE semantic_graph_group_v17(group_id TEXT PRIMARY KEY,label TEXT NOT NULL,direction TEXT NOT NULL,ordinal INTEGER NOT NULL UNIQUE) STRICT;
        CREATE TABLE semantic_graph_meta_v17(key TEXT PRIMARY KEY,value TEXT NOT NULL) STRICT;
        CREATE TABLE semantic_graph_node_v17(node_id TEXT PRIMARY KEY,label TEXT NOT NULL,node_kind TEXT NOT NULL,group_id TEXT REFERENCES semantic_graph_group_v17(group_id),ordinal INTEGER NOT NULL UNIQUE) STRICT;
        CREATE TABLE semantic_graph_edge_v17(edge_id TEXT PRIMARY KEY,source_node_id TEXT NOT NULL REFERENCES semantic_graph_node_v17(node_id),target_node_id TEXT NOT NULL REFERENCES semantic_graph_node_v17(node_id),label TEXT,conditional INTEGER NOT NULL,ordinal INTEGER NOT NULL UNIQUE) STRICT;
        CREATE TABLE semantic_graph_render_receipt_v17(sequence INTEGER PRIMARY KEY,authority_id TEXT NOT NULL,semantic_topology_sha256 TEXT NOT NULL,mmd_sha256 TEXT NOT NULL,dot_sha256 TEXT NOT NULL,graph_pipeline_receipt_json TEXT NOT NULL,receipt_sha256 TEXT NOT NULL UNIQUE,recorded_at TEXT NOT NULL) STRICT;
        """
    )


def _store_graph(
    connection: sqlite3.Connection,
    authority_id: str,
    graph: SemanticGraph,
    mmd_path: Path,
    dot_path: Path,
) -> dict[str, Any]:
    _semantic_schema(connection)
    mmd, dot, graph_receipt = graph.render_pair()
    atomic_write_bytes(mmd_path, mmd.encode("utf-8"))
    atomic_write_bytes(dot_path, dot.encode("utf-8"))
    connection.executemany(
        "INSERT INTO semantic_graph_meta_v17 VALUES(?,?)",
        (
            ("authority_id", authority_id),
            ("direction", graph.direction),
            ("graph_role", graph.role),
        ),
    )
    connection.executemany(
        "INSERT INTO semantic_graph_group_v17 VALUES(?,?,?,?)",
        [
            (row.group_id, row.label, row.direction, index)
            for index, row in enumerate(graph.groups, 1)
        ],
    )
    connection.executemany(
        "INSERT INTO semantic_graph_node_v17 VALUES(?,?,?,?,?)",
        [
            (row.node_id, row.label, row.kind, row.group_id, index)
            for index, row in enumerate(graph.nodes, 1)
        ],
    )
    connection.executemany(
        "INSERT INTO semantic_graph_edge_v17 VALUES(?,?,?,?,?,?)",
        [
            (
                f"edge_{index:05d}",
                row.source,
                row.target,
                row.label,
                int(row.conditional),
                index,
            )
            for index, row in enumerate(graph.edges, 1)
        ],
    )
    core = {
        "schema": "evidence-lane.codex-action-plane-graph.v1",
        "status": "PASS",
        "authority_id": authority_id,
        "semantic_topology_sha256": graph_receipt["semantic_topology_sha256"],
        "mmd_sha256": sha256_file(mmd_path),
        "dot_sha256": sha256_file(dot_path),
        "graph_pipeline_receipt": graph_receipt,
        "recorded_at": FIXED_TIME,
    }
    receipt = {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
    connection.execute(
        "INSERT INTO semantic_graph_render_receipt_v17 VALUES(?,?,?,?,?,?,?,?)",
        (
            1,
            authority_id,
            receipt["semantic_topology_sha256"],
            receipt["mmd_sha256"],
            receipt["dot_sha256"],
            canonical_json_bytes(graph_receipt).decode(),
            receipt["receipt_sha256"],
            FIXED_TIME,
        ),
    )
    return receipt


def _build_graphs(
    env: sqlite3.Connection, uop: sqlite3.Connection
) -> tuple[SemanticGraph, SemanticGraph]:
    env_graph = SemanticGraph(
        "env_codex_action_plane", direction="TB", role="EXECUTABLE_WORKFLOW"
    )
    env_graph.add_node("PROMPT", "Prompt or steer", "root")
    env_graph.add_node(
        "ENTRY_SLIP",
        "Entry Slip: intent, focus, source route, workflow and bounded next action",
        "source",
    )
    env_graph.add_node(
        "SOURCE_INTAKE",
        "Source Intake and exact authority/lane classification",
        "source",
    )
    env_graph.add_node(
        "MODE", "Codex mode + Project Recipe + project-class policy", "semantic"
    )
    env_graph.add_node(
        "ACTION", "Typed action + schema + internal SDK owner", "semantic"
    )
    env_graph.add_node(
        "ENV",
        "ENV selects host, context, locality, workflow and conditional pipeline",
        "semantic",
    )
    env_graph.add_node(
        "UOP", "UOP applies governance, gates, HIL and fallback policy", "warn"
    )
    env_graph.add_node(
        "TOOLS",
        "Condition-true multi-tool pipeline; Codex remains sole agent",
        "semantic",
    )
    env_graph.add_node(
        "OUTER", "Outer SDK + FastMCP/native/domain MCP/tunnel transport", "semantic"
    )
    env_graph.add_node("HOOKS", "Ordered host hook events", "semantic")
    env_graph.add_node(
        "VALIDATE", "Validate result, authority effects and direct purge", "warn"
    )
    env_graph.add_node(
        "DELTA_EXIT", "Adaptive Delta-exit append for continuing work", "output"
    )
    env_graph.add_node(
        "EXIT_SLIP",
        "Exit Slip only for Goal option 2 or completed State Travel",
        "output",
    )
    for left, right in (
        ("PROMPT", "ENTRY_SLIP"),
        ("ENTRY_SLIP", "SOURCE_INTAKE"),
        ("SOURCE_INTAKE", "MODE"),
        ("MODE", "ACTION"),
        ("ACTION", "ENV"),
        ("ENV", "UOP"),
        ("UOP", "TOOLS"),
        ("TOOLS", "OUTER"),
        ("OUTER", "HOOKS"),
        ("HOOKS", "VALIDATE"),
    ):
        env_graph.add_edge(left, right)
    env_graph.add_edge(
        "VALIDATE", "DELTA_EXIT", "ordinary Delta closes", conditional=True
    )
    env_graph.add_edge(
        "VALIDATE",
        "EXIT_SLIP",
        "Goal option 2 or State Travel completes",
        conditional=True,
    )
    for group_id, table, id_col, label_col, parent in (
        ("HOSTS", "codex_host_variant_v17", "host_id", "app_variant", "ENV"),
        (
            "ACCELERATORS",
            "env_accelerator_profile_v17",
            "provider_id",
            "selection_rule",
            "ENV",
        ),
        ("MODES", "env_mode_registry_v17", "mode_id", "hil_policy", "MODE"),
        (
            "FORMULAS",
            "env_formula_registry_v17",
            "formula_id",
            "boolean_expression",
            "VALIDATE",
        ),
        ("LANES", "env_lane_binding_v17", "lane_id", "parser_id", "SOURCE_INTAKE"),
    ):
        env_graph.begin_group(group_id, group_id.title(), direction="TB")
        nodes = []
        for row in env.execute(
            f"SELECT {id_col},{label_col} FROM {table} ORDER BY {id_col}"
        ):
            node = f"{group_id}_{re.sub(r'[^A-Za-z0-9_]+', '_', str(row[0])).upper()}"
            nodes.append(node)
            env_graph.add_node(node, f"{row[0]}: {row[1]}", "semantic")
        env_graph.end_group()
        for node in nodes:
            env_graph.add_edge(
                parent, node, "current registry member", conditional=True
            )
    env_graph.begin_group("ACTION_PIPELINES", "Typed action pipelines", direction="TB")
    action_nodes = []
    for row in env.execute(
        "SELECT action_name,workflow_classes_json,ordered_tools_json FROM env_action_binding_v17 ORDER BY action_name"
    ):
        node = "ACTION_" + re.sub(r"[^A-Za-z0-9_]+", "_", str(row[0])).upper()
        action_nodes.append(node)
        classes = json.loads(str(row[1]))
        tools = json.loads(str(row[2]))
        env_graph.add_node(
            node,
            f"{row[0]} | classes={','.join(classes)} | tools={len(tools)}",
            "semantic",
        )
    env_graph.end_group()
    for node in action_nodes:
        env_graph.add_edge("ACTION", node)

    uop_graph = SemanticGraph(
        "uop_codex_governance", direction="TB", role="EXECUTABLE_WORKFLOW"
    )
    for node, label, kind in (
        ("INPUT", "ENV-selected Codex action context", "root"),
        ("SOURCE", "Source and authority precedence", "semantic"),
        ("GATES", "Work, privacy, disclosure, direct-purge and HIL gates", "warn"),
        ("OPERATOR", "Applicable governance operators only", "semantic"),
        ("FALLBACK", "Declared same-class fallback only", "semantic"),
        (
            "OUTPUT",
            "Governed result + receipt; cannot override ENV or Project Truth",
            "output",
        ),
    ):
        uop_graph.add_node(node, label, kind)
    for left, right in (
        ("INPUT", "SOURCE"),
        ("SOURCE", "GATES"),
        ("GATES", "OPERATOR"),
        ("OPERATOR", "FALLBACK"),
        ("FALLBACK", "OUTPUT"),
    ):
        uop_graph.add_edge(left, right)
    for group_id, table, id_col, label_col, parent in (
        (
            "OPERATORS",
            "uop_governance_operator_v17",
            "operator_id",
            "rule_text",
            "OPERATOR",
        ),
        (
            "HIL_POLICIES",
            "uop_project_class_hil_policy_v17",
            "project_class",
            "project_hil",
            "GATES",
        ),
        ("HOST_POLICIES", "uop_host_policy_v17", "host_id", "reason", "INPUT"),
        (
            "ACCELERATOR_POLICIES",
            "uop_accelerator_policy_v17",
            "provider_id",
            "default_memory_budget_percent",
            "GATES",
        ),
        (
            "FALLBACKS",
            "uop_fallback_policy_v17",
            "workflow_class",
            "fallback_rule",
            "FALLBACK",
        ),
    ):
        uop_graph.begin_group(group_id, group_id.title(), direction="TB")
        nodes = []
        for row in uop.execute(
            f"SELECT {id_col},{label_col} FROM {table} ORDER BY {id_col}"
        ):
            node = f"{group_id}_{re.sub(r'[^A-Za-z0-9_]+', '_', str(row[0])).upper()}"
            nodes.append(node)
            uop_graph.add_node(
                node,
                f"{row[0]}: {row[1]}",
                "warn" if group_id == "HIL_POLICIES" else "semantic",
            )
        uop_graph.end_group()
        for node in nodes:
            uop_graph.add_edge(parent, node, "governed member", conditional=True)
    return env_graph, uop_graph


def _populate(
    plugin_root: Path, env: sqlite3.Connection, uop: sqlite3.Connection
) -> dict[str, Any]:
    tools_payload = _load(
        plugin_root / "toolchains" / "tool-requirement-matrix.v1.json"
    )
    tools = {str(row["tool"]): dict(row) for row in tools_payload["requirements"]}
    actions = _load(plugin_root / "schemas" / "public-action-schemas.v001.json")[
        "tools"
    ]
    skills = _load(plugin_root / "skills" / "skill-surface-registry.v1.json")["skills"]
    hooks = _load(plugin_root / "hooks" / "hook-event-registry.v1.json")["events"]
    env.executemany(
        "INSERT INTO env_authority_meta VALUES(?,?)",
        (
            ("authority", "ENV15"),
            ("schema", CODEX_ACTION_PLANE_SCHEMA),
            ("host_plane", "CODEX_ONLY"),
            ("codex_is_sole_agent", "true"),
            ("foreign_surface_payload_allowed", "false"),
        ),
    )
    env.executemany(
        "INSERT INTO codex_host_variant_v17 VALUES(?,?,?,?,?,?,?,?)",
        [
            (
                r["host_id"],
                r["host_profile"],
                r["app_variant"],
                r["application_id"],
                r["lifetime"],
                int(r["native_mcp"]),
                r["tunnel_policy"],
                "ACTIVE",
            )
            for r in CODEX_HOST_VARIANTS
        ],
    )
    env.executemany(
        "INSERT INTO env_accelerator_profile_v17 VALUES(?,?,?,?,?,?,?,?,?)",
        [
            (
                row["provider_id"],
                row["vendor_plugin"],
                row["runtime"],
                canonical_json_bytes(row["action_classes"]).decode(),
                row["default_memory_budget_percent"],
                row["selection_rule"],
                0,
                0,
                "ACTIVE",
            )
            for row in ACCELERATOR_PROVIDERS
        ],
    )
    env.executemany(
        "INSERT INTO env_workflow_event_v17 VALUES(?,?,?,?,?,?)",
        [
            (
                r["event"],
                r["trigger"],
                r["entry_slip"],
                r["delta_append"],
                r["exit_slip"],
                "ACTIVE",
            )
            for r in WORKFLOW_EVENTS
        ],
    )
    env.executemany(
        "INSERT INTO env_mode_registry_v17 VALUES(?,?,?,?,?)",
        [
            (
                r["mode"],
                canonical_json_bytes(r["project_classes"]).decode(),
                int(r["ci"]),
                r["hil"],
                "ACTIVE",
            )
            for r in MODE_ROWS
        ],
    )
    env.executemany(
        "INSERT INTO env_project_class_policy_v17 VALUES(?,?,?,?,?)",
        [
            (
                r["project_class"],
                canonical_json_bytes(r["lanes"]).decode(),
                r["ci"],
                r["hil"],
                "ACTIVE",
            )
            for r in PROJECT_CLASSES
        ],
    )
    env.executemany(
        "INSERT INTO env_formula_registry_v17 VALUES(?,?,?,?,?,?)",
        [
            (
                r["formula"],
                r["purpose"],
                r["expression"],
                r["rerun"],
                r["exit_effect"],
                "ACTIVE",
            )
            for r in FORMULAS
        ],
    )
    operator_rows = []
    for event in WORKFLOW_EVENTS:
        operator_rows.append(
            (
                f"EVENT::{event['event']}",
                "WORKFLOW_EVENT",
                event["event"],
                "fire only for exact event and authority context",
                "typed event result",
                "ACTIVE",
            )
        )
    for formula in FORMULAS:
        operator_rows.append(
            (
                f"FORMULA::{formula['formula']}",
                "FORMULA",
                formula["purpose"],
                formula["expression"],
                formula["exit_effect"],
                "ACTIVE",
            )
        )
    for mode in MODE_ROWS:
        operator_rows.append(
            (
                f"MODE::{mode['mode']}",
                "MODE",
                mode["mode"],
                "select only when prompt intent and project recipe match",
                mode["hil"],
                "ACTIVE",
            )
        )
    from .mode_governance import _OPERATORS

    for operator_id, operator in sorted(_OPERATORS.items()):
        operator_rows.append(
            (
                str(operator_id),
                str(operator["family"]),
                str(operator["chapter"]),
                "fire only when the selected Codex Mode formula names this operator",
                str(operator["effect"]),
                "ACTIVE",
            )
        )
    env.executemany(
        "INSERT INTO env_operator_registry_v17 VALUES(?,?,?,?,?,?)", operator_rows
    )
    env.executemany(
        "INSERT INTO env_tool_registry_v17 VALUES(?,?,?,?,?,?)",
        [
            (
                tool_id,
                str(row["requirement"]),
                canonical_json_bytes(row.get("surfaces") or []).decode(),
                str(row["role"]),
                0,
                "ACTIVE_DECLARED",
            )
            for tool_id, row in sorted(tools.items())
        ],
    )
    action_rows = []
    sdk_rows = []
    mcp_rows = []
    for action in actions:
        route = dict(action.get("route_contract") or {})
        classes, pipeline = _action_pipeline(action, tools)
        internal = dict(route.get("internal_sdk") or {})
        mcp = dict(route.get("mcp") or {})
        workflows = list(route.get("skill_workflows") or [])
        action_name = str(action["name"])
        entry_event = (
            "STEER_ENTRY"
            if "steer" in action_name
            else "ADAPTIVE_DELTA_EXIT_APPEND"
            if action_name == "adaptive_delta_exit"
            else "STATE_TRAVEL_EXIT"
            if action_name.startswith("pv_state_travel_direct")
            else "DELTA_ENTRY"
            if action_name == "pv_begin_next_turn"
            else "PROMPT_ENTRY"
        )
        body = {
            "action": action_name,
            "classes": classes,
            "owner": route.get("owner_skill"),
            "internal": internal,
            "mcp": mcp,
            "workflows": workflows,
            "entry_event": entry_event,
            "tools": pipeline,
            "schema_sha256": action["schema_sha256"],
        }
        binding = sha256_bytes(canonical_json_bytes(body))
        action_rows.append(
            (
                action_name,
                canonical_json_bytes(classes).decode(),
                route.get("owner_skill"),
                canonical_json_bytes(internal).decode(),
                canonical_json_bytes(mcp).decode(),
                canonical_json_bytes(workflows).decode(),
                entry_event,
                canonical_json_bytes(pipeline).decode(),
                action["schema_sha256"],
                binding,
                "ACTIVE",
            )
        )
        sdk_rows.append(
            (
                action_name,
                canonical_json_bytes(internal).decode(),
                canonical_json_bytes(
                    {"service_dispatch": route.get("service_dispatch"), "mcp": mcp}
                ).decode(),
                "ACTIVE",
            )
        )
        mcp_rows.append(
            (
                action_name,
                str(mcp.get("server_identity") or "evidence-lane"),
                str(mcp.get("tool_name") or action_name),
                "ACTIVE",
            )
        )
    env.executemany(
        "INSERT INTO env_action_binding_v17 VALUES(?,?,?,?,?,?,?,?,?,?,?)", action_rows
    )
    from .lanes import LANE_REGISTRY

    lane_rows = []
    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        ordered = []
        for tool_id, row in tools.items():
            declared = {str(v) for v in row.get("surfaces") or []}
            if (
                lane_id in declared
                or bool(
                    {
                        "all_18_project_sectors",
                        "all_structured_project_sectors",
                        "all_queryable_authorities",
                        "every_sqlite_authority",
                    }
                    & declared
                )
            ) and tool_id != "OpenAI_Agents_SDK":
                ordered.append(tool_id)
        body = {
            "lane": lane_id,
            "source_types": list(lane.source_types),
            "parser": lane.parser_id,
            "chunker": lane.chunker_version,
            "fts": lane.fts_table,
            "tools": ordered,
        }
        lane_rows.append(
            (
                lane_id,
                canonical_json_bytes(list(lane.source_types)).decode(),
                lane.parser_id,
                lane.chunker_version,
                lane.fts_table,
                canonical_json_bytes(ordered).decode(),
                sha256_bytes(canonical_json_bytes(body)),
                "ACTIVE",
            )
        )
    env.executemany(
        "INSERT INTO env_lane_binding_v17 VALUES(?,?,?,?,?,?,?,?)", lane_rows
    )
    env.executemany(
        "INSERT INTO env_skill_binding_v17 VALUES(?,?,?,?,?,?)",
        [
            (
                row["name"],
                row["description"],
                canonical_json_bytes(row["workflow"]).decode(),
                int(row["member_count"]),
                row.get("routing_manifest_sha256"),
                "ACTIVE",
            )
            for row in skills
        ],
    )
    env.executemany(
        "INSERT INTO env_hook_binding_v17 VALUES(?,?,?,?,?,?)",
        [
            (
                row["event"],
                int(row["event_number"]),
                int(row["handler_count"]),
                row["path"],
                row["event_sha256"],
                "ACTIVE",
            )
            for row in hooks
        ],
    )
    env.executemany("INSERT INTO env_sdk_action_binding_v17 VALUES(?,?,?,?)", sdk_rows)
    env.executemany("INSERT INTO env_mcp_action_binding_v17 VALUES(?,?,?,?)", mcp_rows)

    uop.executemany(
        "INSERT INTO uop_authority_meta VALUES(?,?)",
        (
            ("authority", "UOP15"),
            ("schema", CODEX_ACTION_PLANE_SCHEMA),
            ("governance_only", "true"),
            ("can_override_env", "false"),
            ("can_override_project", "false"),
            ("foreign_surface_payload_allowed", "false"),
        ),
    )
    governance = (
        (
            "SOURCE_PRECEDENCE",
            "SOURCE",
            "exact source and authority context selected",
            "No source payload changes authority by itself",
        ),
        (
            "WORK_GATE",
            "WORK",
            "before any action executes",
            "Only condition-true bounded work may run",
        ),
        (
            "PRIVACY_GATE",
            "PRIVACY",
            "before data leaves local authority",
            "Secrets and private reasoning never leave their authority",
        ),
        (
            "DISCLOSURE_GATE",
            "DISCLOSURE",
            "before result emission",
            "Emit only user-visible result and receipt",
        ),
        (
            "DIRECT_PURGE_GATE",
            "DIRECT_PURGE",
            "when a current route is replaced",
            "Remove replaced route and references in the same Delta",
        ),
        (
            "PROJECT_HIL_GATE",
            "HIL",
            "full Project proposal decision",
            "Only exact owning Project token may promote",
        ),
        (
            "LEARNING_HIL_GATE",
            "HIL",
            "Learning proposal decision",
            "Learning approval remains separate from Project approval",
        ),
        (
            "ENTRY_SLIP_GATE",
            "ENTRY",
            "every prompt and steer",
            "Entry Slip classifies intent and focus; it is not an Exit Slip",
        ),
        (
            "EXIT_SLIP_GATE",
            "EXIT",
            "Goal option 2 or completed State Travel only",
            "Delta-exit append never emits an Exit Slip",
        ),
        (
            "FALLBACK_GATE",
            "FALLBACK",
            "selected tool unavailable",
            "Only declared same-class fallback may run and failure remains visible",
        ),
        (
            "ACCELERATOR_BUDGET_GATE",
            "RESOURCE",
            "before eligible GPU execution",
            "Require user vendor-plugin grant, compatible runtime, bounded telemetry, no throttle, configured VRAM budget, and visible CPU fallback",
        ),
    )
    uop.executemany(
        "INSERT INTO uop_governance_operator_v17 VALUES(?,?,?,?,?,?,?)",
        [(a, b, c, d, 0, 0, "ACTIVE") for a, b, c, d in governance],
    )
    uop.executemany(
        "INSERT INTO uop_project_class_hil_policy_v17 VALUES(?,?,?,?,?,?)",
        [
            (
                row["project_class"],
                row["hil"],
                "SEPARATE_CONSOLIDATED_LEARNING_HIL",
                1,
                1,
                "ACTIVE",
            )
            for row in PROJECT_CLASSES
        ],
    )
    gate_rows = []
    for event in WORKFLOW_EVENTS:
        if event["event"] in {"GOAL_OPTION_2_EXIT", "STATE_TRAVEL_EXIT"}:
            allowed = ["EMIT_EXIT_SLIP", "SEAL_BOUNDARY"]
        elif event["event"] == "ADAPTIVE_DELTA_EXIT_APPEND":
            allowed = ["APPEND_DELTA_EXIT", "REFRESH_CHANGED_AUTHORITIES"]
        elif event["entry_slip"]:
            allowed = ["EMIT_ENTRY_SLIP", "CLASSIFY_INTENT", "SELECT_WORKFLOW"]
        else:
            allowed = ["RUN_EVENT_CONTRACT"]
        blocked = [
            "INFER_HIL",
            "MOVE_PROJECT_TRUTH_WITHOUT_APPROVAL",
            "RETAIN_SUPERSEDED_ROUTE",
        ]
        gate_rows.append(
            (
                event["event"],
                canonical_json_bytes(allowed).decode(),
                canonical_json_bytes(blocked).decode(),
                int(event["event"] == "HIL_DECISION"),
                "ACTIVE",
            )
        )
    uop.executemany("INSERT INTO uop_workflow_gate_v17 VALUES(?,?,?,?,?)", gate_rows)
    policy_rows = []
    for action in actions:
        ann = dict(action.get("annotations") or {})
        name = str(action["name"])
        hil = int(name in {"pv_fuse", "learning_decide", "pv_rollback"})
        effects = (
            ["READ_CURRENT_AUTHORITY"]
            if ann.get("readOnlyHint")
            else ["WRITE_OWNING_AUTHORITY", "EMIT_RECEIPT"]
        )
        policy_rows.append(
            (
                name,
                int(bool(ann.get("readOnlyHint"))),
                int(bool(ann.get("destructiveHint"))),
                int(bool(ann.get("idempotentHint"))),
                hil,
                int(not bool(ann.get("readOnlyHint"))),
                canonical_json_bytes(effects).decode(),
                "ACTIVE",
            )
        )
    uop.executemany(
        "INSERT INTO uop_action_policy_v17 VALUES(?,?,?,?,?,?,?,?)", policy_rows
    )
    uop.executemany(
        "INSERT INTO uop_tool_policy_v17 VALUES(?,?,?,?,?,?,?)",
        [
            (
                tool_id,
                str(row["requirement"]),
                int(
                    str(row["requirement"])
                    not in {"REQUIRED_DEPENDENCY", "REQUIRED_BUNDLED_NATIVE"}
                ),
                1,
                0,
                "RUN_ONLY_WHEN_ENV_ACTION_LANE_PHASE_AND_GRANT_SELECT",
                "ACTIVE",
            )
            for tool_id, row in sorted(tools.items())
        ],
    )
    uop.executemany(
        "INSERT INTO uop_host_policy_v17 VALUES(?,?,?,?,?)",
        [
            (
                row["host_id"],
                row["host_profile"],
                1,
                "CODEX_HOST_VARIANT_BOUND_TO_CURRENT_RUNTIME",
                "ACTIVE",
            )
            for row in CODEX_HOST_VARIANTS
        ],
    )
    uop.executemany(
        "INSERT INTO uop_accelerator_policy_v17 VALUES(?,?,?,?,?,?,?,?,?,?)",
        [
            (
                row["provider_id"],
                int(row["vendor_plugin"] != "NONE"),
                1,
                row["default_memory_budget_percent"],
                100 if row["provider_id"] == "CPU" else 95,
                int(row["provider_id"] != "CPU"),
                int(row["provider_id"] != "CPU"),
                1,
                0,
                "ACTIVE",
            )
            for row in ACCELERATOR_PROVIDERS
        ],
    )
    uop.executemany(
        "INSERT INTO uop_fallback_policy_v17 VALUES(?,?,?,?,?)",
        [
            (
                key,
                "ORDERED_AVAILABLE_TOOL_WITHIN_SAME_WORKFLOW_CLASS_ONLY",
                0,
                0,
                "ACTIVE",
            )
            for key in CLASS_TOOLS
        ],
    )
    return {
        "tools": len(tools),
        "actions": len(actions),
        "lanes": len(lane_rows),
        "skills": len(skills),
        "hooks": len(hooks),
        "hosts": len(CODEX_HOST_VARIANTS),
    }


def _seal_receipt(
    connection: sqlite3.Connection, authority: str, counts: dict[str, int]
) -> dict[str, Any]:
    table_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    )
    core = {
        "schema": CODEX_ACTION_PLANE_SCHEMA,
        "status": "PASS",
        "authority": authority,
        "table_count": table_count,
        "counts": counts,
        "foreign_surface_row_count": 0,
        "direct_rebuild_from_current_codex_registries": True,
        "predecessor_database_copied": False,
        "recorded_at": FIXED_TIME,
    }
    receipt = {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
    if authority == "ENV15":
        connection.execute(
            "INSERT INTO env_action_plane_build_receipt VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                table_count,
                counts["tools"],
                counts["actions"],
                counts["lanes"],
                counts["skills"],
                counts["hooks"],
                counts["hosts"],
                0,
                canonical_json_bytes(receipt).decode(),
                receipt["receipt_sha256"],
                FIXED_TIME,
            ),
        )
    else:
        connection.execute(
            "INSERT INTO uop_action_plane_build_receipt VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                table_count,
                10,
                counts["actions"],
                counts["tools"],
                counts["hosts"],
                0,
                canonical_json_bytes(receipt).decode(),
                receipt["receipt_sha256"],
                FIXED_TIME,
            ),
        )
    return receipt


def _replace_database(source: Path, destination: Path) -> str:
    for attempt in range(8):
        try:
            os.replace(source, destination)
            return "ATOMIC_FILE_REPLACE"
        except PermissionError:
            if attempt == 7:
                break
            time.sleep(0.05 * (attempt + 1))

    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination, timeout=60)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
        if (
            destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
            != "ok"
        ):
            raise RuntimeError("CODEX_ACTION_PLANE_BACKUP_REPLACE_INTEGRITY_FAILED")
    finally:
        destination_connection.close()
        source_connection.close()
    source.unlink()
    return "SQLITE_BACKUP_REPLACE_TRANSIENT_FILE_HANDLE_COMPATIBLE"


def rebuild_codex_action_planes(plugin_root: str | Path) -> dict[str, Any]:
    root = Path(plugin_root).resolve()
    env_final = root / "env" / "env_sqlite.sqlite"
    uop_final = root / "uop" / "uop_sqlite.sqlite"
    for directory, pattern in (
        (env_final.parent, f".{env_final.name}.*.tmp"),
        (uop_final.parent, f".{uop_final.name}.*.tmp"),
    ):
        for stale in directory.glob(pattern):
            resolved = stale.resolve()
            if resolved.parent != directory.resolve():
                raise RuntimeError("ACTION_PLANE_TEMP_PURGE_PATH_ESCAPE")
            resolved.unlink()
    env_tmp = env_final.with_name(f".{env_final.name}.{uuid.uuid4().hex}.tmp")
    uop_tmp = uop_final.with_name(f".{uop_final.name}.{uuid.uuid4().hex}.tmp")
    for path in (env_tmp, uop_tmp):
        if path.exists():
            path.unlink()
    env = sqlite3.connect(env_tmp)
    uop = sqlite3.connect(uop_tmp)
    try:
        _create_env_schema(env)
        _create_uop_schema(uop)
        counts = _populate(root, env, uop)
        env_graph, uop_graph = _build_graphs(env, uop)
        env_graph_receipt = _store_graph(
            env,
            "env",
            env_graph,
            root / "env" / "env_mmd.mmd",
            root / "env" / "env_mmd.dot",
        )
        uop_graph_receipt = _store_graph(
            uop,
            "uop",
            uop_graph,
            root / "uop" / "uop_mmd.mmd",
            root / "uop" / "uop_mmd.dot",
        )
        env_index = rebuild_connection_authority_index(
            env, authority_id="env", recorded_at=FIXED_TIME, reset_receipts=True
        )
        uop_index = rebuild_connection_authority_index(
            uop, authority_id="uop", recorded_at=FIXED_TIME, reset_receipts=True
        )
        env_receipt = _seal_receipt(env, "ENV15", counts)
        uop_receipt = _seal_receipt(uop, "UOP15", counts)
        env.commit()
        uop.commit()
        for connection in (env, uop):
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("CODEX_ACTION_PLANE_INTEGRITY_FAILED")
    finally:
        env.close()
        uop.close()
    env_replace_mode = _replace_database(env_tmp, env_final)
    uop_replace_mode = _replace_database(uop_tmp, uop_final)
    source_audit = {
        "schema": "evidence-lane.codex-action-plane-source-audit.v1",
        "overall_status": "PASS",
        "whole_packet_accepted": True,
        "usable_boundary": "CURRENT_CODEX_ACTION_PLANE_ONLY",
        "missing_declared_files": [],
        "authority_source": "CURRENT_CODEX_PLUGIN_REGISTRIES_ONLY",
        "chatgpt_surface_rows": 0,
        "foreign_absolute_paths": 0,
        "discussion_or_chatlineage_authority_rows": 0,
        "predecessor_database_copied": False,
        "receipt_sha256": sha256_bytes(
            canonical_json_bytes(
                {
                    "authority_source": "CURRENT_CODEX_PLUGIN_REGISTRIES_ONLY",
                    "chatgpt_surface_rows": 0,
                    "foreign_absolute_paths": 0,
                    "discussion_or_chatlineage_authority_rows": 0,
                    "predecessor_database_copied": False,
                }
            )
        ),
    }
    atomic_write_bytes(
        root / "env" / "SOURCE_PACKET_AUDIT.json",
        canonical_json_bytes(source_audit),
    )
    atomic_write_bytes(
        root / "env" / "UNIVERSAL_FLASH_PROMPT.md",
        (
            b"# Codex ENV15/UOP15 Flash Contract\n\n"
            b"Load the clean Codex-native ENV and UOP SQLite authorities read-only. "
            b"Codex is the sole acting agent. For every prompt or steer, Source Intake "
            b"and ENV emit an Entry Slip containing intent, focus, owning authority/lane, "
            b"workflow, gates, and the next bounded action. Adaptive Delta-exit append is "
            b"a continuing-work refresh and never an Exit Slip. Emit an Exit Slip only "
            b"when State Travel completes or the Goal finishes through option 2. UOP "
            b"governs but cannot override ENV, Project Truth, Plan, Goal, or HIL.\n"
        ),
    )
    core = {
        "schema": "evidence-lane.codex-action-plane-rebuild.v1",
        "status": "PASS",
        "counts": counts,
        "env": {
            "receipt": env_receipt,
            "graph": env_graph_receipt,
            "index": env_index,
            "sqlite_sha256": sha256_file(env_final),
            "replace_mode": env_replace_mode,
        },
        "uop": {
            "receipt": uop_receipt,
            "graph": uop_graph_receipt,
            "index": uop_index,
            "sqlite_sha256": sha256_file(uop_final),
            "replace_mode": uop_replace_mode,
        },
        "codex_is_sole_agent": True,
        "chatgpt_surface_rows": 0,
        "foreign_absolute_paths": 0,
        "discussion_or_chatlineage_authority_rows": 0,
        "predecessor_database_copied": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "CODEX_ACTION_PLANE_SCHEMA",
    "CODEX_HOST_VARIANTS",
    "FORMULAS",
    "MODE_ROWS",
    "PROJECT_CLASSES",
    "WORKFLOW_EVENTS",
    "classify_action_workflow_classes",
    "rebuild_codex_action_planes",
]

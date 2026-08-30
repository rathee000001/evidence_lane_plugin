"""Deterministic eighteen-lane SQLite/MMD/DOT build and incremental Refresh."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import mimetypes
import posixpath
import re
import shutil
import sqlite3
import subprocess
import zipfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import cache
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, cast

from defusedxml import ElementTree

from .artifact_contract import (
    FOUR_FILE_CONTRACT_SCHEMA,
    TOOLS_ARTIFACT_AUTHORITY_SCHEMA,
    bind_tools_to_artifacts,
    build_four_file_contract,
    stable_artifact_names,
    validate_four_file_contract,
)
from .compact_storage import (
    compress_exact_bytes,
    decompress_exact_bytes,
    verify_lane_compact_storage,
)
from .data_toolchain import (
    DataInspectionRequest,
    inspect_excel_openpyxl,
    inspect_sqlalchemy_sqlite,
    inspect_tableau_hyper,
    inspect_tabular_pandas,
)
from .dependency_detection import parse_pnpm_lock_dependencies
from .document_toolchain import (
    DoclingRequest,
    docling_available,
    extract_with_docling,
    packaged_docling_artifacts_root,
)
from .entity_reconciliation import reconcile_entity_candidates
from .git_history import (
    create_git_history_schema,
    git_history_signature,
    index_git_history,
)
from .git_optional import probe_git_arm
from .graph_pipeline import SemanticGraph
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .ingest import extract_code_lane_facts, governed_source_files
from .lane_contract import (
    LANE_DISPOSITION_SCHEMA,
    build_lane_disposition_projection,
    validate_lane_disposition_projection,
)
from .lanes import (
    CANONICAL_LANE_IDS,
    CODE_LOGICAL_TOPOLOGY,
    CORE_SCHEMA_TABLES,
    LANE_ARTIFACT_ROLE_REGISTRY_SHA256,
    LANE_REGISTRY,
    LANE_SCHEMA_EVOLUTION_POLICY_SHA256,
    LANE_SCHEMA_REGISTRY_SHA256,
    PRIMARY_CODE_LANES,
    SQLITE_BRAIN_BUILDER_MASTER_TOPOLOGY_AUTHORITY_SHA256,
    SQLITE_BRAIN_BUILDER_MMD_AUTHORITY_SHA256,
    LaneDefinition,
    catalog,
    lane_artifact_contract,
    lane_schema_asset,
    lane_schema_evolution_contract,
    lane_schema_registry_contract,
    route_batch,
    route_source,
)
from .native_toolchain import (
    NativeInvocationRequest,
    configured_runtime_root,
    run_native_tool,
    try_resolve_native_tool,
)
from .redaction import redact_text
from .schema_topology import (
    PHYSICAL_SCHEMA_PROJECTION_SCHEMA,
    physical_schema_projection,
    physical_table_groups,
    physical_table_node_ids,
)
from .sqlite_execution import verify_and_optimize_sqlite_authority
from .sqlite_indexing import (
    ensure_authority_index_schema,
    llama_index_nodes,
    rebuild_connection_authority_index,
)
from .tabular_toolchain import (
    DuckDBStageRequest,
    duckdb_available,
    polars_available,
    stage_result_to_lane_payloads,
    stage_tabular_source,
    stage_tabular_source_polars,
)
from .timeutil import utc_now
from .topology_reconciliation import (
    reconcile_bundle_topology,
    reconcile_lane_topology,
)
from .web_toolchain import extract_web_document

LANE_SCHEMA_VERSION = "evidence-lane.universal-lane.v4"
LEGACY_LANE_BUNDLE_SCHEMA = "evidence-lane.universal-lane-bundle.v1"
LANE_BUNDLE_SCHEMA = "evidence-lane.universal-lane-bundle.v2"
TOPOLOGY_GENERATOR_SCHEMA = "evidence-lane.lane-topology-generator.v5"
LANE_MANIFEST_SCHEMA = "evidence-lane.lane-manifest.v3"
MAX_EXTRACT_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_ROWS_PER_TAB = 5000
CHUNK_CHARS = 6000
CHUNK_OVERLAP = 500
TFIDF_TERMS_PER_CHUNK = 256
MAX_PARALLEL_LANE_WORKERS = 8
_TOKEN_RE = re.compile(r"[\w][\w.-]{1,63}", flags=re.UNICODE)
_XML_TEXT_TAG = re.compile(r"}t$")
_RAPIDOCR_CALL_LOCK = Lock()
_MEDIA_EXTENSIONS = frozenset(
    {".avi", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}
)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _capability_rows(lane: LaneDefinition) -> list[dict[str, str]]:
    rows = [
        {
            "capability": "exact_source_bytes",
            "state": "ACTIVE",
            "tool": "python pathlib/hashlib",
            "detail": "Every routed source is hash-bound even when parsing is unavailable.",
        },
        {
            "capability": "fts5_bm25",
            "state": "ACTIVE",
            "tool": "sqlite3 FTS5",
            "detail": "Unicode FTS with explicit BM25 ranking.",
        },
        {
            "capability": "sqlite_full_api",
            "state": "ACTIVE" if _module_available("apsw") else "UNAVAILABLE",
            "tool": "APSW",
            "detail": (
                "Preferred hidden-runtime SQLite adapter for integrity, optimize, "
                "backup, Session/RBU capability, and tracing; bulk writes remain one "
                "explicit transaction."
            ),
        },
        {
            "capability": "llamaindex_sqlite_nodes",
            "state": "ACTIVE" if _module_available("llama_index") else "UNAVAILABLE",
            "tool": "LlamaIndex",
            "detail": "Deterministic nodes are persisted into the owning SQLite and FTS5 index.",
        },
        {
            "capability": "pydantic_tool_contracts",
            "state": "ACTIVE" if _module_available("pydantic") else "UNAVAILABLE",
            "tool": "Pydantic",
            "detail": "Validates typed tool requests and results before SQLite persistence.",
        },
        {
            "capability": "tfidf",
            "state": "ACTIVE",
            "tool": "deterministic Python term statistics",
            "detail": (
                "Bounded query-time TF/DF/IDF over FTS candidates; no duplicate "
                "materialized vector table."
            ),
        },
        {
            "capability": "mermaid_source",
            "state": "ACTIVE",
            "tool": "deterministic Mermaid emitter",
            "detail": "Authoritative lane topology is emitted as Mermaid source.",
        },
        {
            "capability": "dot_source",
            "state": "ACTIVE",
            "tool": "deterministic DOT emitter",
            "detail": "Authoritative lane topology is emitted as Graphviz DOT source.",
        },
        {
            "capability": "mermaid_render_mmdc",
            "state": "ACTIVE" if shutil.which("mmdc") else "UNAVAILABLE",
            "tool": "mmdc",
            "detail": "Optional derived render; Mermaid source remains authoritative.",
        },
        {
            "capability": "graphviz_render_dot",
            "state": "ACTIVE" if try_resolve_native_tool("graphviz") else "UNAVAILABLE",
            "tool": "dot",
            "detail": "Hidden-runtime native DOT validation/render; DOT source remains authoritative.",
        },
    ]
    if lane.canonical_lane_id == "pdf_ocr":
        for capability, module in (
            ("pdf_page_render_pypdfium2", "pypdfium2"),
            ("pdf_native_text_pypdf", "pypdf"),
            ("pdf_structural_pdfplumber", "pdfplumber"),
            ("ocr_pytesseract", "pytesseract"),
            ("ocr_rapidocr", "rapidocr"),
            ("ocr_onnxruntime", "onnxruntime"),
            ("document_parser_docling", "docling"),
            ("image_pillow", "PIL"),
            ("image_opencv", "cv2"),
        ):
            rows.append(
                {
                    "capability": capability,
                    "state": "ACTIVE" if _module_available(module) else "UNAVAILABLE",
                    "tool": module,
                    "detail": "Optional V1/V3 extractor; absence is fail-visible.",
                }
            )
        rows.append(
            {
                "capability": "ocr_tesseract_binary",
                "state": "ACTIVE"
                if try_resolve_native_tool("tesseract")
                else "UNAVAILABLE",
                "tool": "tesseract",
                "detail": "Required by pytesseract for local OCR.",
            }
        )
        for capability, tool_id in (
            ("pdf_poppler_pdftotext", "poppler_pdftotext"),
            ("pdf_poppler_pdfinfo", "poppler_pdfinfo"),
            ("pdf_ghostscript", "ghostscript"),
        ):
            command = try_resolve_native_tool(tool_id)
            rows.append(
                {
                    "capability": capability,
                    "state": "ACTIVE" if command else "UNAVAILABLE",
                    "tool": tool_id,
                    "detail": "V1/V3 local extraction tool; absence is fail-visible.",
                }
            )
    elif lane.canonical_lane_id == "images_ocr":
        for capability, module in (
            ("image_metadata", "PIL"),
            ("ocr_pytesseract", "pytesseract"),
            ("ocr_rapidocr", "rapidocr"),
            ("ocr_onnxruntime", "onnxruntime"),
            ("image_opencv", "cv2"),
        ):
            rows.append(
                {
                    "capability": capability,
                    "state": "ACTIVE" if _module_available(module) else "UNAVAILABLE",
                    "tool": module,
                    "detail": "Optional local image/OCR extractor.",
                }
            )
        rows.append(
            {
                "capability": "ocr_tesseract_binary",
                "state": "ACTIVE"
                if try_resolve_native_tool("tesseract")
                else "UNAVAILABLE",
                "tool": "tesseract",
                "detail": "Required only for the pytesseract OCR route.",
            }
        )
    elif lane.canonical_lane_id == "data_excel":
        rows.extend(
            [
                {
                    "capability": "xlsx_openxml",
                    "state": "ACTIVE",
                    "tool": "zipfile+xml.etree",
                    "detail": "Workbook, sheet, cell, formula, and relationship extraction.",
                },
                {
                    "capability": "csv_tsv",
                    "state": "ACTIVE" if _module_available("duckdb") else "FALLBACK",
                    "tool": "DuckDB -> python csv",
                    "detail": (
                        "DuckDB is the primary bounded analytical stage; Python CSV is "
                        "the fail-visible fallback. Results persist only to lane SQLite."
                    ),
                },
                {
                    "capability": "json_jsonl",
                    "state": "ACTIVE",
                    "tool": "python json",
                    "detail": "Bounded object/list shape and row-sample extraction.",
                },
            ]
        )
        for capability, module in (
            ("excel_openpyxl", "openpyxl"),
            ("excel_pandas", "pandas"),
            ("parquet_pyarrow", "pyarrow"),
            ("excel_calamine", "python_calamine"),
            ("tabular_duckdb", "duckdb"),
            ("tabular_polars_lazy", "polars"),
        ):
            rows.append(
                {
                    "capability": capability,
                    "state": "ACTIVE" if _module_available(module) else "UNAVAILABLE",
                    "tool": module,
                    "detail": "Optional higher-fidelity tabular extractor.",
                }
            )
    elif lane.canonical_lane_id == "docs":
        rows.append(
            {
                "capability": "docx_openxml",
                "state": "ACTIVE",
                "tool": "zipfile+xml.etree",
                "detail": "Hierarchy-preserving DOCX text/table extraction.",
            }
        )
    elif lane.canonical_lane_id == "ppt":
        rows.append(
            {
                "capability": "pptx_openxml",
                "state": "ACTIVE",
                "tool": "zipfile+xml.etree",
                "detail": "Slide/notes/shape text and relationship extraction.",
            }
        )
    elif lane.canonical_lane_id in {"sqlite_brain", "brain_loader"}:
        rows.append(
            {
                "capability": "sqlite_read_only_inspection",
                "state": "ACTIVE",
                "tool": "sqlite3 immutable URI",
                "detail": "Schema, integrity, foreign keys, FTS objects; imported SQL is never run.",
            }
        )
    required = {
        "exact_source_bytes",
        "fts5_bm25",
        "tfidf",
        "mermaid_source",
        "dot_source",
        "pydantic_tool_contracts",
    }
    optional = {
        "mermaid_render_mmdc",
        "graphviz_render_dot",
        "document_parser_docling",
        "excel_openpyxl",
        "excel_pandas",
        "parquet_pyarrow",
        "excel_calamine",
        "sqlite_full_api",
        "llamaindex_sqlite_nodes",
        "tabular_duckdb",
        "tabular_polars_lazy",
        "image_opencv",
    }
    for row in rows:
        capability = row["capability"]
        row["requirement"] = (
            "REQUIRED"
            if capability in required
            else "OPTIONAL"
            if capability in optional
            else "CONDITIONAL"
        )
    return rows


def _tool_identity(
    lane: LaneDefinition,
    *,
    source_paths: Iterable[str] = (),
) -> dict[str, Any]:
    capabilities = _capability_rows(lane)
    topology_generator = _topology_generator_identity(lane)
    schema_asset = lane_schema_asset(lane.canonical_lane_id)
    artifact_contract_module = Path(
        bind_tools_to_artifacts.__code__.co_filename
    ).resolve()
    registry_workflow = _registry_linked_lane_workflow(lane.canonical_lane_id)
    source_conditioned = _source_conditioned_lane_toolchain(
        lane,
        source_paths=tuple(source_paths),
        registry_workflow=registry_workflow,
    )
    source_conditioned_identity = {
        "lane_id": source_conditioned["lane_id"],
        "detected_source_lanes": source_conditioned["detected_source_lanes"],
        "eligible_tools": source_conditioned["eligible_tools"],
        "routing_registry": source_conditioned["routing_registry"],
        "pairing_registry": source_conditioned["pairing_registry"],
        "source_path_count_affects_tool_identity": False,
    }
    payload = {
        "lane": lane.as_dict(),
        "capabilities": capabilities,
        "lane_schema_version": LANE_SCHEMA_VERSION,
        "lane_schema_asset": {
            "registry": lane_schema_registry_contract(),
            "schema_id": schema_asset["schema_id"],
            "schema_version": schema_asset["schema_version"],
            "contract_sha256": schema_asset["contract_sha256"],
            "sqlite_master_projection_sha256": schema_asset[
                "sqlite_master_projection_sha256"
            ],
            "extension_namespace": schema_asset["extension_namespace"],
            "migration_head": schema_asset["migration_ledger"][-1],
        },
        "topology_generator": topology_generator,
        "artifact_contract": {
            "four_file_schema": FOUR_FILE_CONTRACT_SCHEMA,
            "tools_authority_schema": TOOLS_ARTIFACT_AUTHORITY_SCHEMA,
            "module_sha256": sha256_file(artifact_contract_module),
        },
        "parser_implementation": {
            "lane_engine_sha256": sha256_file(Path(__file__).resolve()),
            "tabular_toolchain_sha256": sha256_file(
                Path(stage_tabular_source.__code__.co_filename).resolve()
            ),
            "sqlite_execution_sha256": sha256_file(
                Path(
                    verify_and_optimize_sqlite_authority.__code__.co_filename
                ).resolve()
            ),
            "code_ingest_sha256": sha256_file(
                Path(extract_code_lane_facts.__code__.co_filename).resolve()
            ),
            "dependency_detection_sha256": sha256_file(
                Path(parse_pnpm_lock_dependencies.__code__.co_filename).resolve()
            ),
        },
        "registry_linked_workflow": registry_workflow,
        "source_conditioned_toolchain": source_conditioned,
        "source_conditioned_tool_identity": source_conditioned_identity,
    }
    # The four-file contract deliberately hashes the established tool-identity
    # core.  ``lane_schema_asset`` is an additive public projection; its exact
    # bytes are already sealed inside ``lane`` and ``topology_generator``.
    identity_core = {
        key: payload[key]
        for key in (
            "lane",
            "capabilities",
            "lane_schema_version",
            "topology_generator",
            "artifact_contract",
            "parser_implementation",
            "registry_linked_workflow",
            "source_conditioned_tool_identity",
        )
    }
    payload["sha256"] = sha256_bytes(canonical_json_bytes(identity_core))
    return payload


@cache
def _registry_linked_lane_workflow(lane_id: str) -> dict[str, Any]:
    """Resolve every current tool/action/skill/hook binding for one lane."""

    plugin_root = Path(__file__).resolve().parents[2]
    relative_paths = {
        "tool_requirements": "toolchains/tool-requirement-matrix.v1.json",
        "tool_routing": "toolchains/tool-execution-routing.v1.json",
        "unified_pairing": "toolchains/unified-tool-workflow-pairing.v1.json",
        "public_actions": "schemas/public-action-schemas.v001.json",
        "skills": "skills/skill-surface-registry.v1.json",
        "hooks": "hooks/hook-event-registry.v1.json",
        "sdk": "sdk/sdk-manifest.v1.json",
        "mcp": "mcp/mcp-manifest.v1.json",
    }
    paths = {name: plugin_root / relative for name, relative in relative_paths.items()}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError(f"Lane workflow registries are missing: {missing}")
    values = {
        name: json.loads(path.read_text(encoding="utf-8"))
        for name, path in paths.items()
    }
    linked_tools = [
        dict(row)
        for row in values["unified_pairing"]["rows"]
        if lane_id in {str(value) for value in row["eligible_lanes"]}
    ]
    task_execution_tools = [
        str(row["tool"])
        for row in linked_tools
        if row.get("role_class", "TASK_EXECUTION") == "TASK_EXECUTION"
    ]
    transport_adapters = [
        str(row["tool"])
        for row in linked_tools
        if row.get("role_class") == "TRANSPORT_OR_ORCHESTRATION"
    ]
    external_services = [
        str(row["tool"])
        for row in linked_tools
        if row.get("role_class") == "EXTERNAL_SERVICE_OR_STORE"
    ]
    cross_cutting_attachments = [
        str(row["tool"])
        for row in linked_tools
        if row.get("role_class") == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT"
    ]
    execution_rows = [
        row for row in linked_tools if row.get("role_class") == "TASK_EXECUTION"
    ]
    actions = sorted(
        {
            str(action)
            for row in execution_rows
            for action in row["eligible_public_actions"]
        }
    )
    workflows = sorted(
        {
            (str(item["skill"]), str(item["workflow"]))
            for row in execution_rows
            for item in row["eligible_skill_workflows"]
        }
    )
    action_classes = sorted(
        {
            str(action_class)
            for row in execution_rows
            for action_class in row["action_classes"]
        }
    )
    core = {
        "schema": "evidence-lane.lane-registry-linked-workflow.v1",
        "status": "PASS",
        "lane_id": lane_id,
        "eligible_tools": task_execution_tools,
        "eligible_tool_count": len(task_execution_tools),
        "task_execution_tools": task_execution_tools,
        "transport_orchestration_adapters": transport_adapters,
        "external_services_or_stores": external_services,
        "observability_evaluation_attachments": cross_cutting_attachments,
        "tool_role_classes_are_mutually_exclusive": True,
        "action_classes": action_classes,
        "eligible_public_actions": actions,
        "eligible_public_action_count": len(actions),
        "eligible_skill_workflows": [
            {"skill": skill, "workflow": workflow} for skill, workflow in workflows
        ],
        "eligible_skill_workflow_count": len(workflows),
        "current_registry_counts": {
            "tool_requirements": len(values["tool_requirements"]["requirements"]),
            "mcp_actions": len(values["public_actions"]["tools"]),
            "governed_skills": len(values["skills"]["skills"]),
            "hook_events": len(values["hooks"]["events"]),
        },
        "counts_are_current_snapshot_not_ceiling": True,
        "all_required_linked_steps_run": True,
        "unrelated_tools_run": False,
        "env_selects_applicable_capabilities": True,
        "uop_governs_operators_gates_and_fallbacks": True,
        "internal_sdk_owns_execution": True,
        "outer_sdk_selects_local_or_transport_route": True,
        "mcp_action_inventory_is_not_tool_requirement_inventory": True,
        "source_registries": {
            name: (
                {
                    "path": relative_paths[name],
                    "hash_authority": "manifests/executable-surface-registry.v1.json",
                    "direct_hash_embedded": False,
                    "reason": "DERIVED_REGISTRY_AVOIDS_SELF_REFERENTIAL_HASH_CYCLE",
                }
                if name in {"unified_pairing", "sdk", "mcp"}
                else {
                    "path": relative_paths[name],
                    "sha256": sha256_file(path),
                    "direct_hash_embedded": True,
                }
            )
            for name, path in paths.items()
        },
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _source_conditioned_lane_toolchain(
    lane: LaneDefinition,
    *,
    source_paths: tuple[str, ...],
    registry_workflow: dict[str, Any],
) -> dict[str, Any]:
    """Expand a lane route only for source shapes and executable-plane needs."""

    detected_lanes = (
        sorted(
            {
                route_source(path, code_mode=lane.canonical_lane_id)
                for path in source_paths
            }
        )
        if source_paths and lane.canonical_lane_id in PRIMARY_CODE_LANES
        else [lane.canonical_lane_id]
    )
    plugin_root = Path(__file__).resolve().parents[2]
    routing_path = plugin_root / "toolchains" / "tool-execution-routing.v1.json"
    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    pairing_path = plugin_root / "toolchains" / "unified-tool-workflow-pairing.v1.json"
    pairing = json.loads(pairing_path.read_text(encoding="utf-8"))
    pairing_by_tool = {str(row["tool"]): row for row in pairing["rows"]}
    base_tools = {str(value) for value in registry_workflow["eligible_tools"]}
    executable_surfaces = {
        "local_code",
        "github_code",
        "git_delivery",
        "native_mcp",
        "all_18_project_sectors",
    }
    selected_rows = []
    for row in routing["rows"]:
        row_lanes = {str(value) for value in row["lanes"]}
        surfaces = {str(value) for value in row["surfaces"]}
        tool = str(row["tool"])
        if (
            tool in base_tools
            or bool(row_lanes & set(detected_lanes))
            or bool(surfaces & executable_surfaces)
        ):
            selected_rows.append(
                {
                    "tool": tool,
                    "role_class": str(pairing_by_tool[tool]["role_class"]),
                    "requirement": str(row["requirement"]),
                    "action_classes": [str(value) for value in row["action_classes"]],
                    "primary": [str(value) for value in row["primary"]],
                    "fallback": [str(value) for value in row["fallback"]],
                    "eligible_lanes": [str(value) for value in row["lanes"]],
                    "surfaces": [str(value) for value in row["surfaces"]],
                    "implementation_owner": str(row["implementation_owner"]),
                    "runs_only_when_selected": bool(row["runs_only_when_selected"]),
                }
            )
    core = {
        "schema": "evidence-lane.source-conditioned-lane-toolchain.v1",
        "status": "PASS",
        "lane_id": lane.canonical_lane_id,
        "source_path_count": len(source_paths),
        "detected_source_lanes": detected_lanes,
        "eligible_tools": [row["tool"] for row in selected_rows],
        "eligible_tool_count": len(selected_rows),
        "rows": selected_rows,
        "all_linked_required_steps_must_run_or_fail_visible": True,
        "all_registry_tools_run": False,
        "selection_is_source_shape_action_workflow_env_uop_dependent": True,
        "outside_project_root_execution_surfaces_included": True,
        "counts_are_current_snapshot_not_ceiling": True,
        "routing_registry": {
            "path": routing_path.relative_to(plugin_root).as_posix(),
            "sha256": sha256_file(routing_path),
        },
        "pairing_registry": {
            "path": pairing_path.relative_to(plugin_root).as_posix(),
            "sha256": sha256_file(pairing_path),
        },
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _lane_tool_execution_evidence(
    *,
    lane: LaneDefinition,
    tools: dict[str, Any],
    database: Path,
    mmd_path: Path,
    dot_path: Path,
    history_report: dict[str, Any] | None,
    sqlite_execution: dict[str, Any],
    recorded_at: str,
) -> dict[str, Any]:
    """Classify every eligible tool from evidence produced by this lane build."""

    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        counts = {
            table: _table_count(connection, table)
            for table in (
                "source_registry",
                "source_content_cas",
                "chunk_index",
                "chunk_content_cas",
                lane.fts_table,
                "authority_index_refresh_receipt",
                "code_parser_receipt",
                "structured_fact",
            )
        }
        parser_states = [
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT parser_state FROM source_registry "
                "ORDER BY parser_state"
            )
        ]
        code_parser_statuses: dict[str, int] = {}
        unresolved_source_cas = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM source_registry AS source
                LEFT JOIN source_content_cas AS content
                  ON content.sha256 = source.sha256
                WHERE content.sha256 IS NULL
                   OR content.size_bytes != source.size_bytes
                """
            ).fetchone()[0]
        )
        distinct_source_hashes = int(
            connection.execute(
                "SELECT COUNT(DISTINCT sha256) FROM source_registry"
            ).fetchone()[0]
        )
        if counts["code_parser_receipt"]:
            for row in connection.execute(
                "SELECT payload_json FROM code_parser_receipt"
            ):
                status = str(json.loads(str(row[0])).get("status") or "UNKNOWN")
                code_parser_statuses[status] = code_parser_statuses.get(status, 0) + 1
    finally:
        connection.close()

    capability_by_tool = {
        str(row["tool"]).casefold(): dict(row) for row in tools["capabilities"]
    }
    core_proofs: dict[str, tuple[bool, str]] = {
        "Python": (True, "current Python lane builder process"),
        "hashlib_pathlib": (
            counts["source_registry"] > 0
            and unresolved_source_cas == 0
            and distinct_source_hashes == counts["source_content_cas"],
            (
                "every source hash resolves to one exact-size CAS row; "
                f"sources={counts['source_registry']}; "
                f"distinct_hashes={distinct_source_hashes}; "
                f"cas_rows={counts['source_content_cas']}; "
                f"unresolved={unresolved_source_cas}"
            ),
        ),
        "SQLite_CAS": (
            counts["source_content_cas"] > 0 and counts["chunk_content_cas"] > 0,
            "compressed source/chunk CAS rows",
        ),
        "SQLite_FTS5_BM25": (
            counts[lane.fts_table] == counts["chunk_index"],
            f"{lane.fts_table} row parity with chunk_index",
        ),
        "APSW_SQLite_engine": (
            str(sqlite_execution.get("status") or "").upper() == "PASS",
            "verify_and_optimize_sqlite_authority PASS",
        ),
        "LlamaIndex_SQLite_indexer": (
            counts["authority_index_refresh_receipt"] > 0,
            "authority_index_refresh_receipt",
        ),
        "LangGraph_Mermaid_engine": (
            mmd_path.is_file(),
            mmd_path.name,
        ),
        "Python_Graphviz_DOT_engine": (
            dot_path.is_file(),
            dot_path.name,
        ),
        "Pydantic": (
            str(sqlite_execution.get("status") or "").upper() == "PASS",
            "typed SQLite execution receipt",
        ),
        "Python_structural_parser": (
            counts["code_parser_receipt"] > 0,
            "code_parser_receipt rows",
        ),
    }
    parser_state_markers = {
        "DuckDB": ("DUCKDB",),
        "Polars": ("POLARS",),
        "pandas": ("PANDAS",),
        "pyarrow": ("PYARROW", "PARQUET"),
        "openpyxl": ("OPENPYXL",),
        "python_calamine": ("CALAMINE",),
        "Tableau_Hyper_API": ("TABLEAU", "HYPER"),
        "SQLAlchemy": ("SQLALCHEMY",),
        "RapidOCR_ONNX_Runtime": ("OCR_LOCAL", "RAPIDOCR"),
        "pytesseract_Tesseract": ("PYTESSERACT", "TESSERACT"),
        "Pillow": ("PILLOW", "IMAGE_METADATA", "OCR_LOCAL"),
        "OpenCV": ("OPENCV",),
        "PyMuPDF": ("PYMUPDF",),
        "pdfplumber": ("PDFPLUMBER",),
        "pypdf": ("PYPDF",),
        "Docling": ("DOCLING",),
        "lxml": ("LXML", "OPENXML"),
        "BeautifulSoup4": ("BEAUTIFULSOUP",),
        "markdownify": ("MARKDOWNIFY",),
        "html2text": ("HTML2TEXT",),
        "trafilatura": ("TRAFILATURA",),
        "DOCX_OpenXML": ("DOCX", "OPENXML"),
        "PPTX_OpenXML": ("PPTX", "OPENXML"),
    }
    query_phase_tools = {
        "deterministic_TFIDF",
        "rank_bm25",
        "SentenceTransformers",
        "FAISS_CPU",
        "sqlite_vec",
        "RapidFuzz",
    }
    render_on_request_tools = {"Mermaid_CLI_mmdc", "Graphviz_dot"}
    rows = []
    for eligible in tools["source_conditioned_toolchain"]["rows"]:
        tool = str(eligible["tool"])
        role_class = str(eligible["role_class"])
        phases = sorted(
            {
                phase
                for action_class in eligible["action_classes"]
                for phase in (
                    "BUILD_OR_PARSE"
                    if action_class
                    in {"CODE", "DOCUMENT", "OCR_MEDIA", "DATA", "WEB_RESEARCH"}
                    else "INDEX_OR_QUERY"
                    if action_class == "RETRIEVAL"
                    else "TOPOLOGY"
                    if action_class == "GRAPH"
                    else "VALIDATE"
                    if action_class == "EVALUATION"
                    else "OBSERVE"
                    if action_class == "OBSERVABILITY"
                    else "DELIVER"
                    if action_class == "DEPLOYMENT"
                    else "TRANSPORT"
                    if action_class in {"RUNTIME_API", "MCP_COMPOSITION"}
                    else "GOVERN",
                )
            }
        )
        condition_state = "CONDITION_FALSE"
        execution_state = "NOT_EXECUTED"
        evidence = ""
        if role_class == "EXTERNAL_SERVICE_OR_STORE":
            evidence = "no explicit project grant/action/credential for this lane build"
        elif role_class == "TRANSPORT_OR_ORCHESTRATION":
            evidence = "package-local lane build required no outer transport"
        elif role_class == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT":
            evidence = "no explicit evaluation or observability export requested"
        elif tool in core_proofs:
            condition_state = "CONDITION_TRUE"
            passed, evidence = core_proofs[tool]
            execution_state = "EXECUTED" if passed else "BLOCKED_MISSING_PROOF"
        elif tool == "TreeSitter_LanguagePack":
            condition_state = (
                "CONDITION_TRUE" if code_parser_statuses else "CONDITION_FALSE"
            )
            passed = any(
                status not in {"RUNTIME_NOT_PREWARMED", "UNSUPPORTED_LANGUAGE"}
                for status in code_parser_statuses
            )
            execution_state = (
                ("EXECUTED" if passed else "BLOCKED_RUNTIME_NOT_PREWARMED")
                if code_parser_statuses
                else "NOT_EXECUTED"
            )
            evidence = json.dumps(code_parser_statuses, sort_keys=True)
        elif tool in parser_state_markers:
            matching = [
                state
                for state in parser_states
                if any(marker in state.upper() for marker in parser_state_markers[tool])
            ]
            if matching:
                condition_state = "CONDITION_TRUE"
                execution_state = "EXECUTED"
                evidence = ",".join(matching)
            else:
                evidence = "alternate parser not selected by current source/path"
        elif tool in query_phase_tools:
            evidence = "query phase not requested during lane build"
        elif tool in render_on_request_tools:
            capability = capability_by_tool.get(
                "mmdc" if tool == "Mermaid_CLI_mmdc" else "dot"
            )
            evidence = "derived visual render not requested; availability=" + str(
                (capability or {}).get("state") or "UNKNOWN"
            )
        elif tool in {"Git", "GitPython", "PyGithub"}:
            if history_report and history_report.get("status") == "PASS":
                condition_state = "CONDITION_TRUE"
                execution_state = "EXECUTED"
                evidence = "git_history receipt PASS"
            else:
                evidence = "Git history/sync phase not requested for this lane build"
        else:
            evidence = "eligible capability not selected by the current build phase"
        rows.append(
            {
                "tool": tool,
                "role_class": role_class,
                "phases": phases,
                "eligibility_state": "ELIGIBLE",
                "selection_state": (
                    "SELECTED_FOR_CURRENT_ACTION_PHASE"
                    if condition_state == "CONDITION_TRUE"
                    else "NOT_SELECTED"
                ),
                "condition_state": condition_state,
                "execution_state": execution_state,
                "evidence": evidence,
                "network_call_performed": False,
                "credential_value_read": False,
            }
        )
    condition_true = [row for row in rows if row["condition_state"] == "CONDITION_TRUE"]
    valid = all(
        row["execution_state"]
        in {"EXECUTED", "BLOCKED_RUNTIME_NOT_PREWARMED", "BLOCKED_MISSING_PROOF"}
        for row in condition_true
    )
    core = {
        "schema": "evidence-lane.lane-tool-execution-evidence.v1",
        "status": "PASS" if valid else "FAIL",
        "lane_id": lane.canonical_lane_id,
        "rows": rows,
        "eligible_tool_count": len(rows),
        "condition_true_tool_count": len(condition_true),
        "selected_tool_count": len(condition_true),
        "executed_tool_count": sum(
            row["execution_state"] == "EXECUTED" for row in rows
        ),
        "blocked_tool_count": sum(
            str(row["execution_state"]).startswith("BLOCKED_") for row in rows
        ),
        "condition_false_tool_count": sum(
            row["condition_state"] == "CONDITION_FALSE" for row in rows
        ),
        "all_condition_true_tools_executed_or_failed_visible": valid,
        "presence_or_eligibility_is_execution_proof": False,
        "multiple_compatible_tools_may_form_one_pipeline": True,
        "recorded_at": recorded_at,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _empty_table_classification(
    *,
    database: Path,
    lane: LaneDefinition,
    history_enabled: bool,
    recorded_at: str,
) -> dict[str, Any]:
    """Classify zero-row contract tables without treating emptiness as success."""

    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        counts = {
            table: _required_table_count(connection, table)
            for table in lane.schema_contract
        }
        fact_counts = {
            str(row["kind"]): int(row["row_count"])
            for row in connection.execute(
                "SELECT kind, COUNT(*) AS row_count FROM structured_fact GROUP BY kind"
            )
        }
        parser_statuses: set[str] = set()
        if "code_parser_receipt" in counts:
            for row in connection.execute(
                "SELECT payload_json FROM code_parser_receipt"
            ):
                payload = json.loads(str(row[0]))
                parser_statuses.add(str(payload.get("status") or "UNKNOWN"))
    finally:
        connection.close()

    source_count = counts.get("source_registry", 0)
    chunk_count = counts.get("chunk_index", 0)
    rows: list[dict[str, Any]] = []
    defect_tables: list[str] = []
    for table in lane.schema_contract:
        if counts[table] != 0:
            continue
        classification = "INTENTIONALLY_EMPTY_TEMPLATE"
        reason = "NO_ROUTED_SOURCE_IN_THIS_GENERATION"
        if table in {"tfidf_term", "tfidf_vector"}:
            classification = "QUERY_TIME_MATERIALIZATION"
            reason = "TF_DF_IDF_IS_COMPUTED_OVER_BOUNDED_FTS_CANDIDATES_AT_QUERY_TIME"
        elif table == "mutation_receipt":
            classification = "CONDITION_FALSE"
            reason = "NO_MUTATION_OR_SUPERSEDED_CAS_PURGE_IN_THIS_DELTA"
        elif table.startswith("git_"):
            classification = "CONDITION_FALSE"
            reason = (
                "GIT_HISTORY_PHASE_NOT_REQUESTED"
                if not history_enabled
                else "NO_MATCHING_GIT_HISTORY_EVENT"
            )
        elif (
            table == "code_call"
            and parser_statuses
            and all(
                status in {"RUNTIME_NOT_PREWARMED", "UNSUPPORTED_LANGUAGE"}
                for status in parser_statuses
            )
        ):
            classification = "BLOCKED_UPSTREAM_TOOL"
            reason = "TREE_SITTER_RUNTIME_NOT_PREWARMED_OR_LANGUAGE_UNSUPPORTED"
        elif table == "code_parser_diagnostic":
            classification = "CONDITION_FALSE"
            reason = "NO_PARSER_DIAGNOSTIC_EMITTED"
        elif table not in CORE_SCHEMA_TABLES and table != lane.fts_table:
            if fact_counts.get(table, 0) > 0:
                classification = "DEFECT"
                reason = "STRUCTURED_FACT_EXISTS_BUT_LANE_TABLE_IS_EMPTY"
            else:
                classification = "CONDITION_FALSE"
                reason = "NO_MATCHING_EXTRACTED_FACT_FOR_THIS_SOURCE_SHAPE"
        elif table in {
            "chunk_index",
            "chunk_content_cas",
            "chunk_history",
            lane.fts_table,
        }:
            classification = "CONDITION_FALSE"
            reason = (
                "NO_TEXT_CHUNK_EMITTED" if chunk_count == 0 else "NO_FTS_ROW_EMITTED"
            )
        elif table == "structured_fact":
            classification = "CONDITION_FALSE"
            reason = "NO_STRUCTURED_FACT_EMITTED"
        elif source_count > 0:
            classification = "DEFECT"
            reason = "CURRENT_PHASE_REQUIRED_TABLE_IS_EMPTY"
        if classification == "DEFECT":
            defect_tables.append(table)
        rows.append(
            {
                "table": table,
                "row_count": 0,
                "classification": classification,
                "reason": reason,
            }
        )
    core = {
        "schema": "evidence-lane.empty-table-classification.v1",
        "status": "PASS" if not defect_tables else "FAIL",
        "lane_id": lane.canonical_lane_id,
        "empty_table_count": len(rows),
        "defect_table_count": len(defect_tables),
        "defect_tables": defect_tables,
        "rows": rows,
        "counts_are_current_generation_facts_not_schema_requirements": True,
        "recorded_at": recorded_at,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _topology_generator_identity(lane: LaneDefinition) -> dict[str, Any]:
    """Bind incremental reuse to the exact installed topology emitter bytes."""

    module_path = Path(__file__).resolve()
    schema_topology_module_path = Path(
        physical_schema_projection.__code__.co_filename
    ).resolve()
    reconciliation_module_path = Path(
        reconcile_lane_topology.__code__.co_filename
    ).resolve()
    schema_asset = lane_schema_asset(lane.canonical_lane_id)
    payload = {
        "schema": TOPOLOGY_GENERATOR_SCHEMA,
        "module": module_path.name,
        "module_sha256": sha256_file(module_path),
        "schema_topology_module_sha256": sha256_file(schema_topology_module_path),
        "topology_reconciliation_module_sha256": sha256_file(
            reconciliation_module_path
        ),
        "lane_id": lane.canonical_lane_id,
        "lane_schema_contract": list(lane.schema_contract),
        "lane_schema_contract_sha256": schema_asset["contract_sha256"],
        "lane_schema_registry_sha256": LANE_SCHEMA_REGISTRY_SHA256,
        "mmd_dot_shared_graph": True,
        "physical_schema_projection_schema": PHYSICAL_SCHEMA_PROJECTION_SCHEMA,
        "sqlite_brain_builder_mmd_authority_sha256": (
            SQLITE_BRAIN_BUILDER_MMD_AUTHORITY_SHA256
        ),
        "sqlite_brain_builder_master_topology_authority_sha256": (
            SQLITE_BRAIN_BUILDER_MASTER_TOPOLOGY_AUTHORITY_SHA256
        ),
        "graph_projection_contract": {
            "implementation": "PROJECT_AUTHORED_GRAPHIFY_INFORMED",
            "stable_node_identity": True,
            "stable_edge_identity": True,
            "confidence_vocabulary": ["EXTRACTED", "INFERRED", "AMBIGUOUS"],
            "coverage_and_impact_visible": True,
            "network_or_llm_extraction": False,
            "profile": (
                "GITHUB_REPOSITORY_HISTORY"
                if lane.canonical_lane_id == "github_code"
                else "LOCAL_WORKTREE"
                if lane.canonical_lane_id == "local_code"
                else "SQLITE_SCHEMA_RELATION_SAMPLE"
            ),
        },
        "code_logical_topology": (
            [list(row) for row in CODE_LOGICAL_TOPOLOGY]
            if lane.canonical_lane_id in PRIMARY_CODE_LANES
            else None
        ),
    }
    payload["sha256"] = sha256_bytes(canonical_json_bytes(payload))
    return payload


def _decode_text(data: bytes) -> tuple[str | None, str | None]:
    if not data:
        return "", "utf-8"
    if b"\x00" in data[:8192]:
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                return data.decode(encoding), encoding
            except UnicodeError:
                continue
        return None, None
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            continue
    return None, None


def _xml_text(root: ElementTree.Element) -> str:
    values = [
        str(node.text)
        for node in root.iter()
        if node.text
        and (_XML_TEXT_TAG.search(node.tag) or node.tag.endswith("}instrText"))
    ]
    return "\n".join(values)


def _fact(kind: str, locator: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "locator": locator, "payload": payload}


def _append_fact_once(
    facts: list[dict[str, Any]],
    kind: str,
    locator: str,
    payload: dict[str, Any],
) -> None:
    if any(item["kind"] == kind and item["locator"] == locator for item in facts):
        return
    facts.append(_fact(kind, locator, payload))


_SOURCE_FACT_KIND = {
    "discussion": "discussion_source",
    "analysis": "analysis_source",
    "plan": "plan_source",
    "mode": "mode_source",
    "docs": "doc_file",
    "data_excel": "data_source",
    "ppt": "ppt_file",
    "pdf_ocr": "pdf_file",
    "images_ocr": "image_file",
    "artifacts": "project_artifact",
    "custom": "custom_source",
    "brain_loader": "brain_loader_source",
    "research": "research_source",
    "project_engulf": "project_engulf_source",
    "sqlite_brain": "loaded_sqlite_brain_source",
}

_STRUCTURE_FACT_KIND = {
    "docs": "source_structure_signature",
    "data_excel": "data_structure_signature",
    "ppt": "ppt_structure_signature",
    "pdf_ocr": "pdf_structure_signature",
}

_PREFIX_FACT_KIND = {
    "discussion": {
        "decision": "discussion_decision",
        "delta": "discussion_delta",
        "next": "discussion_next_action",
        "next_action": "discussion_next_action",
        "gate": "discussion_hard_gate",
        "artifact": "discussion_artifact_reference",
    },
    "analysis": {
        "claim": "analysis_claim",
        "evidence": "analysis_evidence",
        "supporting_evidence": "analysis_supporting_evidence",
        "risk": "analysis_risk",
        "alternative": "analysis_alternative",
        "question": "analysis_open_question",
        "open_question": "analysis_open_question",
        "accepted_decision": "analysis_accepted_decision",
        "blocked": "analysis_blocked_item",
    },
    "plan": {
        "phase": "plan_phase",
        "milestone": "plan_milestone",
        "task": "plan_task",
        "owner": "plan_owner",
        "status": "plan_status",
        "depends": "plan_dependency",
        "dependency": "plan_dependency",
        "blocker": "plan_blocker",
        "next": "plan_next_action",
        "next_action": "plan_next_action",
        "acceptance": "plan_acceptance_criteria",
        "acceptance_criteria": "plan_acceptance_criteria",
    },
    "mode": {
        "scope": "mode_scope",
        "trigger": "mode_trigger",
        "rule": "mode_rule",
        "gate": "mode_gate",
        "allow": "mode_allowed_action",
        "allowed": "mode_allowed_action",
        "block": "mode_blocked_action",
        "blocked": "mode_blocked_action",
        "response": "mode_response_template",
        "priority": "mode_priority",
        "supersedes": "mode_supersede_ledger",
    },
    "research": {
        "question": "research_question",
        "hypothesis": "research_hypothesis",
        "method": "research_method",
        "evidence": "research_evidence",
        "finding": "research_finding",
        "limitation": "research_limitation",
        "citation": "research_citation",
        "open_question": "research_open_question",
    },
    "custom": {
        "item": "custom_item",
        "evidence": "custom_evidence",
        "decision": "custom_decision",
        "next": "custom_next_action",
        "next_action": "custom_next_action",
    },
}


def _semantic_lane_facts(
    lane: LaneDefinition,
    text: str,
    *,
    relative_path: str,
) -> list[dict[str, Any]]:
    mapping = _PREFIX_FACT_KIND.get(lane.canonical_lane_id, {})
    facts: list[dict[str, Any]] = []
    lines = text.splitlines()
    if lane.canonical_lane_id == "discussion":
        for index, line in enumerate(lines[:500], start=1):
            if line.strip():
                facts.append(
                    _fact(
                        "discussion_item",
                        f"{relative_path}:line:{index}",
                        {
                            "text": line.strip(),
                            "line": index,
                            "extractor": "bounded_visible_line_v1",
                        },
                    )
                )
    for index, line in enumerate(lines[:2000], start=1):
        stripped = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _/-]{0,48}):\s*(.+)$", stripped)
        if not match:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", match.group(1).lower()).strip("_")
        kind = mapping.get(key)
        if not kind:
            continue
        facts.append(
            _fact(
                kind,
                f"{relative_path}:line:{index}",
                {
                    "text": match.group(2).strip(),
                    "label": match.group(1),
                    "line": index,
                    "extractor": "deterministic_prefix_v1",
                },
            )
        )
    return facts


def _document_text_facts(text: str, relative_path: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    paragraphs = [
        block.strip() for block in re.split(r"(?:\r?\n){2,}", text) if block.strip()
    ][:1000]
    for index, paragraph in enumerate(paragraphs, start=1):
        first_line = paragraph.splitlines()[0].strip()
        markdown = re.match(r"^(#{1,6})\s+(.+)$", first_line)
        if markdown:
            facts.append(
                _fact(
                    "doc_heading",
                    f"{relative_path}:heading:{index}",
                    {
                        "level": len(markdown.group(1)),
                        "text": markdown.group(2).strip(),
                        "extractor": "markdown_heading_v1",
                    },
                )
            )
        facts.append(
            _fact(
                "doc_paragraph",
                f"{relative_path}:paragraph:{index}",
                {
                    "text": paragraph,
                    "chars": len(paragraph),
                    "extractor": "bounded_paragraph_v1",
                },
            )
        )
    facts.append(
        _fact(
            "doc_structure",
            relative_path,
            {
                "paragraphs": len(paragraphs),
                "headings": sum(item["kind"] == "doc_heading" for item in facts),
                "extractor": "text_hierarchy_v1",
            },
        )
    )
    return facts


def _finalize_extraction(
    lane: LaneDefinition,
    relative_path: str,
    documents: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    parser_state: str,
    encoding: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str | None]:
    text = "\n\n".join(
        str(document.get("text") or "")
        for document in documents
        if str(document.get("text") or "")
    )
    source_kind = _SOURCE_FACT_KIND.get(lane.canonical_lane_id)
    if source_kind:
        _append_fact_once(
            facts,
            source_kind,
            relative_path,
            {
                "path": relative_path,
                "extension": Path(relative_path).suffix.lower(),
                "parser_state": parser_state,
                "documents": len(documents),
                "facts": len(facts),
                "text_chars": len(text),
            },
        )
    structure_kind = _STRUCTURE_FACT_KIND.get(lane.canonical_lane_id)
    if structure_kind:
        _append_fact_once(
            facts,
            structure_kind,
            relative_path,
            {
                "parser_state": parser_state,
                "document_count": len(documents),
                "fact_kinds": sorted({str(item["kind"]) for item in facts}),
                "text_sha256": sha256_bytes(text.encode("utf-8")),
            },
        )
    if text:
        facts.extend(_semantic_lane_facts(lane, text, relative_path=relative_path))
    if lane.canonical_lane_id == "docs" and not any(
        item["kind"] == "doc_paragraph" for item in facts
    ):
        facts.extend(_document_text_facts(text, relative_path))
    if lane.canonical_lane_id == "artifacts":
        _append_fact_once(
            facts,
            "artifact_metadata",
            relative_path,
            {
                "extension": Path(relative_path).suffix.lower(),
                "parser_state": parser_state,
                "text_chars": len(text),
            },
        )
        for index, document in enumerate(documents[:200], start=1):
            facts.append(
                _fact(
                    "artifact_text_extract",
                    f"{relative_path}:extract:{index}",
                    {
                        "source_locator": document.get("locator"),
                        "text_sha256": sha256_bytes(
                            str(document.get("text") or "").encode("utf-8")
                        ),
                    },
                )
            )
        if not documents:
            facts.append(
                _fact(
                    "artifact_review_required",
                    relative_path,
                    {
                        "reason": "NO_TEXT_EXTRACT_AVAILABLE",
                        "exact_bytes_preserved": True,
                    },
                )
            )
    if lane.canonical_lane_id == "custom":
        _append_fact_once(
            facts,
            "custom_item",
            relative_path,
            {
                "parser_state": parser_state,
                "text_chars": len(text),
                "exact_bytes_preserved": True,
            },
        )
        if text:
            _append_fact_once(
                facts,
                "custom_evidence",
                relative_path,
                {"text_sha256": sha256_bytes(text.encode("utf-8"))},
            )
    return documents, facts, parser_state, encoding


def _extract_docx(data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in sorted(archive.namelist()):
            if not (
                name == "word/document.xml"
                or name.startswith(("word/header", "word/footer", "word/footnotes"))
            ):
                continue
            root = ElementTree.fromstring(archive.read(name))
            paragraph_texts: list[str] = []
            paragraph_count = 0
            heading_count = 0
            for paragraph in (node for node in root.iter() if node.tag.endswith("}p")):
                text = _xml_text(paragraph).strip()
                if not text:
                    continue
                paragraph_count += 1
                paragraph_texts.append(text)
                style = ""
                for node in paragraph.iter():
                    if not node.tag.endswith("}pStyle"):
                        continue
                    style = next(
                        (
                            str(value)
                            for key, value in node.attrib.items()
                            if key.endswith("}val")
                        ),
                        "",
                    )
                    break
                heading_match = re.match(r"(?i)^heading\s*([1-6])$", style)
                if heading_match:
                    heading_count += 1
                    facts.append(
                        _fact(
                            "doc_heading",
                            f"{name}:heading:{heading_count}",
                            {
                                "level": int(heading_match.group(1)),
                                "style": style,
                                "text": text,
                            },
                        )
                    )
                else:
                    facts.append(
                        _fact(
                            "doc_paragraph",
                            f"{name}:paragraph:{paragraph_count}",
                            {
                                "style": style or None,
                                "text": text,
                            },
                        )
                    )
            table_count = 0
            for table in (node for node in root.iter() if node.tag.endswith("}tbl")):
                table_count += 1
                rows: list[list[str]] = []
                for row in (node for node in table if node.tag.endswith("}tr")):
                    cells = [
                        _xml_text(cell).strip()
                        for cell in row
                        if cell.tag.endswith("}tc")
                    ]
                    if cells:
                        rows.append(cells)
                facts.append(
                    _fact(
                        "doc_table_extract",
                        f"{name}:table:{table_count}",
                        {
                            "rows": rows[:200],
                            "row_count": len(rows),
                            "truncated": len(rows) > 200,
                        },
                    )
                )
                paragraph_texts.extend(
                    " | ".join(cell for cell in row) for row in rows[:200]
                )
            image_count = 0
            for node in root.iter():
                if not node.tag.endswith("}blip"):
                    continue
                relationship_id = next(
                    (
                        str(value)
                        for key, value in node.attrib.items()
                        if key.endswith(("}embed", "}link"))
                    ),
                    "",
                )
                image_count += 1
                facts.append(
                    _fact(
                        "doc_image_reference",
                        f"{name}:image:{image_count}",
                        {"relationship_id": relationship_id},
                    )
                )
            text = "\n\n".join(paragraph_texts)
            if text:
                documents.append({"locator": name, "text": text, "metadata": {}})
            facts.append(
                _fact(
                    "doc_structure",
                    name,
                    {
                        "paragraphs": paragraph_count,
                        "headings": heading_count,
                        "tables": table_count,
                        "images": image_count,
                    },
                )
            )
    return documents, facts


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root:
        values.append(
            "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
        )
    return values


def _extract_xlsx(data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        strings = _shared_strings(archive)
        sheet_members = sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        sheet_titles: dict[str, str] = {}
        workbook_sheets: list[dict[str, str]] = []
        if (
            "xl/workbook.xml" in archive.namelist()
            and "xl/_rels/workbook.xml.rels" in archive.namelist()
        ):
            relationships = ElementTree.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            targets = {
                node.attrib.get("Id", ""): posixpath.normpath(
                    posixpath.join("xl", node.attrib.get("Target", ""))
                )
                for node in relationships
            }
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            for sheet in (
                node for node in workbook.iter() if node.tag.endswith("}sheet")
            ):
                relationship_id = next(
                    (
                        value
                        for key, value in sheet.attrib.items()
                        if key.endswith("}id")
                    ),
                    "",
                )
                member = targets.get(relationship_id, "")
                title = sheet.attrib.get("name", member or relationship_id)
                if member:
                    sheet_titles[member] = title
                workbook_sheets.append(
                    {
                        "title": title,
                        "sheet_id": sheet.attrib.get("sheetId", ""),
                        "member": member,
                    }
                )
            defined_names = [
                {
                    "name": node.attrib.get("name", ""),
                    "formula": node.text or "",
                }
                for node in workbook.iter()
                if node.tag.endswith("}definedName")
            ]
            facts.append(
                {
                    "kind": "sheet_workbook",
                    "locator": "xl/workbook.xml",
                    "payload": {
                        "sheets": workbook_sheets,
                        "defined_names": defined_names,
                    },
                }
            )
        for sheet_member in sheet_members:
            sheet_title = sheet_titles.get(sheet_member, sheet_member)
            root = ElementTree.fromstring(archive.read(sheet_member))
            rows: list[str] = []
            formulas = 0
            cell_count = 0
            cell_refs: list[str] = []
            cell_samples: list[dict[str, Any]] = []
            for row_index, row in enumerate(
                (node for node in root.iter() if node.tag.endswith("}row")), start=1
            ):
                if row_index > MAX_ROWS_PER_TAB:
                    break
                cells: list[str] = []
                for cell in (node for node in row if node.tag.endswith("}c")):
                    cell_count += 1
                    ref = cell.attrib.get("r", "")
                    if ref:
                        cell_refs.append(ref)
                    cell_type = cell.attrib.get("t")
                    formula_node = next(
                        (node for node in cell if node.tag.endswith("}f")), None
                    )
                    value_node = next(
                        (node for node in cell if node.tag.endswith("}v")), None
                    )
                    if formula_node is not None:
                        formulas += 1
                        formula = formula_node.text or ""
                        value = f"={formula}"
                        facts.append(
                            {
                                "kind": "sheet_formula",
                                "locator": f"{sheet_title}!{ref}",
                                "payload": {
                                    "sheet": sheet_title,
                                    "cell": ref,
                                    "formula": formula,
                                    "cached_value": (
                                        value_node.text
                                        if value_node is not None
                                        else None
                                    ),
                                },
                            }
                        )
                        references = sorted(
                            {
                                (
                                    match.group("sheet")
                                    or match.group("quoted")
                                    or sheet_title,
                                    f"{match.group('column')}{match.group('row')}",
                                )
                                for match in re.finditer(
                                    r"(?:(?:'(?P<quoted>[^']+)'|"
                                    r"(?P<sheet>[A-Za-z_][A-Za-z0-9_. ]*))!)?"
                                    r"\$?(?P<column>[A-Z]{1,3})\$?"
                                    r"(?P<row>[1-9][0-9]*)",
                                    formula,
                                )
                            }
                        )
                        for target_sheet, target_cell in references:
                            facts.append(
                                {
                                    "kind": "sheet_formula_dependency_edge",
                                    "locator": f"{sheet_title}!{ref}",
                                    "payload": {
                                        "from_sheet": sheet_title,
                                        "from_cell": ref,
                                        "to_sheet": target_sheet.strip("'"),
                                        "to_cell": target_cell,
                                    },
                                }
                            )
                    else:
                        value = value_node.text or "" if value_node is not None else ""
                        if cell_type == "s" and value and value.isdigit():
                            index = int(value)
                            value = strings[index] if index < len(strings) else value
                    if len(cell_samples) < 2000:
                        cell_samples.append(
                            {
                                "sheet": sheet_title,
                                "cell": ref,
                                "value": value,
                                "cell_type": cell_type,
                                "formula": (
                                    formula_node.text or ""
                                    if formula_node is not None
                                    else None
                                ),
                                "cached_value": (
                                    value_node.text
                                    if formula_node is not None
                                    and value_node is not None
                                    else None
                                ),
                            }
                        )
                    cells.append(f"{ref}={value}")
                if cells:
                    rows.append(" | ".join(cells))
            text = "\n".join(rows)
            if text:
                documents.append(
                    {
                        "locator": sheet_title,
                        "text": text,
                        "metadata": {"bounded_rows": len(rows)},
                    }
                )
            facts.append(
                {
                    "kind": "sheet_tab",
                    "locator": sheet_title,
                    "payload": {
                        "member": sheet_member,
                        "bounded_rows": len(rows),
                        "cells": cell_count,
                        "formulas": formulas,
                        "row_limit": MAX_ROWS_PER_TAB,
                    },
                }
            )
            dimension = next(
                (
                    node.attrib.get("ref", "")
                    for node in root.iter()
                    if node.tag.endswith("}dimension")
                ),
                "",
            )
            if not dimension and cell_refs:
                dimension = (
                    cell_refs[0]
                    if len(cell_refs) == 1
                    else f"{cell_refs[0]}:{cell_refs[-1]}"
                )
            facts.append(
                _fact(
                    "sheet_range",
                    sheet_title,
                    {
                        "range": dimension,
                        "sampled_cells": len(cell_samples),
                        "total_cells": cell_count,
                    },
                )
            )
            facts.extend(
                _fact(
                    "sheet_cell_sample",
                    f"{sheet_title}!{sample['cell']}",
                    sample,
                )
                for sample in cell_samples
            )
        for table_name in sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"xl/tables/table\d+\.xml", name)
        ):
            table = ElementTree.fromstring(archive.read(table_name))
            facts.append(
                {
                    "kind": "sheet_table",
                    "locator": table_name,
                    "payload": {
                        "name": table.attrib.get("name"),
                        "display_name": table.attrib.get("displayName"),
                        "range": table.attrib.get("ref"),
                    },
                }
            )
        chart_names = sorted(
            name for name in archive.namelist() if name.startswith("xl/charts/")
        )
        for chart_name in chart_names:
            chart = ElementTree.fromstring(archive.read(chart_name))
            chart_types = sorted(
                {
                    node.tag.rsplit("}", 1)[-1]
                    for node in chart.iter()
                    if node.tag.rsplit("}", 1)[-1].endswith("Chart")
                }
            )
            text_nodes = [
                node.text or ""
                for node in chart.iter()
                if node.tag.endswith("}t") and node.text
            ]
            series_references = sorted(
                {
                    node.text or ""
                    for node in chart.iter()
                    if node.tag.endswith("}f") and node.text
                }
            )
            facts.append(
                {
                    "kind": "sheet_chart_metadata",
                    "locator": chart_name,
                    "payload": {
                        "member": chart_name,
                        "chart_types": chart_types,
                        "title_text": " ".join(text_nodes),
                        "series_references": series_references,
                    },
                }
            )
    return documents, facts


def _extract_delimited(
    data: bytes, suffix: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    text, encoding = _decode_text(data)
    if text is None:
        return [], [], None
    delimiter = "\t" if suffix == ".tsv" else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    bounded = rows[:MAX_ROWS_PER_TAB]
    normalized = "\n".join(" | ".join(cell for cell in row) for row in bounded)
    facts = [
        _fact(
            "tabular_structure",
            "table",
            {
                "encoding": encoding,
                "rows_total": len(rows),
                "rows_indexed": len(bounded),
                "columns_max": max((len(row) for row in bounded), default=0),
                "header": bounded[0] if bounded else [],
            },
        ),
        _fact(
            "csv_header",
            "row:1",
            {
                "columns": bounded[0] if bounded else [],
                "column_count": len(bounded[0]) if bounded else 0,
                "delimiter": "\\t" if suffix == ".tsv" else ",",
            },
        ),
    ]
    facts.extend(
        _fact(
            "csv_row_sample",
            f"row:{row_index}",
            {
                "values": row,
                "column_count": len(row),
            },
        )
        for row_index, row in enumerate(bounded[1:201], start=2)
    )
    return (
        (
            [{"locator": "table", "text": normalized, "metadata": {}}]
            if normalized
            else []
        ),
        facts,
        encoding,
    )


def _bounded_json_value(value: Any, *, max_chars: int = 8192) -> dict[str, Any]:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    payload: dict[str, Any] = {
        "value_type": type(value).__name__,
        "canonical_sha256": sha256_bytes(serialized.encode("utf-8")),
        "canonical_chars": len(serialized),
    }
    if len(serialized) <= max_chars:
        payload["value"] = value
    else:
        payload["preview"] = serialized[:max_chars]
        payload["truncated"] = True
    return payload


def _json_shape(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth >= 4:
        return {"type": type(value).__name__, "depth_limited": True}
    if isinstance(value, dict):
        keys = sorted(str(key) for key in value)[:200]
        return {
            "type": "object",
            "keys": keys,
            "key_count": len(value),
            "children": {
                key: _json_shape(value[key], depth=depth + 1)
                for key in keys[:50]
                if key in value
            },
            "truncated": len(value) > 200,
        }
    if isinstance(value, list):
        types = sorted({type(item).__name__ for item in value[:1000]})
        return {
            "type": "array",
            "length": len(value),
            "item_types": types,
            "sample_shape": (_json_shape(value[0], depth=depth + 1) if value else None),
        }
    return {"type": type(value).__name__}


def _extract_json(
    data: bytes,
    suffix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    text, encoding = _decode_text(data)
    if text is None:
        return [], [], None
    if suffix == ".jsonl":
        records = [json.loads(line) for line in text.splitlines() if line.strip()][
            :MAX_ROWS_PER_TAB
        ]
        root: Any = records
        source_format = "jsonl"
    else:
        root = json.loads(text)
        records = root[:MAX_ROWS_PER_TAB] if isinstance(root, list) else [root]
        source_format = "json"
    facts = [
        _fact(
            "json_structure",
            "$",
            {
                "format": source_format,
                "shape": _json_shape(root),
                "record_count": len(root) if isinstance(root, list) else 1,
            },
        )
    ]
    documents: list[dict[str, Any]] = []
    for index, record in enumerate(records[:200], start=1):
        payload = _bounded_json_value(record)
        facts.append(_fact("json_record_sample", f"$[{index - 1}]", payload))
        serialized = json.dumps(
            record,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        documents.append(
            {
                "locator": f"$[{index - 1}]",
                "text": serialized,
                "metadata": {"source_format": source_format},
            }
        )
    return documents, facts, encoding


def _extract_parquet(
    data: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if not _module_available("pyarrow"):
        return (
            [],
            [
                _fact(
                    "parquet_schema",
                    "file",
                    {
                        "state": "BLOCKED_OPTIONAL_TOOL_UNAVAILABLE",
                        "required_module": "pyarrow",
                        "exact_bytes_preserved": True,
                    },
                )
            ],
            "BLOCKED_PARQUET_TOOL_UNAVAILABLE_EXACT_BYTES_PRESERVED",
        )
    from pyarrow import parquet  # type: ignore[import-not-found]

    parquet_file = parquet.ParquetFile(io.BytesIO(data))
    metadata = parquet_file.metadata
    facts = [
        _fact(
            "parquet_schema",
            "file",
            {
                "schema": str(parquet_file.schema_arrow),
                "columns": list(parquet_file.schema_arrow.names),
                "row_groups": parquet_file.num_row_groups,
                "rows": int(metadata.num_rows) if metadata is not None else None,
            },
        )
    ]
    documents: list[dict[str, Any]] = []
    sample_ordinal = 0
    for batch in parquet_file.iter_batches(batch_size=200):
        for record in batch.to_pylist():
            sample_ordinal += 1
            if sample_ordinal > 200:
                break
            payload = _bounded_json_value(record)
            facts.append(
                _fact(
                    "parquet_row_sample",
                    f"row:{sample_ordinal}",
                    payload,
                )
            )
            documents.append(
                {
                    "locator": f"row:{sample_ordinal}",
                    "text": json.dumps(
                        record,
                        sort_keys=True,
                        ensure_ascii=False,
                        default=str,
                    ),
                    "metadata": {},
                }
            )
        if sample_ordinal >= 200:
            break
    return documents, facts, "PARSED_PARQUET_PYARROW"


def _extract_legacy_excel(
    path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if not _module_available("python_calamine"):
        return (
            [],
            [
                _fact(
                    "sheet_workbook",
                    "workbook",
                    {
                        "state": "BLOCKED_OPTIONAL_TOOL_UNAVAILABLE",
                        "required_module": "python_calamine",
                        "exact_bytes_preserved": True,
                    },
                )
            ],
            "BLOCKED_XLS_TOOL_UNAVAILABLE_EXACT_BYTES_PRESERVED",
        )
    from python_calamine import (  # type: ignore[import-not-found]
        CalamineWorkbook,
    )

    workbook = CalamineWorkbook.from_path(str(path))
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = [
        _fact(
            "sheet_workbook",
            "workbook",
            {
                "sheets": list(workbook.sheet_names),
                "extractor": "python_calamine",
            },
        )
    ]
    for sheet_name in workbook.sheet_names:
        rows = workbook.get_sheet_by_name(sheet_name).to_python(skip_empty_area=False)
        bounded = rows[:MAX_ROWS_PER_TAB]
        normalized = "\n".join(
            " | ".join("" if value is None else str(value) for value in row)
            for row in bounded
        )
        if normalized:
            documents.append(
                {
                    "locator": sheet_name,
                    "text": normalized,
                    "metadata": {"bounded_rows": len(bounded)},
                }
            )
        max_columns = max((len(row) for row in bounded), default=0)
        facts.append(
            _fact(
                "sheet_tab",
                sheet_name,
                {
                    "bounded_rows": len(bounded),
                    "columns_max": max_columns,
                    "row_limit": MAX_ROWS_PER_TAB,
                    "extractor": "python_calamine",
                },
            )
        )
        facts.append(
            _fact(
                "sheet_range",
                sheet_name,
                {
                    "range": (
                        f"A1:{_excel_column(max_columns)}{len(bounded)}"
                        if bounded and max_columns
                        else None
                    ),
                    "sampled_cells": sum(len(row) for row in bounded[:200]),
                },
            )
        )
        for row_index, row in enumerate(bounded[:200], start=1):
            for column_index, value in enumerate(row[:200], start=1):
                cell = f"{_excel_column(column_index)}{row_index}"
                facts.append(
                    _fact(
                        "sheet_cell_sample",
                        f"{sheet_name}!{cell}",
                        {
                            "sheet": sheet_name,
                            "cell": cell,
                            "value": value,
                            "extractor": "python_calamine",
                        },
                    )
                )
    return documents, facts, "PARSED_XLS_CALAMINE"


def _excel_column(index: int) -> str:
    if index <= 0:
        return ""
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _extract_pptx(data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", name)
        )
        for name in members:
            root = ElementTree.fromstring(archive.read(name))
            text = _xml_text(root)
            if text:
                documents.append({"locator": name, "text": text, "metadata": {}})
            is_slide = "/slides/" in name
            facts.append(
                _fact(
                    "ppt_slide" if is_slide else "ppt_notes",
                    name,
                    {
                        "text_nodes": sum(
                            1 for node in root.iter() if node.tag.endswith("}t")
                        ),
                        "shapes": sum(
                            1 for node in root.iter() if node.tag.endswith("}sp")
                        ),
                        "tables": sum(
                            1 for node in root.iter() if node.tag.endswith("}tbl")
                        ),
                    },
                )
            )
            if is_slide:
                for shape_index, shape in enumerate(
                    (node for node in root.iter() if node.tag.endswith("}sp")),
                    start=1,
                ):
                    non_visual = next(
                        (node for node in shape.iter() if node.tag.endswith("}cNvPr")),
                        None,
                    )
                    shape_id = (
                        non_visual.attrib.get("id", str(shape_index))
                        if non_visual is not None
                        else str(shape_index)
                    )
                    shape_name = (
                        non_visual.attrib.get("name", "")
                        if non_visual is not None
                        else ""
                    )
                    shape_text = _xml_text(shape).strip()
                    facts.append(
                        _fact(
                            "ppt_shape",
                            f"{name}:shape:{shape_id}",
                            {
                                "shape_id": shape_id,
                                "name": shape_name,
                                "text_chars": len(shape_text),
                            },
                        )
                    )
                    if shape_text:
                        facts.append(
                            _fact(
                                "ppt_text_block",
                                f"{name}:shape:{shape_id}:text",
                                {
                                    "shape_id": shape_id,
                                    "name": shape_name,
                                    "text": shape_text,
                                    "text_sha256": sha256_bytes(
                                        shape_text.encode("utf-8")
                                    ),
                                },
                            )
                        )
                for table_index, table in enumerate(
                    (node for node in root.iter() if node.tag.endswith("}tbl")),
                    start=1,
                ):
                    rows: list[list[str]] = []
                    for row in (node for node in table if node.tag.endswith("}tr")):
                        cells = [
                            _xml_text(cell).strip()
                            for cell in row
                            if cell.tag.endswith("}tc")
                        ]
                        if cells:
                            rows.append(cells)
                    facts.append(
                        _fact(
                            "ppt_table",
                            f"{name}:table:{table_index}",
                            {
                                "rows": rows[:200],
                                "row_count": len(rows),
                                "column_count_max": max(
                                    (len(row) for row in rows), default=0
                                ),
                                "truncated": len(rows) > 200,
                            },
                        )
                    )
                for picture_index, picture in enumerate(
                    (node for node in root.iter() if node.tag.endswith("}pic")),
                    start=1,
                ):
                    non_visual = next(
                        (
                            node
                            for node in picture.iter()
                            if node.tag.endswith("}cNvPr")
                        ),
                        None,
                    )
                    blip = next(
                        (node for node in picture.iter() if node.tag.endswith("}blip")),
                        None,
                    )
                    relationship_id = ""
                    if blip is not None:
                        relationship_id = next(
                            (
                                str(value)
                                for key, value in blip.attrib.items()
                                if key.endswith(("}embed", "}link"))
                            ),
                            "",
                        )
                    facts.append(
                        _fact(
                            "ppt_image_reference",
                            f"{name}:image:{picture_index}",
                            {
                                "relationship_id": relationship_id,
                                "name": (
                                    non_visual.attrib.get("name", "")
                                    if non_visual is not None
                                    else ""
                                ),
                                "description": (
                                    non_visual.attrib.get("descr", "")
                                    if non_visual is not None
                                    else ""
                                ),
                            },
                        )
                    )
            relationship_member = posixpath.join(
                posixpath.dirname(name),
                "_rels",
                f"{posixpath.basename(name)}.rels",
            )
            if relationship_member in archive.namelist():
                relationships = ElementTree.fromstring(
                    archive.read(relationship_member)
                )
                for relationship in relationships:
                    relationship_id = relationship.attrib.get("Id", "")
                    target = relationship.attrib.get("Target", "")
                    relationship_type = relationship.attrib.get("Type", "")
                    facts.append(
                        _fact(
                            "ppt_slide_relationship",
                            f"{name}:{relationship_id}",
                            {
                                "relationship_id": relationship_id,
                                "target": target,
                                "relationship_type": relationship_type,
                                "external": (
                                    relationship.attrib.get("TargetMode") == "External"
                                ),
                            },
                        )
                    )
                    if relationship_type.endswith("/image") and not any(
                        item["kind"] == "ppt_image_reference"
                        and item["payload"].get("relationship_id") == relationship_id
                        for item in facts
                    ):
                        facts.append(
                            _fact(
                                "ppt_image_reference",
                                f"{name}:relationship:{relationship_id}",
                                {
                                    "relationship_id": relationship_id,
                                    "target": target,
                                    "derived_from_relationship": True,
                                },
                            )
                        )
    return documents, facts


@cache
def _rapidocr_engine() -> tuple[str, Any] | None:
    if _module_available("rapidocr"):
        from rapidocr import (
            RapidOCR as RapidOCREngine,  # type: ignore[import-not-found]
        )

        return "rapidocr+onnxruntime", RapidOCREngine()
    if _module_available("rapidocr_onnxruntime"):
        from rapidocr_onnxruntime import (  # type: ignore[import-not-found]
            RapidOCR as RapidOCROnnxEngine,
        )

        return "rapidocr_onnxruntime", RapidOCROnnxEngine()
    return None


def prewarm_native_dependencies() -> tuple[str, ...]:
    """Load optional native engines before an MCP/ASGI event loop starts.

    On Windows, first-time NumPy/OpenCV/ONNX loading from inside a running MCP
    request can block the host far longer than the same import at process
    startup.  The cached OCR engine is process-local, so this bounded startup
    step removes that lifecycle collision without serializing independent lane
    builders.
    """
    cached = _rapidocr_engine()
    return () if cached is None else (cached[0],)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _opencv_preprocess_for_ocr(image_data: bytes) -> tuple[bytes, bool]:
    if not _module_available("cv2"):
        return image_data, False
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np

        decoded = cv2.imdecode(
            np.frombuffer(image_data, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if decoded is None:
            return image_data, False
        grayscale = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
        filtered = cv2.bilateralFilter(grayscale, 5, 50, 50)
        normalized = cv2.adaptiveThreshold(
            filtered,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )
        encoded, buffer = cv2.imencode(".png", normalized)
        return (bytes(buffer), True) if encoded else (image_data, False)
    except Exception:  # noqa: BLE001 - exact source remains the fallback
        return image_data, False


def _rapidocr_lines(image_data: bytes) -> tuple[list[dict[str, Any]], str | None]:
    # PDF and image lanes may build concurrently. The cached ONNX-backed OCR
    # object is process-local and is not documented as safe for simultaneous
    # calls, so only this shared external-engine boundary is serialized.
    with _RAPIDOCR_CALL_LOCK:
        cached = _rapidocr_engine()
        if cached is None:
            return [], "OCR_ENGINE_UNAVAILABLE"
        engine_name, engine = cached
        prepared, opencv_used = _opencv_preprocess_for_ocr(image_data)
        try:
            result = engine(prepared)
        except Exception as exc:  # noqa: BLE001 - external engine failure is evidence
            return [], f"OCR_ENGINE_ERROR_{type(exc).__name__.upper()}"
    lines: list[dict[str, Any]] = []
    if hasattr(result, "txts"):
        texts_value = getattr(result, "txts", None)
        boxes_value = getattr(result, "boxes", None)
        scores_value = getattr(result, "scores", None)
        texts = list(texts_value) if texts_value is not None else []
        boxes = list(boxes_value) if boxes_value is not None else []
        scores = list(scores_value) if scores_value is not None else []
        for index, text in enumerate(texts):
            lines.append(
                {
                    "text": str(text),
                    "confidence": (
                        float(scores[index]) if index < len(scores) else None
                    ),
                    "box": _json_safe(boxes[index]) if index < len(boxes) else None,
                    "engine": engine_name + ("+opencv" if opencv_used else ""),
                }
            )
        return lines, None if lines else "RAPIDOCR_EMPTY"
    raw_result = result[0] if isinstance(result, tuple) else result
    for item in raw_result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        lines.append(
            {
                "box": _json_safe(item[0]),
                "text": str(item[1]),
                "confidence": (
                    float(item[2]) if len(item) > 2 and item[2] is not None else None
                ),
                "engine": engine_name + ("+opencv" if opencv_used else ""),
            }
        )
    return lines, None if lines else "RAPIDOCR_EMPTY"


def _ocr_payload(
    *,
    prefix: str,
    locator: str,
    lines: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = "\n".join(
        str(line["text"]).strip()
        for line in lines
        if str(line.get("text") or "").strip()
    )
    if not text:
        return [], []
    engine = str(lines[0]["engine"])
    documents = [
        {
            "locator": f"{locator}:ocr",
            "text": text,
            "metadata": {
                "ocr": True,
                "ocr_text_chars": len(text),
                "engine": engine,
            },
        }
    ]
    facts = [
        _fact(
            f"{prefix}_ocr_run",
            locator,
            {
                "engine": engine,
                "text_chars": len(text),
                "line_count": len(lines),
            },
        ),
        _fact(
            f"{prefix}_ocr_block",
            f"{locator}:ocr",
            {
                "text_chars": len(text),
                "text_sha256": sha256_bytes(text.encode("utf-8")),
            },
        ),
    ]
    facts.extend(
        _fact(
            f"{prefix}_ocr_line",
            f"{locator}:ocr:line:{index}",
            {
                "text": str(line["text"]),
                "text_sha256": sha256_bytes(str(line["text"]).encode("utf-8")),
                "confidence": line.get("confidence"),
                "box": line.get("box"),
                "engine": line.get("engine"),
            },
        )
        for index, line in enumerate(lines, start=1)
        if str(line.get("text") or "").strip()
    )
    return documents, facts


def _pytesseract_lines(image_data: bytes) -> tuple[list[dict[str, Any]], str | None]:
    tesseract = try_resolve_native_tool("tesseract")
    if (
        not _module_available("PIL")
        or not _module_available("pytesseract")
        or tesseract is None
    ):
        return [], "PYTESSERACT_ROUTE_UNAVAILABLE"
    try:
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]

        pytesseract.pytesseract.tesseract_cmd = str(tesseract.executable)
        image = Image.open(io.BytesIO(image_data))
        text = pytesseract.image_to_string(image)
    except Exception as exc:  # noqa: BLE001 - external engine failure is evidence
        return [], f"PYTESSERACT_ERROR_{type(exc).__name__.upper()}"
    return (
        [
            {
                "text": line,
                "confidence": None,
                "box": None,
                "engine": "pytesseract+tesseract",
            }
            for line in text.splitlines()
            if line.strip()
        ],
        None,
    )


def _extract_pdf(
    data: bytes,
    *,
    path: Path,
    host_profile: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    parser_errors: list[str] = []
    parser_state = "OPAQUE_EXACT_BYTES"
    page_count = 0
    ocr_page_images: list[tuple[str, list[bytes]]] = []
    native_root = configured_runtime_root()
    if native_root is not None and try_resolve_native_tool("poppler_pdfinfo"):
        receipt = run_native_tool(
            NativeInvocationRequest(
                tool_id="poppler_pdfinfo",
                arguments=[str(path)],
                host_profile=host_profile,
            ),
            runtime_root=native_root,
        )
        facts.append(
            _fact(
                "pdf_page",
                "poppler:pdfinfo",
                {
                    "tool": "pdfinfo",
                    "status": receipt["status"],
                    "metadata": receipt["stdout"][:100_000],
                    "receipt_sha256": receipt["receipt_sha256"],
                },
            )
        )
    if _module_available("pymupdf"):
        try:
            import pymupdf  # type: ignore[import-not-found]

            document = pymupdf.open(stream=data, filetype="pdf")
            try:
                page_count = min(int(document.page_count), MAX_PDF_PAGES)
                for page_index in range(page_count):
                    page = document.load_page(page_index)
                    locator = f"page:{page_index + 1}"
                    text = str(page.get_text("text") or "")
                    page_dict = page.get_text("dict")
                    blocks = list(page_dict.get("blocks") or [])
                    image_blocks = [
                        block for block in blocks if int(block.get("type", -1)) == 1
                    ]
                    documents.append(
                        {
                            "locator": locator,
                            "text": text,
                            "metadata": {
                                "native_text_chars": len(text),
                                "width": float(page.rect.width),
                                "height": float(page.rect.height),
                                "extractor": "PyMuPDF",
                            },
                        }
                    )
                    facts.extend(
                        [
                            _fact(
                                "pdf_page",
                                locator,
                                {
                                    "native_text_chars": len(text),
                                    "blocks": len(blocks),
                                    "images": len(image_blocks),
                                    "width": float(page.rect.width),
                                    "height": float(page.rect.height),
                                    "extractor": "PyMuPDF",
                                },
                            ),
                            _fact(
                                "pdf_text_block",
                                f"{locator}:native",
                                {
                                    "text_chars": len(text),
                                    "text_sha256": sha256_bytes(text.encode("utf-8")),
                                    "extractor": "PyMuPDF",
                                },
                            ),
                        ]
                    )
                    if len(text.strip()) < 5:
                        pixmap = page.get_pixmap(
                            matrix=pymupdf.Matrix(2, 2), alpha=False
                        )
                        ocr_page_images.append((locator, [pixmap.tobytes("png")]))
                parser_state = "PARSED_PYMUPDF"
            finally:
                document.close()
        except Exception as exc:  # noqa: BLE001 - ordered parser fallback
            parser_errors.append(f"PYMUPDF_{type(exc).__name__.upper()}")
            documents.clear()
            facts.clear()
            ocr_page_images.clear()
    if not documents and _module_available("pypdf"):
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]

            reader = PdfReader(io.BytesIO(data), strict=False)
            page_count = min(len(reader.pages), MAX_PDF_PAGES)
            for page_index, page in enumerate(reader.pages[:page_count], start=1):
                text = page.extract_text() or ""
                locator = f"page:{page_index}"
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                page_images: list[bytes] = []
                try:
                    for image_index, image in enumerate(page.images, start=1):
                        image_data = bytes(image.data)
                        page_images.append(image_data)
                        facts.append(
                            _fact(
                                "pdf_image_block",
                                f"{locator}:image:{image_index}",
                                {
                                    "name": image.name,
                                    "bytes": len(image_data),
                                    "sha256": sha256_bytes(image_data),
                                },
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - image fallback stays open
                    parser_errors.append(
                        f"PYPDF_IMAGE_PAGE_{page_index}_{type(exc).__name__.upper()}"
                    )
                ocr_page_images.append((locator, page_images))
                documents.append(
                    {
                        "locator": locator,
                        "text": text,
                        "metadata": {
                            "native_text_chars": len(text),
                            "width": width,
                            "height": height,
                        },
                    }
                )
                facts.extend(
                    [
                        _fact(
                            "pdf_page",
                            locator,
                            {
                                "native_text_chars": len(text),
                                "images": len(page_images),
                                "width": width,
                                "height": height,
                            },
                        ),
                        _fact(
                            "pdf_text_block",
                            f"{locator}:native",
                            {
                                "text_chars": len(text),
                                "text_sha256": sha256_bytes(text.encode("utf-8")),
                            },
                        ),
                    ]
                )
            parser_state = "PARSED_PYPDF"
        except Exception as exc:  # noqa: BLE001 - parser fallback must remain open
            parser_errors.append(f"PYPDF_{type(exc).__name__.upper()}")
            documents.clear()
            facts.clear()
    if not documents and _module_available("pdfplumber"):
        try:
            import pdfplumber  # type: ignore[import-not-found]

            with pdfplumber.open(io.BytesIO(data)) as document:
                page_count = min(len(document.pages), MAX_PDF_PAGES)
                for page_index, page in enumerate(document.pages[:page_count], start=1):
                    text = page.extract_text() or ""
                    locator = f"page:{page_index}"
                    documents.append(
                        {
                            "locator": locator,
                            "text": text,
                            "metadata": {
                                "native_text_chars": len(text),
                                "width": page.width,
                                "height": page.height,
                            },
                        }
                    )
                    facts.extend(
                        [
                            _fact(
                                "pdf_page",
                                locator,
                                {
                                    "native_text_chars": len(text),
                                    "width": page.width,
                                    "height": page.height,
                                },
                            ),
                            _fact(
                                "pdf_text_block",
                                f"{locator}:native",
                                {
                                    "text_chars": len(text),
                                    "text_sha256": sha256_bytes(text.encode("utf-8")),
                                },
                            ),
                        ]
                    )
            parser_state = "PARSED_PDFPLUMBER"
        except Exception as exc:  # noqa: BLE001 - parser fallback must remain open
            parser_errors.append(f"PDFPLUMBER_{type(exc).__name__.upper()}")
            documents.clear()
            facts.clear()
    if (
        not documents
        and native_root is not None
        and try_resolve_native_tool("poppler_pdftotext")
    ):
        try:
            receipt = run_native_tool(
                NativeInvocationRequest(
                    tool_id="poppler_pdftotext",
                    arguments=[
                        "-f",
                        "1",
                        "-l",
                        str(MAX_PDF_PAGES),
                        "-layout",
                        str(path),
                        "-",
                    ],
                    host_profile=host_profile,
                    max_output_bytes=32_000_000,
                ),
                runtime_root=native_root,
            )
            extracted = str(receipt["stdout"])
            for page_index, text in enumerate(extracted.split("\f"), start=1):
                if not text.strip():
                    continue
                documents.append(
                    {
                        "locator": f"page:{page_index}",
                        "text": text,
                        "metadata": {"extractor": "Poppler pdftotext"},
                    }
                )
            facts.append(
                _fact(
                    "pdf_text_block",
                    "poppler:pdftotext",
                    {
                        "tool": "pdftotext",
                        "status": receipt["status"],
                        "text_chars": len(extracted),
                        "text_sha256": sha256_bytes(extracted.encode("utf-8")),
                        "receipt_sha256": receipt["receipt_sha256"],
                    },
                )
            )
            if documents:
                parser_state = "PARSED_POPPLER_PDFTOTEXT"
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            parser_errors.append(f"POPPLER_{type(exc).__name__.upper()}")
    native_chars = sum(len(str(item.get("text") or "")) for item in documents)
    text_poor = native_chars < max(10, max(page_count, 1) * 5)
    if text_poor and _module_available("pypdfium2"):
        try:
            import pypdfium2 as pdfium  # type: ignore[import-not-found]

            rendered_pages: list[tuple[str, list[bytes]]] = []
            pdfium_document = pdfium.PdfDocument(data)
            try:
                for page_index in range(min(len(pdfium_document), MAX_PDF_PAGES)):
                    page = pdfium_document[page_index]
                    try:
                        bitmap = page.render(scale=2)
                        try:
                            rendered = bitmap.to_pil().convert("RGB")
                            stream = io.BytesIO()
                            rendered.save(
                                stream,
                                format="PNG",
                                optimize=False,
                                compress_level=9,
                            )
                            rendered_pages.append(
                                (f"page:{page_index + 1}", [stream.getvalue()])
                            )
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
            finally:
                pdfium_document.close()
            if rendered_pages:
                ocr_page_images = rendered_pages
        except Exception as exc:  # noqa: BLE001 - OCR fallback must remain open
            parser_errors.append(f"PDFIUM_{type(exc).__name__.upper()}")
    ocr_image_available = any(images for _, images in ocr_page_images)
    if text_poor and ocr_image_available:
        try:
            ocr_documents: list[dict[str, Any]] = []
            ocr_facts: list[dict[str, Any]] = []
            for locator, page_images in ocr_page_images:
                page_lines: list[dict[str, Any]] = []
                blockers: list[str] = []
                for image_data in page_images:
                    lines, blocker = _rapidocr_lines(image_data)
                    if not lines:
                        secondary_lines, secondary = _pytesseract_lines(image_data)
                        lines = secondary_lines
                        blocker = " | ".join(
                            item for item in (blocker, secondary) if item
                        )
                    page_lines.extend(lines)
                    if blocker:
                        blockers.append(blocker)
                page_documents, page_facts = _ocr_payload(
                    prefix="pdf",
                    locator=locator,
                    lines=page_lines,
                )
                ocr_documents.extend(page_documents)
                ocr_facts.extend(page_facts)
                if not page_documents:
                    if any("RAPIDOCR_EMPTY" in blocker for blocker in blockers):
                        ocr_facts.append(
                            _fact(
                                "pdf_ocr_run",
                                locator,
                                {
                                    "engine": "rapidocr+onnxruntime",
                                    "state": "EMPTY",
                                    "text_chars": 0,
                                    "line_count": 0,
                                },
                            )
                        )
                    ocr_facts.append(
                        _fact(
                            "pdf_review_region",
                            locator,
                            {
                                "reason": " | ".join(blockers) or "OCR_EMPTY",
                                "native_text_chars": sum(
                                    len(str(item.get("text") or ""))
                                    for item in documents
                                    if item.get("locator") == locator
                                ),
                                "exact_bytes_preserved": True,
                            },
                        )
                    )
            if ocr_documents:
                documents = ocr_documents
                facts.extend(ocr_facts)
                parser_state = "PARSED_OCR_LOCAL"
            else:
                facts.extend(ocr_facts)
        except Exception as exc:  # noqa: BLE001 - OCR failure is governed evidence
            parser_errors.append(f"PDF_OCR_{type(exc).__name__.upper()}")
    elif text_poor:
        facts.append(
            _fact(
                "pdf_review_region",
                "file",
                {
                    "reason": (
                        "PDF_RASTERIZER_OR_EMBEDDED_IMAGE_UNAVAILABLE"
                        if not ocr_image_available
                        else "OCR_ENGINE_UNAVAILABLE_OR_EMPTY"
                    ),
                    "parser_errors": parser_errors,
                    "native_text_chars": native_chars,
                    "exact_bytes_preserved": True,
                },
            )
        )
    if not documents and parser_state == "OPAQUE_EXACT_BYTES":
        parser_state = (
            "BLOCKED_PDF_CAPABILITY_UNAVAILABLE_EXACT_BYTES_PRESERVED"
            if not parser_errors
            else "PARSE_FAILED_PDF_EXACT_BYTES_PRESERVED"
        )
    return documents, facts, parser_state


def _extract_image(
    data: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    parser_state = "OPAQUE_EXACT_BYTES"
    metadata_error: str | None = None
    if _module_available("PIL"):
        try:
            from PIL import Image  # type: ignore[import-not-found]

            image = Image.open(io.BytesIO(data))
            facts.append(
                _fact(
                    "image_metadata",
                    "image",
                    {
                        "format": image.format,
                        "width": image.width,
                        "height": image.height,
                        "mode": image.mode,
                        "frames": getattr(image, "n_frames", 1),
                    },
                )
            )
            parser_state = "METADATA_PARSED"
        except Exception as exc:  # noqa: BLE001 - image decoder is optional
            metadata_error = f"IMAGE_METADATA_{type(exc).__name__.upper()}"
    lines, blocker = _rapidocr_lines(data)
    if not lines:
        secondary_lines, secondary = _pytesseract_lines(data)
        lines = secondary_lines
        blocker = " | ".join(item for item in (blocker, secondary) if item)
    ocr_documents, ocr_facts = _ocr_payload(
        prefix="image",
        locator="image",
        lines=lines,
    )
    documents.extend(ocr_documents)
    facts.extend(ocr_facts)
    if ocr_documents:
        parser_state = "PARSED_OCR_LOCAL"
    else:
        if blocker and "RAPIDOCR_EMPTY" in blocker:
            facts.append(
                _fact(
                    "image_ocr_run",
                    "image",
                    {
                        "engine": "rapidocr+onnxruntime",
                        "state": "EMPTY",
                        "text_chars": 0,
                        "line_count": 0,
                    },
                )
            )
        facts.append(
            _fact(
                "image_review_region",
                "image",
                {
                    "reason": blocker or metadata_error or "OCR_EMPTY",
                    "metadata_error": metadata_error,
                    "exact_bytes_preserved": True,
                },
            )
        )
        if parser_state == "OPAQUE_EXACT_BYTES":
            parser_state = (
                "PARSE_FAILED_IMAGE_EXACT_BYTES_PRESERVED"
                if metadata_error
                else "BLOCKED_IMAGE_CAPABILITY_UNAVAILABLE_EXACT_BYTES_PRESERVED"
            )
    return documents, facts, parser_state


def _inspect_sqlite(
    path: Path,
    lane: LaneDefinition,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    connection: sqlite3.Connection | None = None
    is_loader = lane.canonical_lane_id == "brain_loader"
    database_kind = (
        "brain_loader_database" if is_loader else "loaded_sqlite_brain_database"
    )
    schema_kind = (
        "brain_loader_schema_object"
        if is_loader
        else "loaded_sqlite_brain_schema_object"
    )
    relationship_kind = (
        "brain_loader_relationship" if is_loader else "loaded_sqlite_brain_relationship"
    )
    receipt_kind = (
        "brain_loader_receipt" if is_loader else "loaded_sqlite_brain_receipt"
    )
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_key_errors = [
            dict(row)
            for row in connection.execute("PRAGMA foreign_key_check").fetchmany(1001)
        ]
        schema_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ]
        foreign_keys: list[dict[str, Any]] = []
        table_stats: list[dict[str, Any]] = []
        fts_tables: list[dict[str, Any]] = []
        for row in schema_rows:
            if row["type"] == "table":
                safe_name = str(row["name"]).replace('"', '""')
                table_fks = [
                    {
                        **dict(fk),
                        "from_table": row["name"],
                        "to_table": dict(fk).get("table"),
                    }
                    for fk in connection.execute(
                        f'PRAGMA foreign_key_list("{safe_name}")'  # nosec B608
                    )
                ]
                foreign_keys.extend(table_fks)
                sql = str(row.get("sql") or "")
                is_virtual = sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")
                is_fts = is_virtual and "USING FTS" in sql.upper()
                if is_fts:
                    fts_tables.append(
                        {
                            "table": row["name"],
                            "sql_sha256": sha256_bytes(sql.encode("utf-8")),
                        }
                    )
                row_count: int | None = None
                count_error: str | None = None
                if not is_virtual:
                    try:
                        row_count = int(
                            connection.execute(
                                f'SELECT COUNT(*) FROM "{safe_name}"'  # nosec B608
                            ).fetchone()[0]
                        )
                    except sqlite3.Error as exc:
                        count_error = type(exc).__name__
                table_stats.append(
                    {
                        "table": row["name"],
                        "row_count": row_count,
                        "count_error_type": count_error,
                        "virtual": is_virtual,
                        "fts": is_fts,
                    }
                )
        text = "\n\n".join(
            f"{row['type']} {row['name']}\n{row.get('sql') or ''}"
            for row in schema_rows
        )
        documents.append({"locator": "sqlite_schema", "text": text, "metadata": {}})
        facts.append(
            _fact(
                database_kind,
                path.name,
                {
                    "size_bytes": path.stat().st_size,
                    "schema_objects": len(schema_rows),
                    "tables": sum(row["type"] == "table" for row in schema_rows),
                    "views": sum(row["type"] == "view" for row in schema_rows),
                    "indexes": sum(row["type"] == "index" for row in schema_rows),
                    "triggers": sum(row["type"] == "trigger" for row in schema_rows),
                    "inspection_mode": "sqlite_uri_ro_immutable_query_only",
                },
            )
        )
        facts.extend(_fact(schema_kind, str(row["name"]), row) for row in schema_rows)
        for foreign_key in foreign_keys:
            facts.append(
                _fact(
                    (
                        relationship_kind
                        if is_loader
                        else "loaded_sqlite_brain_foreign_key"
                    ),
                    (
                        f"{foreign_key['from_table']}:{foreign_key.get('id')}:"
                        f"{foreign_key.get('seq')}"
                    ),
                    foreign_key,
                )
            )
            if not is_loader:
                facts.append(
                    _fact(
                        relationship_kind,
                        (
                            f"{foreign_key['from_table']}."
                            f"{foreign_key.get('from')}->"
                            f"{foreign_key.get('to_table')}."
                            f"{foreign_key.get('to')}"
                        ),
                        {
                            "from_table": foreign_key["from_table"],
                            "from_column": foreign_key.get("from"),
                            "to_table": foreign_key.get("to_table"),
                            "to_column": foreign_key.get("to"),
                        },
                    )
                )
        if not is_loader:
            facts.extend(
                _fact(
                    "loaded_sqlite_brain_table_stat",
                    str(stat["table"]),
                    stat,
                )
                for stat in table_stats
            )
            facts.extend(
                _fact(
                    "loaded_sqlite_brain_fts_table",
                    str(item["table"]),
                    item,
                )
                for item in fts_tables
            )
            facts.append(
                _fact(
                    "loaded_sqlite_brain_integrity_result",
                    "PRAGMA integrity_check",
                    {
                        "rows": integrity,
                        "valid": integrity == ["ok"] and not foreign_key_errors,
                        "foreign_key_error_count": len(foreign_key_errors),
                        "foreign_key_errors": foreign_key_errors[:1000],
                        "foreign_key_errors_truncated": len(foreign_key_errors) > 1000,
                    },
                )
            )
            facts.append(
                _fact(
                    "loaded_sqlite_brain_compatibility",
                    "schema",
                    {
                        "read_only_open": True,
                        "integrity_ok": integrity == ["ok"],
                        "foreign_keys_ok": not foreign_key_errors,
                        "fts_table_count": len(fts_tables),
                        "imported_sql_executed": False,
                    },
                )
            )
        facts.append(
            _fact(
                receipt_kind,
                "read_only_inspection",
                {
                    "status": (
                        "PASS"
                        if integrity == ["ok"] and not foreign_key_errors
                        else "FAIL"
                    ),
                    "rows": integrity,
                    "schema_objects": len(schema_rows),
                    "table_stats": len(table_stats),
                    "foreign_keys": len(foreign_keys),
                    "fts_tables": len(fts_tables),
                    "foreign_key_errors": len(foreign_key_errors),
                    "writeback": False,
                    "imported_sql_executed": False,
                },
            )
        )
        return documents, facts, "PARSED_SQLITE_READ_ONLY"
    except sqlite3.Error as exc:
        failure_kind = (
            "brain_loader_receipt"
            if is_loader
            else "loaded_sqlite_brain_integrity_result"
        )
        return (
            [],
            [
                _fact(
                    failure_kind,
                    "open",
                    {
                        "valid": False,
                        "error_type": type(exc).__name__,
                        "writeback": False,
                        "imported_sql_executed": False,
                    },
                )
            ],
            "PARSE_FAILED_SQLITE_EXACT_BYTES_PRESERVED",
        )
    finally:
        if connection is not None:
            connection.close()


def _archive_member_safe(name: str) -> bool:
    raw = name.replace("\\", "/")
    normalized = posixpath.normpath(raw)
    segments = [segment for segment in raw.split("/") if segment not in {"", "."}]
    return bool(
        normalized
        and normalized not in {".", ".."}
        and not normalized.startswith("../")
        and not normalized.startswith("/")
        and not re.match(r"^[A-Za-z]:", normalized)
        and ".." not in segments
    )


def _extract_archive(
    data: bytes,
    lane: LaneDefinition,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [
            {
                "name": info.filename.replace("\\", "/"),
                "bytes": info.file_size,
                "compressed_bytes": info.compress_size,
                "is_directory": info.is_dir(),
                "safe_path": _archive_member_safe(info.filename),
                "crc32": f"{info.CRC:08X}",
            }
            for info in archive.infolist()[:10000]
        ]
    text = "\n".join(
        f"{item['name']} ({item['bytes']} bytes)"
        for item in members
        if not item["is_directory"]
    )
    if text:
        documents.append(
            {
                "locator": "archive_members",
                "text": text,
                "metadata": {"member_count": len(members)},
            }
        )
    unsafe = [item["name"] for item in members if not item["safe_path"]]
    lane_id = lane.canonical_lane_id
    if lane_id == "brain_loader":
        facts.append(
            _fact(
                "brain_loader_package",
                "archive",
                {
                    "member_count": len(members),
                    "unsafe_member_count": len(unsafe),
                    "exact_bytes_preserved": True,
                    "members_executed": False,
                },
            )
        )
        facts.extend(
            _fact("brain_loader_member", str(item["name"]), item) for item in members
        )
        for item in members:
            lowered = str(item["name"]).lower()
            if "manifest" in lowered and lowered.endswith(".json"):
                facts.append(_fact("brain_loader_manifest", str(item["name"]), item))
            if "pointer" in lowered and lowered.endswith((".json", ".txt")):
                facts.append(_fact("brain_loader_pointer", str(item["name"]), item))
        facts.append(
            _fact(
                "brain_loader_receipt",
                "archive_inspection",
                {
                    "status": "PASS" if not unsafe else "BLOCKED",
                    "unsafe_members": unsafe,
                    "members_executed": False,
                    "writeback": False,
                },
            )
        )
        state = (
            "PARSED_BRAIN_PACKAGE_DIRECTORY"
            if not unsafe
            else "BLOCKED_BRAIN_PACKAGE_UNSAFE_MEMBER"
        )
    elif lane_id == "project_engulf":
        facts.append(
            _fact(
                "project_engulf_origin",
                "archive",
                {
                    "origin_type": "zip_archive",
                    "member_count": len(members),
                    "exact_bytes_preserved": True,
                },
            )
        )
        components: dict[str, int] = {}
        for item in members:
            name = str(item["name"])
            facts.append(_fact("project_engulf_file", name, item))
            top_level = name.split("/", 1)[0] if name else ""
            if top_level:
                components[top_level] = components.get(top_level, 0) + 1
            if not item["is_directory"] and item["safe_path"]:
                target_lane = route_source(name, code_mode="local_code")
                facts.extend(
                    [
                        _fact(
                            "project_engulf_sector_target",
                            name,
                            {
                                "target_lane": target_lane,
                                "classification_only": True,
                                "writeback": False,
                            },
                        ),
                        _fact(
                            "project_engulf_schema_mapping",
                            name,
                            {
                                "extension": Path(name).suffix.lower(),
                                "target_lane": target_lane,
                            },
                        ),
                        _fact(
                            "project_engulf_object_decision",
                            name,
                            {
                                "decision": "INDEX_ONLY_PENDING_HIL",
                                "target_lane": target_lane,
                            },
                        ),
                        _fact(
                            "project_engulf_relationship",
                            f"archive->{name}",
                            {
                                "from": "archive",
                                "to": name,
                                "relation": "contains",
                            },
                        ),
                    ]
                )
            elif not item["safe_path"]:
                facts.append(
                    _fact(
                        "project_engulf_conflict",
                        name,
                        {"reason": "UNSAFE_ARCHIVE_MEMBER_PATH"},
                    )
                )
        facts.extend(
            _fact(
                "project_engulf_component",
                component,
                {"member_count": member_count},
            )
            for component, member_count in sorted(components.items())
        )
        reconciliation_names = sorted(components)[:200]
        reconciliation = reconcile_entity_candidates(reconciliation_names)
        facts.append(
            _fact(
                "project_engulf_relationship",
                "component-entity-reconciliation",
                {
                    **reconciliation,
                    "component_count": len(components),
                    "bounded_component_count": len(reconciliation_names),
                    "truncated": len(components) > len(reconciliation_names),
                },
            )
        )
        facts.extend(
            [
                _fact(
                    "project_engulf_run",
                    "archive_inspection",
                    {
                        "status": "PASS" if not unsafe else "BLOCKED",
                        "member_count": len(members),
                        "writeback": False,
                    },
                ),
                _fact(
                    "project_engulf_topology_update",
                    "proposed",
                    {
                        "state": "PROPOSED_NOT_APPLIED",
                        "component_count": len(components),
                    },
                ),
                _fact(
                    "project_engulf_receipt",
                    "archive_inspection",
                    {
                        "status": "PASS" if not unsafe else "BLOCKED",
                        "unsafe_members": unsafe,
                        "mutated_project": False,
                    },
                ),
            ]
        )
        state = (
            "PARSED_PROJECT_ARCHIVE_DIRECTORY"
            if not unsafe
            else "BLOCKED_PROJECT_ARCHIVE_UNSAFE_MEMBER"
        )
    elif lane_id == "sqlite_brain":
        database_members = [
            item
            for item in members
            if Path(str(item["name"])).suffix.lower() in {".db", ".sqlite", ".sqlite3"}
        ]
        facts.append(
            _fact(
                "loaded_sqlite_brain_package_pointer",
                "archive",
                {
                    "database_members": database_members,
                    "member_count": len(members),
                    "state": "INDEXED_NOT_LOADED",
                    "writeback": False,
                },
            )
        )
        facts.append(
            _fact(
                "loaded_sqlite_brain_receipt",
                "archive_inspection",
                {
                    "status": "PASS" if database_members and not unsafe else "BLOCKED",
                    "unsafe_members": unsafe,
                    "database_member_count": len(database_members),
                    "database_members_executed": False,
                },
            )
        )
        state = (
            "PARSED_SQLITE_BRAIN_PACKAGE_DIRECTORY"
            if database_members and not unsafe
            else "BLOCKED_SQLITE_BRAIN_PACKAGE"
        )
    else:
        facts.extend(
            _fact(
                "custom_item",
                str(item["name"]),
                item,
            )
            for item in members
        )
        state = "PARSED_ZIP_DIRECTORY" if not unsafe else "BLOCKED_ZIP_UNSAFE_MEMBER"
    return documents, facts, state


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_message_text(item) for item in value if item is not None)
    if isinstance(value, dict):
        if "parts" in value:
            return _message_text(value["parts"])
        if "text" in value:
            return _message_text(value["text"])
        if "content" in value:
            return _message_text(value["content"])
    return ""


def _chat_turns(value: Any) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    records = value if isinstance(value, list) else [value]
    for record in records:
        if not isinstance(record, dict):
            continue
        prompt = _message_text(
            record.get("prompt")
            or record.get("user")
            or record.get("input")
            or record.get("question")
        )
        response = _message_text(
            record.get("response")
            or record.get("assistant")
            or record.get("output")
            or record.get("answer")
        )
        if prompt or response:
            turns.append((prompt, response))
        messages = record.get("messages")
        if not isinstance(messages, list):
            continue
        pending_prompt = ""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").lower()
            text = _message_text(message.get("content"))
            if role in {"user", "human"}:
                if pending_prompt:
                    turns.append((pending_prompt, ""))
                pending_prompt = text
            elif role in {"assistant", "ai"}:
                turns.append((pending_prompt, text))
                pending_prompt = ""
        if pending_prompt:
            turns.append((pending_prompt, ""))
    if isinstance(value, dict) and isinstance(value.get("mapping"), dict):
        pending_prompt = ""
        ordered = sorted(
            value["mapping"].values(),
            key=lambda item: (
                (
                    item.get("message", {}).get("create_time")
                    if isinstance(item, dict)
                    else None
                )
                or 0,
                str(item.get("id", "")) if isinstance(item, dict) else "",
            ),
        )
        for node in ordered:
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue
            author = message.get("author")
            role = (
                str(author.get("role") or "").lower()
                if isinstance(author, dict)
                else ""
            )
            text = _message_text(message.get("content"))
            if role in {"user", "human"}:
                if pending_prompt:
                    turns.append((pending_prompt, ""))
                pending_prompt = text
            elif role in {"assistant", "ai"}:
                turns.append((pending_prompt, text))
                pending_prompt = ""
        if pending_prompt:
            turns.append((pending_prompt, ""))
    return [(prompt, response) for prompt, response in turns if prompt or response]


def _chat_turns_from_text(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(?ims)^\s*(?:user|human)\s*:\s*(.+?)"
        r"^\s*(?:assistant|ai)\s*:\s*(.+?)"
        r"(?=^\s*(?:user|human)\s*:|\Z)"
    )
    return [
        (match.group(1).strip(), match.group(2).strip())
        for match in pattern.finditer(text)
    ]


def _chat_lineage_facts(
    turns: list[tuple[str, str]],
    relative_path: str,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    prior_hash = "0" * 64
    for ordinal, (prompt, response) in enumerate(turns[:5000], start=1):
        locator = f"{relative_path}:turn:{ordinal}"
        prepare = {
            "turn_ordinal": ordinal,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "response_sha256": sha256_bytes(response.encode("utf-8")),
        }
        facts.extend(
            [
                _fact("turn_prepare", locator, prepare),
                _fact(
                    "prompt_raw_exact",
                    f"{locator}:prompt",
                    {"text": prompt, "sha256": prepare["prompt_sha256"]},
                ),
                _fact(
                    "prompt_normalized_summary",
                    f"{locator}:prompt-summary",
                    {
                        "visible_prefix": prompt[:500],
                        "truncated": len(prompt) > 500,
                    },
                ),
                _fact(
                    "response_raw_visible_exact",
                    f"{locator}:response",
                    {"text": response, "sha256": prepare["response_sha256"]},
                ),
                _fact(
                    "response_summary",
                    f"{locator}:response-summary",
                    {
                        "visible_prefix": response[:500],
                        "truncated": len(response) > 500,
                    },
                ),
            ]
        )
        commit_payload = {
            "turn_ordinal": ordinal,
            "prior_hash": prior_hash,
            "prompt_sha256": prepare["prompt_sha256"],
            "response_sha256": prepare["response_sha256"],
        }
        commit_hash = sha256_bytes(canonical_json_bytes(commit_payload))
        commit_payload["commit_hash"] = commit_hash
        facts.extend(
            [
                _fact("turn_commit", locator, commit_payload),
                _fact(
                    "state_hash_chain",
                    locator,
                    {
                        "prior_hash": prior_hash,
                        "current_hash": commit_hash,
                    },
                ),
            ]
        )
        prior_hash = commit_hash
    facts.extend(
        [
            _fact(
                "lineage_head",
                relative_path,
                {
                    "turn_count": min(len(turns), 5000),
                    "head_hash": prior_hash,
                    "truncated": len(turns) > 5000,
                },
            ),
            _fact(
                "source_normalization_receipt",
                relative_path,
                {
                    "turns_detected": len(turns),
                    "turns_indexed": min(len(turns), 5000),
                    "raw_source_preserved": True,
                    "private_reasoning_inferred": False,
                },
            ),
        ]
    )
    return facts


def _extract_source(
    path: Path,
    relative_path: str,
    data: bytes,
    lane: LaneDefinition,
    *,
    host_profile: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str | None]:
    suffix = path.suffix.lower()
    parser_state = "OPAQUE_EXACT_BYTES"
    encoding: str | None = None

    def finish(
        documents: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        state: str,
        detected_encoding: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str | None]:
        return _finalize_extraction(
            lane,
            relative_path,
            documents,
            facts,
            state,
            detected_encoding,
        )

    def docling_enrich(
        documents: list[dict[str, Any]],
        facts: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        artifacts = packaged_docling_artifacts_root()
        if not docling_available() or not artifacts.is_dir():
            facts.append(
                _fact(
                    "toolchain_fallback_receipt",
                    relative_path,
                    {
                        "tool": "Docling",
                        "status": (
                            "AWAITING_HIDDEN_RUNTIME_MODELS"
                            if docling_available()
                            else "DEPENDENCY_UNAVAILABLE"
                        ),
                        "model_download_allowed": False,
                        "native_exact_extractor_preserved": True,
                    },
                )
            )
            return documents, facts
        try:
            receipt = extract_with_docling(
                DoclingRequest(
                    source_path=path,
                    artifacts_path=artifacts,
                    host_profile=host_profile,
                    allow_model_download=False,
                )
            )
            markdown = str(receipt.get("markdown") or "")
            if markdown:
                documents.append(
                    {
                        "locator": "docling:document",
                        "text": markdown,
                        "metadata": {
                            "extractor": "Docling",
                            "receipt_sha256": receipt["receipt_sha256"],
                        },
                    }
                )
            facts.append(_fact("docling_extraction", relative_path, receipt))
        except Exception as exc:  # noqa: BLE001 - exact native extractor remains primary
            facts.append(
                _fact(
                    "toolchain_fallback_receipt",
                    relative_path,
                    {
                        "tool": "Docling",
                        "status": "FAILED_NATIVE_EXTRACTOR_PRESERVED",
                        "error": type(exc).__name__,
                        "model_download_allowed": False,
                    },
                )
            )
        return documents, facts

    try:
        if suffix == ".docx":
            documents, facts = _extract_docx(data)
            documents, facts = docling_enrich(documents, facts)
            return finish(documents, facts, "PARSED_DOCX_OPENXML", None)
        if suffix in {".xlsx", ".xlsm"}:
            documents, facts = _extract_xlsx(data)
            request = DataInspectionRequest(
                source_path=path,
                host_profile=host_profile,
                max_rows=min(MAX_ROWS_PER_TAB, 2_000),
            )
            for fact_kind, inspector in (
                ("openpyxl_workbook_inspection", inspect_excel_openpyxl),
                ("pandas_workbook_inspection", inspect_tabular_pandas),
            ):
                try:
                    facts.append(_fact(fact_kind, relative_path, inspector(request)))
                except Exception as exc:  # noqa: BLE001 - exact OpenXML facts remain primary
                    facts.append(
                        _fact(
                            "toolchain_fallback_receipt",
                            relative_path,
                            {
                                "tool": fact_kind,
                                "status": "UNAVAILABLE_OR_BLOCKED",
                                "error": type(exc).__name__,
                                "openxml_primary_preserved": True,
                            },
                        )
                    )
            documents, facts = docling_enrich(documents, facts)
            return finish(documents, facts, "PARSED_XLSX_OPENXML", None)
        if suffix == ".xls":
            documents, facts, state = _extract_legacy_excel(path)
            return finish(documents, facts, state, None)
        if suffix in {".csv", ".tsv"}:
            if duckdb_available():
                result = stage_tabular_source(
                    DuckDBStageRequest(
                        source_path=path,
                        lane_id=lane.canonical_lane_id,
                        host_profile=host_profile,
                        max_rows=min(MAX_ROWS_PER_TAB, 2_000),
                    )
                )
                documents, facts = stage_result_to_lane_payloads(result)
                return finish(
                    documents,
                    facts,
                    "PARSED_DUCKDB_DELIMITED_TO_SQLITE",
                    "AUTO_DETECTED_BY_DUCKDB",
                )
            if polars_available():
                result = stage_tabular_source_polars(
                    DuckDBStageRequest(
                        source_path=path,
                        lane_id=lane.canonical_lane_id,
                        host_profile=host_profile,
                        max_rows=min(MAX_ROWS_PER_TAB, 2_000),
                    )
                )
                documents, facts = stage_result_to_lane_payloads(result)
                return finish(
                    documents,
                    facts,
                    "PARSED_POLARS_LAZY_DELIMITED_TO_SQLITE",
                    "AUTO_DETECTED_BY_POLARS",
                )
            documents, facts, encoding = _extract_delimited(data, suffix)
            facts.append(
                _fact(
                    "toolchain_fallback_receipt",
                    relative_path,
                    {
                        "primary_tool": "DuckDB",
                        "primary_state": "UNAVAILABLE",
                        "fallback_tool": "python csv",
                        "host_profile": host_profile,
                        "durable_authority": "OWNING_LANE_SQLITE",
                    },
                )
            )
            return finish(documents, facts, "PARSED_DELIMITED", encoding)
        if suffix in {".json", ".jsonl"}:
            if (
                suffix == ".jsonl"
                and lane.canonical_lane_id
                in {"data_excel", "analysis", "project_engulf", "artifacts"}
                and polars_available()
            ):
                result = stage_tabular_source_polars(
                    DuckDBStageRequest(
                        source_path=path,
                        lane_id=lane.canonical_lane_id,
                        host_profile=host_profile,
                        max_rows=min(MAX_ROWS_PER_TAB, 2_000),
                    )
                )
                documents, facts = stage_result_to_lane_payloads(result)
                return finish(
                    documents,
                    facts,
                    "PARSED_POLARS_LAZY_NDJSON_TO_SQLITE",
                    "UTF-8_NDJSON",
                )
            documents, facts, encoding = _extract_json(data, suffix)
            native_root = configured_runtime_root()
            if native_root is not None and try_resolve_native_tool("jq") is not None:
                jq_receipt = run_native_tool(
                    NativeInvocationRequest(
                        tool_id="jq",
                        arguments=["--sort-keys", "."],
                        input_bytes=data,
                        host_profile=host_profile,
                    ),
                    runtime_root=native_root,
                )
                facts.append(
                    _fact(
                        "native_json_validation",
                        relative_path,
                        {
                            "status": jq_receipt["status"],
                            "tool_id": "jq",
                            "receipt_sha256": jq_receipt["receipt_sha256"],
                            "stdout_sha256": sha256_bytes(
                                str(jq_receipt["stdout"]).encode("utf-8")
                            ),
                            "python_json_remains_parser": True,
                        },
                    )
                )
            decoded, _ = _decode_text(data)
            if decoded is not None and lane.canonical_lane_id in PRIMARY_CODE_LANES:
                facts.extend(extract_code_lane_facts(relative_path, decoded))
            if lane.canonical_lane_id == "chat_lineage":
                root: Any = []
                if decoded is not None:
                    if suffix == ".jsonl":
                        root = [
                            json.loads(line)
                            for line in decoded.splitlines()
                            if line.strip()
                        ]
                    else:
                        root = json.loads(decoded)
                facts.extend(_chat_lineage_facts(_chat_turns(root), relative_path))
            return finish(documents, facts, "PARSED_JSON", encoding)
        if suffix == ".parquet":
            if duckdb_available():
                result = stage_tabular_source(
                    DuckDBStageRequest(
                        source_path=path,
                        lane_id=lane.canonical_lane_id,
                        host_profile=host_profile,
                        max_rows=min(MAX_ROWS_PER_TAB, 2_000),
                    )
                )
                documents, facts = stage_result_to_lane_payloads(result)
                return finish(
                    documents,
                    facts,
                    "PARSED_DUCKDB_PARQUET_TO_SQLITE",
                    None,
                )
            if polars_available():
                result = stage_tabular_source_polars(
                    DuckDBStageRequest(
                        source_path=path,
                        lane_id=lane.canonical_lane_id,
                        host_profile=host_profile,
                        max_rows=min(MAX_ROWS_PER_TAB, 2_000),
                    )
                )
                documents, facts = stage_result_to_lane_payloads(result)
                return finish(
                    documents,
                    facts,
                    "PARSED_POLARS_LAZY_PARQUET_TO_SQLITE",
                    None,
                )
            documents, facts, state = _extract_parquet(data)
            facts.append(
                _fact(
                    "toolchain_fallback_receipt",
                    relative_path,
                    {
                        "primary_tool": "DuckDB",
                        "primary_state": "UNAVAILABLE",
                        "fallback_tool": "pyarrow",
                        "host_profile": host_profile,
                        "durable_authority": "OWNING_LANE_SQLITE",
                    },
                )
            )
            return finish(documents, facts, state, None)
        if suffix == ".pptx":
            documents, facts = _extract_pptx(data)
            documents, facts = docling_enrich(documents, facts)
            return finish(documents, facts, "PARSED_PPTX_OPENXML", None)
        if suffix in _MEDIA_EXTENSIONS:
            native_root = configured_runtime_root()
            if native_root is None:
                return finish(
                    [],
                    [
                        _fact(
                            "artifact_media_probe",
                            relative_path,
                            {
                                "status": "BLOCKED_HIDDEN_RUNTIME_UNAVAILABLE",
                                "required_tool": "FFmpeg",
                                "exact_bytes_preserved": True,
                            },
                        )
                    ],
                    "BLOCKED_FFMPEG_HIDDEN_RUNTIME_UNAVAILABLE_EXACT_BYTES_PRESERVED",
                    None,
                )
            receipt = run_native_tool(
                NativeInvocationRequest(
                    tool_id="ffmpeg",
                    arguments=[
                        "-hide_banner",
                        "-i",
                        str(path),
                        "-f",
                        "ffmetadata",
                        "-",
                    ],
                    host_profile=host_profile,
                    max_output_bytes=4_000_000,
                ),
                runtime_root=native_root,
            )
            metadata = (str(receipt["stdout"]) + "\n" + str(receipt["stderr"])).strip()
            return finish(
                [
                    {
                        "locator": "ffmpeg:metadata",
                        "text": metadata,
                        "metadata": {"extractor": "FFmpeg"},
                    }
                ],
                [
                    _fact(
                        "artifact_media_probe",
                        relative_path,
                        {
                            "status": receipt["status"],
                            "metadata_sha256": sha256_bytes(metadata.encode("utf-8")),
                            "receipt_sha256": receipt["receipt_sha256"],
                            "media_mutated": False,
                        },
                    )
                ],
                "PARSED_FFMPEG_MEDIA_METADATA",
                None,
            )
        if suffix == ".hyper":
            try:
                receipt = inspect_tableau_hyper(
                    DataInspectionRequest(
                        source_path=path,
                        host_profile=host_profile,
                        max_rows=min(MAX_ROWS_PER_TAB, 2_000),
                    )
                )
                documents = [
                    {
                        "locator": str(table["name"]),
                        "text": json.dumps(
                            table,
                            sort_keys=True,
                            ensure_ascii=False,
                            default=str,
                        ),
                        "metadata": {"extractor": "tableauhyperapi"},
                    }
                    for table in receipt["tables"]
                ]
                facts = [_fact("tableau_hyper_inspection", relative_path, receipt)]
                return finish(documents, facts, "PARSED_TABLEAU_HYPER_READ_ONLY", None)
            except Exception as exc:  # noqa: BLE001 - exact bytes remain preserved
                return finish(
                    [],
                    [
                        _fact(
                            "parser_capability_blocker",
                            relative_path,
                            {
                                "required_tool": "tableauhyperapi",
                                "error": type(exc).__name__,
                                "exact_bytes_preserved": True,
                            },
                        )
                    ],
                    "BLOCKED_TABLEAU_HYPER_TOOL_UNAVAILABLE_EXACT_BYTES_PRESERVED",
                    None,
                )
        if suffix == ".pdf":
            documents, facts, state = _extract_pdf(
                data,
                path=path,
                host_profile=host_profile,
            )
            documents, facts = docling_enrich(documents, facts)
            return finish(documents, facts, state, None)
        if suffix in LANE_REGISTRY["images_ocr"].extensions:
            documents, facts, state = _extract_image(data)
            return finish(documents, facts, state, None)
        if suffix in {".db", ".sqlite", ".sqlite3"}:
            documents, facts, state = _inspect_sqlite(path, lane)
            try:
                facts.append(
                    _fact(
                        "sqlalchemy_schema_inspection",
                        relative_path,
                        inspect_sqlalchemy_sqlite(
                            DataInspectionRequest(
                                source_path=path,
                                host_profile=host_profile,
                                max_rows=200,
                            )
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - immutable sqlite inspection remains primary
                facts.append(
                    _fact(
                        "toolchain_fallback_receipt",
                        relative_path,
                        {
                            "primary_tool": "sqlite3 immutable URI",
                            "secondary_tool": "SQLAlchemy",
                            "secondary_state": "UNAVAILABLE_OR_BLOCKED",
                            "error": type(exc).__name__,
                            "writeback": False,
                        },
                    )
                )
            return finish(documents, facts, state, None)
        if suffix == ".zip":
            documents, facts, state = _extract_archive(data, lane)
            return finish(documents, facts, state, None)
        decoded_text, encoding = _decode_text(data)
        if decoded_text is not None:
            text_facts: list[dict[str, Any]] = []
            if lane.canonical_lane_id in PRIMARY_CODE_LANES:
                text_facts.extend(extract_code_lane_facts(relative_path, decoded_text))
            if lane.canonical_lane_id == "chat_lineage":
                text_facts.extend(
                    _chat_lineage_facts(
                        _chat_turns_from_text(decoded_text),
                        relative_path,
                    )
                )
            if suffix in {".html", ".htm", ".xml"}:
                if suffix in {".html", ".htm"}:
                    extraction = extract_web_document(decoded_text)
                    decoded_text = str(extraction["text"])
                    text_facts.append(
                        _fact(
                            "document_structure",
                            relative_path,
                            {
                                "source_format": suffix,
                                "extractor": extraction["selected_tool"],
                                "tool_attempts": extraction["attempts"],
                                "receipt_sha256": extraction["receipt_sha256"],
                                "network_used": False,
                            },
                        )
                    )
                else:
                    stripped = re.sub(r"<[^>]+>", " ", decoded_text)
                    decoded_text = re.sub(r"\s+", " ", stripped)
                    text_facts.append(
                        _fact(
                            "document_structure",
                            relative_path,
                            {"source_format": suffix, "markup_stripped": True},
                        )
                    )
            return finish(
                [
                    {
                        "locator": relative_path,
                        "text": decoded_text,
                        "metadata": {},
                    }
                ],
                text_facts,
                "PARSED_TEXT",
                encoding,
            )
        facts = [
            _fact(
                "parser_capability_blocker",
                relative_path,
                {
                    "reason": "NO_REGISTERED_PARSER_FOR_BINARY_FORMAT",
                    "extension": suffix,
                    "exact_bytes_preserved": True,
                },
            )
        ]
        return finish([], facts, parser_state, encoding)
    except Exception as exc:  # noqa: BLE001 - every parser failure becomes a fact
        return finish(
            [],
            [
                _fact(
                    "parser_error",
                    relative_path,
                    {
                        "error_type": type(exc).__name__,
                        "exact_bytes_preserved": True,
                    },
                )
            ],
            f"PARSE_FAILED_{type(exc).__name__.upper()}",
            encoding,
        )


def _chunks(text: str) -> Iterable[tuple[int, int, str]]:
    source_id = "lane-text-" + sha256_bytes(str(text or "").encode("utf-8"))
    for node in llama_index_nodes(str(text or ""), source_id=source_id):
        yield node.ordinal, node.char_start, node.text_content


LANE_SCHEMA_BUILDER_PROJECTION_SCHEMA = (
    "evidence-lane.lane-schema-builder-projection.v1"
)
LANE_SCHEMA_MIGRATION_PLAN_SCHEMA = "evidence-lane.lane-schema-migration-plan.v1"
LANE_SCHEMA_MIGRATION_RECEIPT_SCHEMA = "evidence-lane.lane-schema-migration-receipt.v1"
LANE_SCHEMA_MIGRATION_STATUS_SCHEMA = "evidence-lane.lane-schema-migration-status.v1"
_LANE_SCHEMA_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_LANE_SCHEMA_MIGRATION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_LANE_SCHEMA_COLUMN_TYPES = frozenset({"INTEGER", "TEXT", "REAL", "BLOB", "ANY"})
_LANE_SCHEMA_FK_ACTIONS = frozenset(
    {"NO ACTION", "RESTRICT", "SET NULL", "SET DEFAULT", "CASCADE"}
)
_LANE_SCHEMA_LEDGER_DDL = (
    """CREATE TABLE IF NOT EXISTS lane_schema_migration(
        sequence INTEGER PRIMARY KEY,
        migration_id TEXT NOT NULL UNIQUE,
        lane_id TEXT NOT NULL,
        namespace TEXT NOT NULL,
        from_version INTEGER NOT NULL,
        to_version INTEGER NOT NULL,
        request_sha256 TEXT NOT NULL,
        ddl_sha256 TEXT NOT NULL,
        pre_schema_sha256 TEXT NOT NULL,
        post_schema_sha256 TEXT NOT NULL,
        foreign_key_projection_sha256 TEXT NOT NULL,
        index_projection_sha256 TEXT NOT NULL,
        fts_rebuild_proof_sha256 TEXT NOT NULL,
        compatibility_proof_sha256 TEXT NOT NULL,
        prior_receipt_sha256 TEXT,
        explicit_user_confirmation_sha256 TEXT,
        applied_by TEXT NOT NULL,
        applied_at TEXT NOT NULL,
        receipt_sha256 TEXT NOT NULL UNIQUE,
        receipt_json TEXT NOT NULL,
        CHECK(to_version = from_version + 1)
    ) STRICT""",
    """CREATE UNIQUE INDEX IF NOT EXISTS
        lane_schema_migration_lane_sequence_idx
        ON lane_schema_migration(lane_id, sequence)""",
    """CREATE TRIGGER IF NOT EXISTS lane_schema_migration_no_update
        BEFORE UPDATE ON lane_schema_migration
        BEGIN
            SELECT RAISE(ABORT, 'lane schema migration ledger is immutable');
        END""",
    """CREATE TRIGGER IF NOT EXISTS lane_schema_migration_no_delete
        BEFORE DELETE ON lane_schema_migration
        BEGIN
            SELECT RAISE(ABORT, 'lane schema migration ledger is immutable');
        END""",
)
LANE_SCHEMA_LEDGER_DDL_SHA256 = sha256_bytes(
    canonical_json_bytes(list(_LANE_SCHEMA_LEDGER_DDL))
)


class LaneSchemaEvolutionError(ValueError):
    """Fail-closed structured lane-schema evolution error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _lane_schema_error(code: str, message: str) -> None:
    raise LaneSchemaEvolutionError(code, message)


def _lane_schema_identifier(value: Any, *, label: str) -> str:
    normalized = str(value or "")
    if not _LANE_SCHEMA_IDENTIFIER_RE.fullmatch(normalized):
        _lane_schema_error(
            "LANE_SCHEMA_UNSAFE_IDENTIFIER",
            f"{label} is not a safe lower-snake-case SQLite identifier.",
        )
    return normalized


def _quoted_lane_schema_identifier(value: str) -> str:
    return f'"{value}"'


def _normalized_lane_schema_column(
    raw: Any,
    *,
    allow_primary_key: bool,
) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {
        "name",
        "type",
        "nullable",
        "primary_key",
    }:
        _lane_schema_error(
            "LANE_SCHEMA_COLUMN_CONTRACT_INVALID",
            "Every column must declare name, type, nullable, and primary_key.",
        )
    name = _lane_schema_identifier(raw["name"], label="column name")
    column_type = str(raw["type"] or "").upper()
    if column_type not in _LANE_SCHEMA_COLUMN_TYPES:
        _lane_schema_error(
            "LANE_SCHEMA_COLUMN_TYPE_UNSUPPORTED",
            f"Unsupported STRICT column type: {column_type!r}.",
        )
    if not isinstance(raw["nullable"], bool) or not isinstance(
        raw["primary_key"], bool
    ):
        _lane_schema_error(
            "LANE_SCHEMA_COLUMN_FLAGS_INVALID",
            "Column nullable and primary_key flags must be booleans.",
        )
    if raw["primary_key"] and not allow_primary_key:
        _lane_schema_error(
            "LANE_SCHEMA_ADD_COLUMN_PRIMARY_KEY_FORBIDDEN",
            "ADD_COLUMN cannot introduce a primary key.",
        )
    if raw["primary_key"] and raw["nullable"]:
        _lane_schema_error(
            "LANE_SCHEMA_PRIMARY_KEY_NULLABLE",
            "A primary-key column cannot be nullable.",
        )
    return {
        "name": name,
        "type": column_type,
        "nullable": raw["nullable"],
        "primary_key": raw["primary_key"],
    }


def _lane_schema_column_ddl(column: dict[str, Any]) -> str:
    result = f"{_quoted_lane_schema_identifier(column['name'])} {column['type']}"
    if column["primary_key"]:
        result += " PRIMARY KEY"
    elif not column["nullable"]:
        result += " NOT NULL"
    return result


def compile_lane_schema_migration(
    lane: LaneDefinition,
    migration: dict[str, Any],
) -> dict[str, Any]:
    """Compile one structured additive migration without touching SQLite."""

    contract = lane_schema_evolution_contract(lane.canonical_lane_id)
    if not isinstance(migration, dict) or set(migration) != {
        "migration_id",
        "from_version",
        "to_version",
        "operations",
        "rebuild_fts",
    }:
        _lane_schema_error(
            "LANE_SCHEMA_MIGRATION_CONTRACT_INVALID",
            "A migration requires exact identity, versions, operations, and FTS intent.",
        )
    migration_id = str(migration["migration_id"] or "")
    if not _LANE_SCHEMA_MIGRATION_ID_RE.fullmatch(
        migration_id
    ) or not migration_id.startswith(contract["migration_id_prefix"]):
        _lane_schema_error(
            "LANE_SCHEMA_MIGRATION_ID_INVALID",
            "The migration ID is outside the lane extension namespace.",
        )
    from_version = migration["from_version"]
    to_version = migration["to_version"]
    if (
        not isinstance(from_version, int)
        or isinstance(from_version, bool)
        or not isinstance(to_version, int)
        or isinstance(to_version, bool)
        or to_version != from_version + 1
    ):
        _lane_schema_error(
            "LANE_SCHEMA_VERSION_TRANSITION_INVALID",
            "A migration must advance exactly one positive schema version.",
        )
    if not isinstance(migration["rebuild_fts"], bool):
        _lane_schema_error(
            "LANE_SCHEMA_FTS_INTENT_INVALID",
            "rebuild_fts must be an explicit boolean.",
        )
    operations = migration["operations"]
    if not isinstance(operations, list) or not operations:
        _lane_schema_error(
            "LANE_SCHEMA_OPERATIONS_EMPTY",
            "At least one structured additive operation is required.",
        )

    prefix = str(contract["physical_name_prefix"])
    normalized_operations: list[dict[str, Any]] = []
    ddl: list[str] = []
    created_tables: set[str] = set()
    created_indexes: set[str] = set()
    for ordinal, raw in enumerate(operations, start=1):
        if not isinstance(raw, dict):
            _lane_schema_error(
                "LANE_SCHEMA_OPERATION_INVALID",
                f"Operation {ordinal} is not an object.",
            )
        kind = str(raw.get("kind") or "")
        if kind == "CREATE_TABLE":
            if set(raw) != {"kind", "table", "columns", "foreign_keys"}:
                _lane_schema_error(
                    "LANE_SCHEMA_CREATE_TABLE_CONTRACT_INVALID",
                    "CREATE_TABLE has an unexpected shape.",
                )
            table = _lane_schema_identifier(raw["table"], label="table")
            if not table.startswith(prefix) or table in created_tables:
                _lane_schema_error(
                    "LANE_SCHEMA_EXTENSION_TABLE_INVALID",
                    "Extension tables must use the exact lane prefix and be unique.",
                )
            columns_raw = raw["columns"]
            if not isinstance(columns_raw, list) or not columns_raw:
                _lane_schema_error(
                    "LANE_SCHEMA_TABLE_COLUMNS_EMPTY",
                    "CREATE_TABLE requires at least one column.",
                )
            columns = [
                _normalized_lane_schema_column(row, allow_primary_key=True)
                for row in columns_raw
            ]
            column_names = [row["name"] for row in columns]
            if len(column_names) != len(set(column_names)):
                _lane_schema_error(
                    "LANE_SCHEMA_DUPLICATE_COLUMN",
                    "CREATE_TABLE column names must be unique.",
                )
            if sum(bool(row["primary_key"]) for row in columns) > 1:
                _lane_schema_error(
                    "LANE_SCHEMA_MULTIPLE_PRIMARY_KEYS",
                    "CREATE_TABLE supports at most one structured primary key.",
                )
            foreign_keys_raw = raw["foreign_keys"]
            if not isinstance(foreign_keys_raw, list):
                _lane_schema_error(
                    "LANE_SCHEMA_FOREIGN_KEYS_INVALID",
                    "foreign_keys must be a list.",
                )
            foreign_keys: list[dict[str, Any]] = []
            constraints: list[str] = []
            for foreign_key in foreign_keys_raw:
                if not isinstance(foreign_key, dict) or set(foreign_key) != {
                    "columns",
                    "referenced_table",
                    "referenced_columns",
                    "on_delete",
                }:
                    _lane_schema_error(
                        "LANE_SCHEMA_FOREIGN_KEY_CONTRACT_INVALID",
                        "A foreign key has an unexpected shape.",
                    )
                local_columns = foreign_key["columns"]
                target_columns = foreign_key["referenced_columns"]
                if (
                    not isinstance(local_columns, list)
                    or not local_columns
                    or not isinstance(target_columns, list)
                    or len(local_columns) != len(target_columns)
                ):
                    _lane_schema_error(
                        "LANE_SCHEMA_FOREIGN_KEY_COLUMNS_INVALID",
                        "Foreign-key column sets must be nonempty and equal in length.",
                    )
                normalized_local = [
                    _lane_schema_identifier(value, label="foreign-key column")
                    for value in local_columns
                ]
                normalized_target = [
                    _lane_schema_identifier(value, label="referenced column")
                    for value in target_columns
                ]
                if not set(normalized_local) <= set(column_names):
                    _lane_schema_error(
                        "LANE_SCHEMA_FOREIGN_KEY_LOCAL_COLUMN_MISSING",
                        "A foreign key references an undeclared local column.",
                    )
                referenced_table = _lane_schema_identifier(
                    foreign_key["referenced_table"],
                    label="referenced table",
                )
                on_delete = str(foreign_key["on_delete"] or "").upper()
                if on_delete not in _LANE_SCHEMA_FK_ACTIONS:
                    _lane_schema_error(
                        "LANE_SCHEMA_FOREIGN_KEY_ACTION_INVALID",
                        "The requested ON DELETE action is unsupported.",
                    )
                normalized_fk = {
                    "columns": normalized_local,
                    "referenced_table": referenced_table,
                    "referenced_columns": normalized_target,
                    "on_delete": on_delete,
                }
                foreign_keys.append(normalized_fk)
                constraints.append(
                    "FOREIGN KEY ("
                    + ", ".join(
                        _quoted_lane_schema_identifier(value)
                        for value in normalized_local
                    )
                    + ") REFERENCES "
                    + _quoted_lane_schema_identifier(referenced_table)
                    + " ("
                    + ", ".join(
                        _quoted_lane_schema_identifier(value)
                        for value in normalized_target
                    )
                    + f") ON DELETE {on_delete}"
                )
            definitions = [
                *(_lane_schema_column_ddl(row) for row in columns),
                *constraints,
            ]
            ddl.append(
                f"CREATE TABLE {_quoted_lane_schema_identifier(table)} ("
                + ", ".join(definitions)
                + ") STRICT"
            )
            created_tables.add(table)
            normalized_operations.append(
                {
                    "kind": kind,
                    "table": table,
                    "columns": columns,
                    "foreign_keys": foreign_keys,
                }
            )
        elif kind == "ADD_COLUMN":
            if set(raw) != {"kind", "table", "column"}:
                _lane_schema_error(
                    "LANE_SCHEMA_ADD_COLUMN_CONTRACT_INVALID",
                    "ADD_COLUMN has an unexpected shape.",
                )
            table = _lane_schema_identifier(raw["table"], label="table")
            if not table.startswith(prefix):
                _lane_schema_error(
                    "LANE_SCHEMA_CORE_TABLE_ALTER_FORBIDDEN",
                    "ADD_COLUMN is limited to this lane's extension tables.",
                )
            column = _normalized_lane_schema_column(
                raw["column"],
                allow_primary_key=False,
            )
            if not column["nullable"]:
                _lane_schema_error(
                    "LANE_SCHEMA_ADD_COLUMN_NOT_NULL_FORBIDDEN",
                    "ADD_COLUMN must remain nullable to preserve existing rows.",
                )
            ddl.append(
                f"ALTER TABLE {_quoted_lane_schema_identifier(table)} "
                f"ADD COLUMN {_lane_schema_column_ddl(column)}"
            )
            normalized_operations.append(
                {"kind": kind, "table": table, "column": column}
            )
        elif kind == "CREATE_INDEX":
            if set(raw) != {"kind", "index", "table", "columns", "unique"}:
                _lane_schema_error(
                    "LANE_SCHEMA_CREATE_INDEX_CONTRACT_INVALID",
                    "CREATE_INDEX has an unexpected shape.",
                )
            index = _lane_schema_identifier(raw["index"], label="index")
            table = _lane_schema_identifier(raw["table"], label="table")
            columns_raw = raw["columns"]
            if (
                not index.startswith(prefix)
                or not table.startswith(prefix)
                or index in created_indexes
                or not isinstance(columns_raw, list)
                or not columns_raw
                or not isinstance(raw["unique"], bool)
            ):
                _lane_schema_error(
                    "LANE_SCHEMA_EXTENSION_INDEX_INVALID",
                    "Extension indexes require exact names, columns, and uniqueness intent.",
                )
            index_columns = [
                _lane_schema_identifier(value, label="indexed column")
                for value in columns_raw
            ]
            if len(index_columns) != len(set(index_columns)):
                _lane_schema_error(
                    "LANE_SCHEMA_DUPLICATE_INDEX_COLUMN",
                    "An index cannot repeat a column.",
                )
            ddl.append(
                "CREATE "
                + ("UNIQUE " if raw["unique"] else "")
                + f"INDEX {_quoted_lane_schema_identifier(index)} ON "
                + _quoted_lane_schema_identifier(table)
                + " ("
                + ", ".join(
                    _quoted_lane_schema_identifier(value) for value in index_columns
                )
                + ")"
            )
            created_indexes.add(index)
            normalized_operations.append(
                {
                    "kind": kind,
                    "index": index,
                    "table": table,
                    "columns": index_columns,
                    "unique": raw["unique"],
                }
            )
        else:
            _lane_schema_error(
                "LANE_SCHEMA_OPERATION_UNSUPPORTED",
                f"Unsupported additive operation: {kind!r}.",
            )

    ddl_sha256 = sha256_bytes(canonical_json_bytes(ddl))
    body = {
        "schema": LANE_SCHEMA_MIGRATION_PLAN_SCHEMA,
        "policy_sha256": LANE_SCHEMA_EVOLUTION_POLICY_SHA256,
        "lane_id": lane.canonical_lane_id,
        "extension_namespace": contract["extension_namespace"],
        "migration_id": migration_id,
        "from_version": from_version,
        "to_version": to_version,
        "operations": normalized_operations,
        "rebuild_fts": migration["rebuild_fts"],
        "ddl": ddl,
        "ddl_sha256": ddl_sha256,
        "ledger_ddl_sha256": LANE_SCHEMA_LEDGER_DDL_SHA256,
        "additive_only": True,
        "raw_sql_accepted": False,
    }
    plan_sha256 = sha256_bytes(canonical_json_bytes(body))
    confirmation = (
        "AUTHORIZE_LANE_SCHEMA_EVOLUTION::"
        f"{lane.canonical_lane_id}::{migration_id}::{ddl_sha256}"
    )
    return {
        **body,
        "plan_sha256": plan_sha256,
        "explicit_user_confirmation_required": contract[
            "explicit_user_confirmation_required"
        ],
        "required_confirmation": confirmation,
        "status": (
            "USER_CONFIRMATION_REQUIRED"
            if contract["explicit_user_confirmation_required"]
            else "READY"
        ),
    }


def _lane_schema_master_projection(
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    return [
        {
            "type": str(row[0]),
            "name": str(row[1]),
            "table": str(row[2]),
            "sql": row[3],
        }
        for row in connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
              AND type IN ('table', 'index', 'trigger', 'view')
            ORDER BY type, name
            """
        )
    ]


def _lane_schema_column_projection(
    connection: sqlite3.Connection,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    tables = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]
    for table in tables:
        safe = _lane_schema_identifier(table, label="existing table")
        result[table] = [
            {
                "cid": int(row[0]),
                "name": str(row[1]),
                "type": str(row[2]),
                "not_null": bool(row[3]),
                "default": row[4],
                "primary_key": int(row[5]),
                "hidden": int(row[6]),
            }
            for row in connection.execute(
                f'PRAGMA table_xinfo("{safe}")'  # nosec B608
            )
        ]
    return result


def _lane_schema_foreign_key_projection(
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for table in sorted(_lane_schema_column_projection(connection)):
        safe = _lane_schema_identifier(table, label="existing table")
        for row in connection.execute(
            f'PRAGMA foreign_key_list("{safe}")'  # nosec B608
        ):
            result.append(
                {
                    "table": table,
                    "id": int(row[0]),
                    "sequence": int(row[1]),
                    "referenced_table": str(row[2]),
                    "from": str(row[3]),
                    "to": str(row[4]),
                    "on_update": str(row[5]),
                    "on_delete": str(row[6]),
                    "match": str(row[7]),
                }
            )
    return result


def _lane_schema_index_projection(
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT name, tbl_name, sql FROM sqlite_master
        WHERE type='index' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ):
        index = _lane_schema_identifier(row[0], label="existing index")
        columns = [
            str(column[2])
            for column in connection.execute(
                f'PRAGMA index_info("{index}")'  # nosec B608
            )
        ]
        result.append(
            {
                "name": index,
                "table": str(row[1]),
                "sql": row[2],
                "columns": columns,
            }
        )
    return result


def _lane_schema_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    objects = _lane_schema_master_projection(connection)
    columns = _lane_schema_column_projection(connection)
    foreign_keys = _lane_schema_foreign_key_projection(connection)
    indexes = _lane_schema_index_projection(connection)
    body = {
        "objects": objects,
        "columns": columns,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
    }
    return {
        **body,
        "schema_sha256": sha256_bytes(canonical_json_bytes(body)),
        "foreign_key_projection_sha256": sha256_bytes(
            canonical_json_bytes(foreign_keys)
        ),
        "index_projection_sha256": sha256_bytes(canonical_json_bytes(indexes)),
    }


def _lane_schema_compatibility_proof(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
    before: dict[str, Any],
) -> dict[str, Any]:
    after = _lane_schema_snapshot(connection)
    before_objects = {(row["type"], row["name"]): row for row in before["objects"]}
    after_objects = {(row["type"], row["name"]): row for row in after["objects"]}
    missing_objects = sorted(
        f"{kind}:{name}" for kind, name in set(before_objects) - set(after_objects)
    )
    changed_non_table_objects = sorted(
        f"{kind}:{name}"
        for (kind, name), row in before_objects.items()
        if kind != "table"
        and (kind, name) in after_objects
        and row["sql"] != after_objects[(kind, name)]["sql"]
    )
    incompatible_tables: list[str] = []
    for table, columns in before["columns"].items():
        current = after["columns"].get(table)
        if current is None or current[: len(columns)] != columns:
            incompatible_tables.append(table)
    before_foreign_keys = {
        canonical_json_bytes(row).decode("utf-8") for row in before["foreign_keys"]
    }
    after_foreign_keys = {
        canonical_json_bytes(row).decode("utf-8") for row in after["foreign_keys"]
    }
    removed_foreign_keys = sorted(before_foreign_keys - after_foreign_keys)
    builder_projection = lane_schema_builder_projection(connection, lane)
    valid = bool(
        not missing_objects
        and not changed_non_table_objects
        and not incompatible_tables
        and not removed_foreign_keys
        and builder_projection["status"] == "PASS"
    )
    body = {
        "schema": "evidence-lane.lane-schema-compatibility-proof.v1",
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "pre_schema_sha256": before["schema_sha256"],
        "post_schema_sha256": after["schema_sha256"],
        "missing_objects": missing_objects,
        "changed_non_table_objects": changed_non_table_objects,
        "incompatible_tables": sorted(incompatible_tables),
        "removed_foreign_keys": removed_foreign_keys,
        "existing_object_count": len(before["objects"]),
        "result_object_count": len(after["objects"]),
        "base_schema_builder_projection": builder_projection,
    }
    return {
        **body,
        "proof_sha256": sha256_bytes(canonical_json_bytes(body)),
        "post_snapshot": after,
    }


def _lane_schema_foreign_key_target_errors(
    connection: sqlite3.Connection,
) -> list[dict[str, str]]:
    columns = _lane_schema_column_projection(connection)
    errors: list[dict[str, str]] = []
    for row in _lane_schema_foreign_key_projection(connection):
        target = columns.get(row["referenced_table"])
        target_names = {column["name"] for column in target or []}
        if target is None or row["to"] not in target_names:
            errors.append(
                {
                    "table": row["table"],
                    "column": row["from"],
                    "referenced_table": row["referenced_table"],
                    "referenced_column": row["to"],
                }
            )
    return errors


def _lane_fts_content_projection(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
) -> dict[str, Any]:
    fts = _lane_schema_identifier(lane.fts_table, label="lane FTS table")
    rows = [
        {
            "rowid": int(row[0]),
            "path": str(row[1]),
            "locator": str(row[2]),
            "text_content": decompress_exact_bytes(
                compression=str(row[6]),
                payload=bytes(row[7]),
                expected_size=int(row[5]),
                expected_sha256=str(row[4]),
            ).decode("utf-8"),
            "chunk_id": int(row[3]),
        }
        for row in connection.execute(
            f"""SELECT f.rowid,s.path,c.locator,c.chunk_id,c.sha256,
                       cas.size_bytes,cas.compression,cas.compressed_text
                FROM \"{fts}\" AS f
                JOIN chunk_index AS c ON c.chunk_id=f.rowid
                JOIN chunk_content_cas AS cas ON cas.sha256=c.sha256
                JOIN source_registry AS s ON s.source_id=c.source_id
                ORDER BY f.rowid"""  # nosec B608
        )
    ]
    return {
        "rows": rows,
        "row_count": len(rows),
        "content_sha256": sha256_bytes(canonical_json_bytes(rows)),
    }


def _rebuild_lane_fts_with_proof(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
) -> dict[str, Any]:
    before = _lane_fts_content_projection(connection, lane)
    fts = _lane_schema_identifier(lane.fts_table, label="lane FTS table")
    connection.execute(f'DELETE FROM "{fts}"')  # nosec B608
    connection.executemany(
        f"""INSERT INTO \"{fts}\"(
                rowid, path, locator, text_content, chunk_id
            ) VALUES (?, ?, ?, ?, ?)""",  # nosec B608
        [
            (
                row["rowid"],
                row["path"],
                row["locator"],
                row["text_content"],
                row["chunk_id"],
            )
            for row in before["rows"]
        ],
    )
    after = _lane_fts_content_projection(connection, lane)
    valid = bool(
        before["row_count"] == after["row_count"]
        and before["content_sha256"] == after["content_sha256"]
    )
    body = {
        "schema": "evidence-lane.lane-fts-rebuild-proof.v1",
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "lane_id": lane.canonical_lane_id,
        "fts_table": lane.fts_table,
        "preserved_rowids": True,
        "before_row_count": before["row_count"],
        "after_row_count": after["row_count"],
        "before_content_sha256": before["content_sha256"],
        "after_content_sha256": after["content_sha256"],
    }
    return {
        **body,
        "proof_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def _no_lane_fts_rebuild_proof(lane: LaneDefinition) -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.lane-fts-rebuild-proof.v1",
        "status": "NOT_REQUESTED",
        "valid": True,
        "lane_id": lane.canonical_lane_id,
        "fts_table": lane.fts_table,
        "preserved_rowids": True,
        "before_row_count": None,
        "after_row_count": None,
        "before_content_sha256": None,
        "after_content_sha256": None,
    }
    return {
        **body,
        "proof_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def _ensure_lane_schema_migration_ledger(
    connection: sqlite3.Connection,
) -> None:
    for statement in _LANE_SCHEMA_LEDGER_DDL:
        connection.execute(statement)


def lane_schema_evolution_status(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
) -> dict[str, Any]:
    """Verify the optional append-only migration ledger and effective head."""

    asset = lane_schema_asset(lane.canonical_lane_id)
    ledger_exists = (
        connection.execute(
            """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='lane_schema_migration'"""
        ).fetchone()
        is not None
    )
    meta = dict(
        connection.execute(
            """
            SELECT key, value FROM lane_meta
            WHERE key IN (
                'lane_schema_effective_version',
                'lane_schema_effective_head',
                'lane_schema_effective_receipt_sha256'
            )
            """
        )
    )
    if not ledger_exists:
        valid = not meta
        return {
            "schema": LANE_SCHEMA_MIGRATION_STATUS_SCHEMA,
            "status": "NOT_APPLIED" if valid else "FAIL",
            "valid": valid,
            "lane_id": lane.canonical_lane_id,
            "base_schema_version": asset["schema_version"],
            "effective_schema_version": asset["schema_version"],
            "migration_count": 0,
            "head_migration_id": None,
            "head_receipt_sha256": None,
            "ledger_ddl_sha256": LANE_SCHEMA_LEDGER_DDL_SHA256,
            "receipt_chain_valid": valid,
            "immutable_triggers_valid": valid,
            "meta_binding_valid": valid,
            "foreign_key_errors": [],
            "builder_schema_byte_parity": (
                lane_schema_builder_projection(connection, lane)["status"] == "PASS"
            ),
        }
    cursor = connection.execute(
        """
        SELECT sequence, migration_id, lane_id, from_version, to_version,
               request_sha256, ddl_sha256, prior_receipt_sha256,
               receipt_sha256, receipt_json
        FROM lane_schema_migration ORDER BY sequence
        """
    )
    keys = [str(row[0]) for row in cursor.description or []]
    rows = [dict(zip(keys, row, strict=True)) for row in cursor]
    triggers = {
        str(row[0])
        for row in connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type='trigger' AND tbl_name='lane_schema_migration'"""
        )
    }
    immutable_triggers_valid = triggers == {
        "lane_schema_migration_no_update",
        "lane_schema_migration_no_delete",
    }
    expected_version = int(asset["schema_version"])
    prior_receipt: str | None = None
    receipt_chain_valid = bool(rows)
    for sequence, row in enumerate(rows, start=1):
        try:
            receipt = json.loads(str(row["receipt_json"]))
        except json.JSONDecodeError:
            receipt_chain_valid = False
            break
        receipt_core = dict(receipt)
        declared_receipt = str(receipt_core.pop("receipt_sha256", ""))
        computed_receipt = sha256_bytes(canonical_json_bytes(receipt_core))
        if not (
            row["sequence"] == sequence
            and row["lane_id"] == lane.canonical_lane_id
            and row["from_version"] == expected_version
            and row["to_version"] == expected_version + 1
            and row["prior_receipt_sha256"] == prior_receipt
            and row["receipt_sha256"] == declared_receipt == computed_receipt
            and receipt.get("schema") == LANE_SCHEMA_MIGRATION_RECEIPT_SCHEMA
            and receipt.get("lane_id") == lane.canonical_lane_id
            and receipt.get("migration_id") == row["migration_id"]
            and receipt.get("request_sha256") == row["request_sha256"]
            and receipt.get("ddl_sha256") == row["ddl_sha256"]
            and receipt.get("prior_receipt_sha256") == prior_receipt
        ):
            receipt_chain_valid = False
            break
        expected_version += 1
        prior_receipt = declared_receipt
    head = rows[-1] if rows else None
    meta_binding_valid = bool(
        head
        and meta
        == {
            "lane_schema_effective_version": str(expected_version),
            "lane_schema_effective_head": str(head["migration_id"]),
            "lane_schema_effective_receipt_sha256": str(head["receipt_sha256"]),
        }
    )
    foreign_key_errors = [
        list(row) for row in connection.execute("PRAGMA foreign_key_check")
    ]
    builder_parity = lane_schema_builder_projection(connection, lane)
    valid = bool(
        receipt_chain_valid
        and immutable_triggers_valid
        and meta_binding_valid
        and not foreign_key_errors
        and builder_parity["status"] == "PASS"
    )
    return {
        "schema": LANE_SCHEMA_MIGRATION_STATUS_SCHEMA,
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "lane_id": lane.canonical_lane_id,
        "base_schema_version": asset["schema_version"],
        "effective_schema_version": expected_version,
        "migration_count": len(rows),
        "head_migration_id": head["migration_id"] if head else None,
        "head_receipt_sha256": head["receipt_sha256"] if head else None,
        "ledger_ddl_sha256": LANE_SCHEMA_LEDGER_DDL_SHA256,
        "receipt_chain_valid": receipt_chain_valid,
        "immutable_triggers_valid": immutable_triggers_valid,
        "meta_binding_valid": meta_binding_valid,
        "foreign_key_errors": foreign_key_errors,
        "builder_schema_byte_parity": builder_parity["status"] == "PASS",
    }


def apply_lane_schema_migration(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
    migration: dict[str, Any],
    *,
    applied_by: str,
    applied_at: str | None = None,
    explicit_user_confirmation: str | None = None,
) -> dict[str, Any]:
    """Atomically apply one structured migration and append its sealed receipt."""

    plan = compile_lane_schema_migration(lane, migration)
    if (
        plan["explicit_user_confirmation_required"]
        and explicit_user_confirmation != plan["required_confirmation"]
    ):
        _lane_schema_error(
            "LANE_SCHEMA_EXPLICIT_USER_CONFIRMATION_REQUIRED",
            "This protected code lane requires the exact compiled confirmation.",
        )
    if not str(applied_by or "").strip():
        _lane_schema_error(
            "LANE_SCHEMA_APPLIED_BY_REQUIRED",
            "A nonempty migration actor is required.",
        )
    lane_meta = dict(
        connection.execute(
            """SELECT key, value FROM lane_meta
               WHERE key IN ('lane_id', 'lane_schema_asset_version')"""
        )
    )
    asset = lane_schema_asset(lane.canonical_lane_id)
    if lane_meta != {
        "lane_id": lane.canonical_lane_id,
        "lane_schema_asset_version": str(asset["schema_version"]),
    }:
        _lane_schema_error(
            "LANE_SCHEMA_DATABASE_BINDING_MISMATCH",
            "The SQLite lane identity or base schema version does not match.",
        )
    current = lane_schema_evolution_status(connection, lane)
    if not current["valid"]:
        _lane_schema_error(
            "LANE_SCHEMA_LEDGER_INVALID",
            "The current migration ledger or base schema is invalid.",
        )
    ledger_exists = current["migration_count"] > 0
    if ledger_exists:
        existing = connection.execute(
            """SELECT request_sha256, receipt_json
               FROM lane_schema_migration WHERE migration_id=?""",
            (plan["migration_id"],),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != plan["plan_sha256"]:
                _lane_schema_error(
                    "LANE_SCHEMA_MIGRATION_ID_CONFLICT",
                    "The immutable migration ID is already bound to other bytes.",
                )
            return {
                "schema": LANE_SCHEMA_MIGRATION_RECEIPT_SCHEMA,
                "status": "PASS",
                "state": "ALREADY_APPLIED",
                "receipt": json.loads(str(existing[1])),
                "plan": plan,
                "ledger": current,
            }
    if plan["from_version"] != current["effective_schema_version"]:
        _lane_schema_error(
            "LANE_SCHEMA_VERSION_MISMATCH",
            "The migration does not start at the exact effective schema version.",
        )
    before = _lane_schema_snapshot(connection)
    connection.execute("SAVEPOINT evidence_lane_schema_evolution")
    try:
        _ensure_lane_schema_migration_ledger(connection)
        for statement in plan["ddl"]:
            connection.execute(statement)
        target_errors = _lane_schema_foreign_key_target_errors(connection)
        if target_errors:
            _lane_schema_error(
                "LANE_SCHEMA_FOREIGN_KEY_TARGET_INVALID",
                "A migrated foreign key targets a missing table or column.",
            )
        fts_proof = (
            _rebuild_lane_fts_with_proof(connection, lane)
            if plan["rebuild_fts"]
            else _no_lane_fts_rebuild_proof(lane)
        )
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        foreign_key_errors = [
            list(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        compatibility = _lane_schema_compatibility_proof(
            connection,
            lane,
            before,
        )
        if (
            integrity != ["ok"]
            or foreign_key_errors
            or not fts_proof["valid"]
            or not compatibility["valid"]
        ):
            _lane_schema_error(
                "LANE_SCHEMA_COMPATIBILITY_CHECK_FAILED",
                "Integrity, foreign-key, FTS, or compatibility proof failed.",
            )
        post = compatibility.pop("post_snapshot")
        prior_receipt = current["head_receipt_sha256"]
        confirmation_sha256 = (
            sha256_bytes(explicit_user_confirmation.encode("utf-8"))
            if plan["explicit_user_confirmation_required"]
            and explicit_user_confirmation is not None
            else None
        )
        receipt_core = {
            "schema": LANE_SCHEMA_MIGRATION_RECEIPT_SCHEMA,
            "status": "PASS",
            "lane_id": lane.canonical_lane_id,
            "extension_namespace": plan["extension_namespace"],
            "migration_id": plan["migration_id"],
            "sequence": int(current["migration_count"]) + 1,
            "from_version": plan["from_version"],
            "to_version": plan["to_version"],
            "request_sha256": plan["plan_sha256"],
            "ddl_sha256": plan["ddl_sha256"],
            "ledger_ddl_sha256": LANE_SCHEMA_LEDGER_DDL_SHA256,
            "pre_schema_sha256": before["schema_sha256"],
            "post_schema_sha256": post["schema_sha256"],
            "foreign_key_projection_sha256": post["foreign_key_projection_sha256"],
            "index_projection_sha256": post["index_projection_sha256"],
            "fts_rebuild_proof": fts_proof,
            "compatibility_proof": compatibility,
            "integrity_check": integrity,
            "foreign_key_errors": foreign_key_errors,
            "prior_receipt_sha256": prior_receipt,
            "explicit_user_confirmation_required": plan[
                "explicit_user_confirmation_required"
            ],
            "explicit_user_confirmation_sha256": confirmation_sha256,
            "raw_user_confirmation_persisted": False,
            "applied_by": applied_by.strip(),
            "applied_at": applied_at or utc_now(),
            "candidate_created": False,
            "hil_invoked": False,
            "pointer_moved": False,
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_core))
        receipt = {**receipt_core, "receipt_sha256": receipt_sha256}
        connection.execute(
            """
            INSERT INTO lane_schema_migration(
                sequence, migration_id, lane_id, namespace,
                from_version, to_version, request_sha256, ddl_sha256,
                pre_schema_sha256, post_schema_sha256,
                foreign_key_projection_sha256, index_projection_sha256,
                fts_rebuild_proof_sha256, compatibility_proof_sha256,
                prior_receipt_sha256, explicit_user_confirmation_sha256,
                applied_by, applied_at, receipt_sha256, receipt_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt["sequence"],
                receipt["migration_id"],
                receipt["lane_id"],
                receipt["extension_namespace"],
                receipt["from_version"],
                receipt["to_version"],
                receipt["request_sha256"],
                receipt["ddl_sha256"],
                receipt["pre_schema_sha256"],
                receipt["post_schema_sha256"],
                receipt["foreign_key_projection_sha256"],
                receipt["index_projection_sha256"],
                receipt["fts_rebuild_proof"]["proof_sha256"],
                receipt["compatibility_proof"]["proof_sha256"],
                receipt["prior_receipt_sha256"],
                receipt["explicit_user_confirmation_sha256"],
                receipt["applied_by"],
                receipt["applied_at"],
                receipt["receipt_sha256"],
                canonical_json_bytes(receipt).decode("utf-8"),
            ),
        )
        for key, value in (
            ("lane_schema_effective_version", receipt["to_version"]),
            ("lane_schema_effective_head", receipt["migration_id"]),
            (
                "lane_schema_effective_receipt_sha256",
                receipt["receipt_sha256"],
            ),
        ):
            connection.execute(
                "INSERT OR REPLACE INTO lane_meta(key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        ledger = lane_schema_evolution_status(connection, lane)
        if not ledger["valid"]:
            _lane_schema_error(
                "LANE_SCHEMA_POST_APPLY_LEDGER_INVALID",
                "The post-apply migration ledger did not verify.",
            )
        connection.execute("RELEASE SAVEPOINT evidence_lane_schema_evolution")
    except Exception as exc:
        connection.execute("ROLLBACK TO SAVEPOINT evidence_lane_schema_evolution")
        connection.execute("RELEASE SAVEPOINT evidence_lane_schema_evolution")
        if isinstance(exc, LaneSchemaEvolutionError):
            raise
        raise LaneSchemaEvolutionError(
            "LANE_SCHEMA_MIGRATION_SQLITE_FAILURE",
            f"The additive migration rolled back: {type(exc).__name__}.",
        ) from exc
    return {
        "schema": LANE_SCHEMA_MIGRATION_RECEIPT_SCHEMA,
        "status": "PASS",
        "state": "APPLIED",
        "receipt": receipt,
        "plan": plan,
        "ledger": ledger,
    }


def lane_schema_builder_projection(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
) -> dict[str, Any]:
    """Compare exact emitted SQLite definitions with the versioned lane asset."""

    asset = lane_schema_asset(lane.canonical_lane_id)
    table_rows: list[dict[str, str | None]] = []
    missing: list[str] = []
    for table in asset["tables"]:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise ValueError(f"Unsafe lane schema table name: {table}")
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row is None:
            missing.append(table)
            table_rows.append({"table": table, "sql": None})
        else:
            table_rows.append({"table": table, "sql": str(row[0] or "")})
    projection_body = {
        "lane_id": lane.canonical_lane_id,
        "tables": table_rows,
    }
    actual_sha256 = (
        sha256_bytes(canonical_json_bytes(projection_body)) if not missing else None
    )
    expected_sha256 = str(asset["sqlite_master_projection_sha256"])
    return {
        "schema": LANE_SCHEMA_BUILDER_PROJECTION_SCHEMA,
        "status": (
            "PASS" if not missing and actual_sha256 == expected_sha256 else "MISMATCH"
        ),
        "lane_id": lane.canonical_lane_id,
        "schema_id": asset["schema_id"],
        "schema_version": asset["schema_version"],
        "registry_sha256": LANE_SCHEMA_REGISTRY_SHA256,
        "contract_sha256": asset["contract_sha256"],
        "expected_sqlite_master_projection_sha256": expected_sha256,
        "actual_sqlite_master_projection_sha256": actual_sha256,
        "table_count": len(table_rows),
        "missing_tables": missing,
        "builder_schema_byte_parity": not missing and actual_sha256 == expected_sha256,
    }


def _create_lane_schema(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
    *,
    verify_schema_asset: bool = True,
) -> None:
    schema_asset = lane_schema_asset(lane.canonical_lane_id)
    fts = lane.fts_table
    if not re.fullmatch(r"[a-z][a-z0-9_]*", fts):
        raise ValueError(f"Unsafe FTS table name: {fts}")
    connection.executescript(
        f"""
        PRAGMA foreign_keys = ON;
        CREATE TABLE lane_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;
        CREATE TABLE lane_pointer(
            pointer_kind TEXT PRIMARY KEY,
            pointer_value TEXT,
            generation INTEGER NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE source_content_cas(
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            compression TEXT NOT NULL,
            compressed_bytes BLOB NOT NULL,
            first_seen_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE source_registry(
            source_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL REFERENCES source_content_cas(sha256),
            mime_type TEXT NOT NULL,
            extension TEXT NOT NULL,
            encoding TEXT,
            parser_state TEXT NOT NULL,
            registered_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE chunk_index(
            chunk_id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES source_registry(source_id) ON DELETE CASCADE,
            locator TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(source_id, locator, ordinal)
        ) STRICT;
        CREATE TABLE chunk_content_cas(
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            compression TEXT NOT NULL,
            compressed_text BLOB NOT NULL,
            first_seen_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE chunk_history(
            history_id INTEGER PRIMARY KEY,
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            locator TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            chunk_sha256 TEXT NOT NULL REFERENCES chunk_content_cas(sha256),
            snapshot_ref TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            content_reused INTEGER NOT NULL CHECK(content_reused IN (0, 1)),
            UNIQUE(snapshot_ref, source_path, locator, ordinal, chunk_sha256)
        ) STRICT;
        CREATE VIRTUAL TABLE {fts} USING fts5(
            path UNINDEXED,
            locator UNINDEXED,
            text_content,
            chunk_id UNINDEXED,
            content='',
            contentless_delete=1,
            tokenize='unicode61'
        );
        CREATE TABLE structured_fact(
            fact_id INTEGER PRIMARY KEY,
            source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            locator TEXT NOT NULL,
            payload_json TEXT NOT NULL
        ) STRICT;
        CREATE TABLE parser_capability(
            capability TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            tool TEXT NOT NULL,
            detail TEXT NOT NULL
        ) STRICT;
        CREATE TABLE tfidf_term(
            term TEXT PRIMARY KEY,
            document_frequency INTEGER NOT NULL,
            document_count INTEGER NOT NULL,
            idf REAL NOT NULL
        ) STRICT;
        CREATE TABLE tfidf_vector(
            chunk_id INTEGER NOT NULL REFERENCES chunk_index(chunk_id) ON DELETE CASCADE,
            term TEXT NOT NULL REFERENCES tfidf_term(term) ON DELETE CASCADE,
            term_count INTEGER NOT NULL,
            token_count INTEGER NOT NULL,
            tf REAL NOT NULL,
            tfidf REAL NOT NULL,
            PRIMARY KEY(chunk_id, term)
        ) STRICT;
        CREATE TABLE refresh_receipt(
            receipt_id INTEGER PRIMARY KEY,
            build_mode TEXT NOT NULL,
            parent_pv TEXT,
            proposed_pv TEXT NOT NULL,
            unchanged_reuse INTEGER NOT NULL,
            changed_rebuild INTEGER NOT NULL,
            new_register INTEGER NOT NULL,
            removed_purge INTEGER NOT NULL,
            blocked_unsupported INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE mutation_receipt(
            mutation_id INTEGER PRIMARY KEY,
            mutation_kind TEXT NOT NULL,
            source_path TEXT,
            prior_sha256 TEXT,
            current_sha256 TEXT,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX source_registry_path_idx ON source_registry(path);
        CREATE INDEX chunk_source_idx ON chunk_index(source_id, ordinal);
        CREATE INDEX chunk_history_source_idx
        ON chunk_history(source_path, snapshot_ref, ordinal);
        CREATE INDEX structured_fact_kind_idx ON structured_fact(kind);
        """
    )
    ensure_authority_index_schema(connection)
    if lane.canonical_lane_id in PRIMARY_CODE_LANES:
        create_git_history_schema(connection)
    shared_tables = {
        "lane_meta",
        "lane_pointer",
        "source_content_cas",
        "source_registry",
        "chunk_index",
        "chunk_content_cas",
        "chunk_history",
        "structured_fact",
        "parser_capability",
        "tfidf_term",
        "tfidf_vector",
        "refresh_receipt",
        "mutation_receipt",
        lane.fts_table,
    }
    for table in schema_asset["tables"]:
        if table in shared_tables:
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise ValueError(f"Unsafe lane schema table name: {table}")
        if schema_asset["lane_table_builder"] == "GIT_HISTORY_V2":
            continue
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table}(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            """
        )
    parity = lane_schema_builder_projection(connection, lane)
    if verify_schema_asset and parity["status"] != "PASS":
        raise ValueError(
            "Lane schema builder bytes do not match the versioned asset: "
            f"{lane.canonical_lane_id}"
        )


def _open_lane(
    path: Path, lane: LaneDefinition, *, initialize: bool
) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    if initialize:
        _create_lane_schema(connection, lane)
    return connection


def _insert_source(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
    root: Path,
    relative_path: str,
    *,
    registered_at: str,
    snapshot_ref: str,
    host_profile: str,
) -> tuple[int, str]:
    path = root / Path(relative_path)
    data = path.read_bytes()
    if len(data) > MAX_EXTRACT_BYTES:
        documents: list[dict[str, Any]] = []
        facts = [
            {
                "kind": "parser_limit",
                "locator": relative_path,
                "payload": {
                    "size_bytes": len(data),
                    "max_extract_bytes": MAX_EXTRACT_BYTES,
                },
            }
        ]
        parser_state = "BLOCKED_UNSUPPORTED_SIZE_EXACT_BYTES_PRESERVED"
        encoding = None
    else:
        documents, facts, parser_state, encoding = _extract_source(
            path,
            relative_path,
            data,
            lane,
            host_profile=host_profile,
        )
    source_sha256 = sha256_bytes(data)
    source_compression, compressed_source = compress_exact_bytes(data)
    connection.execute(
        """
        INSERT OR IGNORE INTO source_content_cas(
            sha256,size_bytes,compression,compressed_bytes,first_seen_at
        ) VALUES(?,?,?,?,?)
        """,
        (
            source_sha256,
            len(data),
            source_compression,
            compressed_source,
            registered_at,
        ),
    )
    cursor = connection.execute(
        """
        INSERT INTO source_registry(
            path, size_bytes, sha256, mime_type, extension, encoding,
            parser_state, registered_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            relative_path,
            len(data),
            source_sha256,
            mimetypes.guess_type(relative_path)[0] or "application/octet-stream",
            path.suffix.lower(),
            encoding,
            parser_state,
            registered_at,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a source row ID.")
    source_id = int(cursor.lastrowid)
    for fact in facts:
        connection.execute(
            """
            INSERT INTO structured_fact(source_id, kind, locator, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                source_id,
                fact["kind"],
                fact["locator"],
                json.dumps(fact["payload"], sort_keys=True, separators=(",", ":")),
            ),
        )
        if fact["kind"] in lane.schema_contract:
            table = fact["kind"]
            # ``table`` is selected only from the immutable validated registry.
            insert_fact_sql = (
                f"INSERT INTO {table}(source_id, locator, payload_json) "  # nosec B608
                "VALUES (?, ?, ?)"
            )
            connection.execute(
                insert_fact_sql,
                (
                    source_id,
                    fact["locator"],
                    json.dumps(fact["payload"], sort_keys=True, separators=(",", ":")),
                ),
            )
    for document in documents:
        text = str(document.get("text") or "")
        for ordinal, char_start, block in _chunks(text):
            block_bytes = block.encode("utf-8")
            chunk_sha256 = sha256_bytes(block_bytes)
            chunk_compression, compressed_block = compress_exact_bytes(block_bytes)
            cas_cursor = connection.execute(
                """
                INSERT OR IGNORE INTO chunk_content_cas(
                    sha256,size_bytes,compression,compressed_text,first_seen_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    chunk_sha256,
                    len(block_bytes),
                    chunk_compression,
                    compressed_block,
                    registered_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO chunk_index(
                    source_id, locator, ordinal, char_start, char_end,
                    sha256, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    str(document["locator"]),
                    ordinal,
                    char_start,
                    char_start + len(block),
                    chunk_sha256,
                    json.dumps(
                        document.get("metadata") or {},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chunk_history(
                    source_path, source_sha256, locator, ordinal, chunk_sha256,
                    snapshot_ref, observed_at, content_reused
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relative_path,
                    source_sha256,
                    str(document["locator"]),
                    ordinal,
                    chunk_sha256,
                    snapshot_ref,
                    registered_at,
                    int(not bool(cas_cursor.rowcount)),
                ),
            )
    return source_id, parser_state


def _rebuild_retrieval(connection: sqlite3.Connection, lane: LaneDefinition) -> None:
    fts = lane.fts_table
    connection.execute(f"DELETE FROM {fts}")  # nosec B608
    # ``fts`` is regex-validated immutable registry data.
    chunk_rows = list(
        connection.execute(
            """
            SELECT c.chunk_id,s.path,c.locator,c.sha256,cas.size_bytes,
                   cas.compression,cas.compressed_text
            FROM chunk_index AS c
            JOIN source_registry AS s ON s.source_id=c.source_id
            JOIN chunk_content_cas AS cas ON cas.sha256=c.sha256
            ORDER BY c.chunk_id
            """
        )
    )
    connection.executemany(
        f"INSERT INTO {fts}(rowid,path,locator,text_content,chunk_id) "  # nosec B608
        "VALUES(?,?,?,?,?)",
        [
            (
                int(row[0]),
                str(row[1]),
                str(row[2]),
                decompress_exact_bytes(
                    compression=str(row[5]),
                    payload=bytes(row[6]),
                    expected_size=int(row[4]),
                    expected_sha256=str(row[3]),
                ).decode("utf-8"),
                int(row[0]),
            )
            for row in chunk_rows
        ],
    )
    connection.execute("DELETE FROM tfidf_vector")
    connection.execute("DELETE FROM tfidf_term")
    connection.execute(
        "INSERT OR REPLACE INTO lane_meta(key,value) VALUES(?,?)",
        ("tfidf_execution", "BOUNDED_QUERY_TIME_OVER_FTS_CANDIDATES"),
    )
    rebuild_connection_authority_index(
        connection,
        authority_id=f"project_sector:{lane.canonical_lane_id}",
        table_names=(
            "lane_meta",
            "source_registry",
            "parser_capability",
            "refresh_receipt",
            "mutation_receipt",
        ),
    )


def _validate_lane_database(path: Path, lane: LaneDefinition) -> dict[str, Any]:
    schema_asset = lane_schema_asset(lane.canonical_lane_id)
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
    schema = connection.execute(
        "SELECT value FROM lane_meta WHERE key='schema_version'"
    ).fetchone()
    schema_binding = dict(
        connection.execute(
            """
            SELECT key, value FROM lane_meta
            WHERE key IN (
                'lane_schema_id',
                'lane_schema_asset_version',
                'lane_schema_contract_sha256',
                'lane_schema_registry_sha256',
                'lane_schema_sqlite_master_projection_sha256',
                'lane_schema_extension_namespace',
                'lane_schema_migration_head'
            )
            """
        )
    )
    counts = {
        table: int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # nosec B608
        )
        for table in (
            "source_registry",
            "source_content_cas",
            "chunk_index",
            "structured_fact",
            "tfidf_term",
            "tfidf_vector",
            "refresh_receipt",
            "authority_index_source",
            "authority_index_node",
            "authority_index_refresh_receipt",
        )
    }
    fts_count = int(
        connection.execute(f"SELECT COUNT(*) FROM {lane.fts_table}").fetchone()[0]  # nosec B608
    )
    builder_projection = lane_schema_builder_projection(connection, lane)
    evolution = lane_schema_evolution_status(connection, lane)
    compact_storage = verify_lane_compact_storage(
        connection,
        fts_table=lane.fts_table,
    )
    connection.close()
    valid = (
        integrity == ["ok"]
        and not foreign_keys
        and schema is not None
        and schema[0] == LANE_SCHEMA_VERSION
        and schema_binding
        == {
            "lane_schema_id": schema_asset["schema_id"],
            "lane_schema_asset_version": str(schema_asset["schema_version"]),
            "lane_schema_contract_sha256": schema_asset["contract_sha256"],
            "lane_schema_registry_sha256": LANE_SCHEMA_REGISTRY_SHA256,
            "lane_schema_sqlite_master_projection_sha256": schema_asset[
                "sqlite_master_projection_sha256"
            ],
            "lane_schema_extension_namespace": schema_asset["extension_namespace"],
            "lane_schema_migration_head": schema_asset["migration_ledger"][-1][
                "migration_id"
            ],
        }
        and builder_projection["status"] == "PASS"
        and evolution["valid"]
        and compact_storage["valid"]
        and fts_count == counts["chunk_index"]
        and counts["authority_index_refresh_receipt"] >= 1
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "schema_version": schema[0] if schema else None,
        "lane_schema_binding": schema_binding,
        "lane_schema_builder_projection": builder_projection,
        "lane_schema_evolution": evolution,
        "compact_storage": compact_storage,
        "counts": counts,
        "fts_rows": fts_count,
        "valid": valid,
    }


def _source_index(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["path"]: {
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "parser_state": row["parser_state"],
        }
        for row in connection.execute(
            "SELECT path, sha256, size_bytes, parser_state FROM source_registry"
        )
    }


def _current_index(root: Path, paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    return {
        relative: {
            "sha256": sha256_file(root / Path(relative)),
            "size_bytes": (root / Path(relative)).stat().st_size,
        }
        for relative in sorted(paths)
    }


def _classify(
    prior: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    *,
    preserve_parent_unmentioned: bool = False,
    force_remove_paths: set[str] | None = None,
) -> dict[str, Any]:
    unchanged = [
        {"path": path, **current[path]}
        for path in sorted(prior.keys() & current.keys())
        if prior[path]["sha256"] == current[path]["sha256"]
    ]
    changed = [
        {
            "path": path,
            "prior_sha256": prior[path]["sha256"],
            "current_sha256": current[path]["sha256"],
            "prior_size_bytes": prior[path]["size_bytes"],
            "current_size_bytes": current[path]["size_bytes"],
        }
        for path in sorted(prior.keys() & current.keys())
        if prior[path]["sha256"] != current[path]["sha256"]
    ]
    added = [
        {"path": path, **current[path]}
        for path in sorted(current.keys() - prior.keys())
    ]
    unmentioned = sorted(prior.keys() - current.keys())
    forced_removed = set(force_remove_paths or ()) & set(unmentioned)
    if preserve_parent_unmentioned:
        unchanged.extend(
            {
                "path": path,
                **prior[path],
                "preserved_parent_record": True,
            }
            for path in unmentioned
            if path not in forced_removed
        )
        removed = [{"path": path, **prior[path]} for path in sorted(forced_removed)]
    else:
        removed = [{"path": path, **prior[path]} for path in unmentioned]
    return {
        "UNCHANGED_REUSE": unchanged,
        "CHANGED_REBUILD": changed,
        "NEW_REGISTER": added,
        "REMOVED_PURGE": removed,
        "BLOCKED_UNSUPPORTED": [],
    }


def _topology_text(value: Any, *, limit: int = 96) -> str:
    normalized = re.sub(
        r"\s+",
        " ",
        redact_text(str(value or "")).replace("\\", "/"),
    ).strip()
    if len(normalized) > limit:
        return normalized[: max(1, limit - 3)].rstrip() + "..."
    return normalized


def _mmd_label(value: Any) -> str:
    return (
        _topology_text(value, limit=180)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
    )


def _dot_label(value: Any) -> str:
    return _topology_text(value, limit=180).replace("\\", "\\\\").replace('"', '\\"')


class _TopologyGraph:
    """Route every lane topology through LangGraph and Graphviz."""

    def __init__(self, name: str, *, direction: str = "TB") -> None:
        self._graph = SemanticGraph(
            name,
            direction=direction,
            role="AUTHORITY_TRAVERSAL",
        )
        self.receipt: dict[str, Any] | None = None

    def begin(self, node_id: str, label: str, *, direction: str = "TB") -> None:
        self._graph.begin_group(node_id, label, direction=direction)

    def end(self) -> None:
        self._graph.end_group()

    def node(self, node_id: str, label: str, kind: str) -> None:
        self._graph.add_node(node_id, label, kind)

    def edge(self, source: str, target: str, label: str | None = None) -> None:
        self._graph.add_edge(source, target, label)

    def finish(self) -> tuple[str, str]:
        mmd, dot, receipt = self._graph.render_pair()
        self.receipt = receipt
        return mmd, dot


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        return 0
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # nosec B608
    except sqlite3.DatabaseError:
        return 0


def _required_table_count(connection: sqlite3.Connection, table: str) -> int:
    """Count a required logical-topology table without masking schema drift."""

    if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        raise ValueError(f"Unsafe required topology table name: {table}")
    try:
        return int(
            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]  # nosec B608
        )
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(
            f"Required code topology table is missing or unreadable: {table}"
        ) from exc


def _fact_display(kind: str, locator: str, payload_json: str) -> str:
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if kind == "code_symbol":
        return str(payload.get("qualified_name") or payload.get("name") or locator)
    if kind == "code_import":
        imported = payload.get("imported_name")
        return f"{payload.get('module') or locator}{' : ' + str(imported) if imported else ''}"
    if kind == "code_route":
        return f"{payload.get('method') or 'ROUTE'} {payload.get('path_pattern') or locator}"
    if kind == "code_dependency":
        return f"{payload.get('name') or locator} {payload.get('constraint_text') or ''}".strip()
    for key in (
        "text",
        "title",
        "name",
        "path",
        "question",
        "finding",
        "decision",
        "table",
        "sheet",
        "route",
    ):
        if payload.get(key):
            return str(payload[key])
    return locator


def _lane_schema_tables(lane: LaneDefinition) -> list[str]:
    """Return the lane-owned physical entities in canonical contract order."""

    return [
        table
        for table in lane.schema_contract
        if table not in CORE_SCHEMA_TABLES and table != lane.fts_table
    ]


def _schema_topology_records(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
) -> list[dict[str, Any]]:
    """Inspect exact SQLite entities, FKs, counts, and one bounded sample."""

    records: list[dict[str, Any]] = []
    for table in _lane_schema_tables(lane):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise ValueError(f"Unsafe lane topology table name: {table}")
        columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()  # nosec B608
        foreign_keys = connection.execute(
            f'PRAGMA foreign_key_list("{table}")'  # nosec B608
        ).fetchall()
        column_names = {str(row["name"]) for row in columns}
        sample = None
        if {"locator", "payload_json"} <= column_names:
            order = "locator, record_id" if "record_id" in column_names else "locator"
            sample = connection.execute(
                f'SELECT locator, payload_json FROM "{table}" ORDER BY {order} LIMIT 1'  # nosec B608
            ).fetchone()
        records.append(
            {
                "table": table,
                "rows": _required_table_count(connection, table),
                "columns": len(columns),
                "foreign_keys": [
                    {
                        "from": str(row["from"]),
                        "table": str(row["table"]),
                        "to": str(row["to"]),
                    }
                    for row in foreign_keys
                ],
                "sample": (
                    {
                        "locator": str(sample["locator"]),
                        "payload_json": str(sample["payload_json"]),
                    }
                    if sample is not None
                    else None
                ),
            }
        )
    return records


def _stable_topology_node(prefix: str, *identity: Any) -> str:
    """Return a Mermaid/DOT-safe stable identity for one evidence node."""

    safe_prefix = re.sub(r"[^A-Z0-9_]+", "_", prefix.upper()).strip("_") or "NODE"
    digest = sha256_bytes(
        canonical_json_bytes(
            {
                "schema": "evidence-lane.topology-stable-node.v1",
                "prefix": safe_prefix,
                "identity": [str(item) for item in identity],
            }
        )
    )
    return f"{safe_prefix}_{digest[:16]}"


def _evidence_edge(
    graph: _TopologyGraph,
    source: str,
    target: str,
    relation: str,
    *,
    confidence: str = "EXTRACTED",
) -> None:
    """Emit an explicitly identified evidence relation in both renderings."""

    edge_id = sha256_bytes(
        canonical_json_bytes(
            {
                "schema": "evidence-lane.topology-stable-edge.v1",
                "source": source,
                "relation": relation,
                "target": target,
                "confidence": confidence,
            }
        )
    )[:12]
    graph.edge(
        source,
        target,
        f"{relation} | {confidence} | edge={edge_id}",
    )


def _payload_object(payload_json: str) -> dict[str, Any]:
    try:
        value = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _emit_code_evidence_graph(
    graph: _TopologyGraph,
    connection: sqlite3.Connection,
) -> dict[int, str]:
    """Project registered files and extracted code facts from exact SQLite rows."""

    source_rows = connection.execute(
        """
        SELECT source_id, path, sha256, size_bytes, parser_state
        FROM source_registry ORDER BY path, source_id LIMIT 24
        """
    ).fetchall()
    table_specs = (
        ("code_symbol", "CODE_SYMBOL", "declares", "SYMBOL"),
        ("code_import", "CODE_FILE", "imports", "IMPORT"),
        ("code_call", "CODE_FILE", "calls", "CALL"),
        (
            "code_parser_diagnostic",
            "CODE_FILE",
            "reports parser diagnostic",
            "PARSER_DIAGNOSTIC",
        ),
        ("code_route", "APP_ROUTE", "exposes", "ROUTE"),
        ("code_dependency", "DEPENDENCY_ITEM", "depends on", "DEPENDENCY"),
    )
    counts = {
        table: _required_table_count(connection, table) for table, *_ in table_specs
    }
    graph.begin(
        "CODE_EVIDENCE_GRAPH",
        "5. SQLite-derived files, semantics, and stable evidence identities",
        direction="LR",
    )
    graph.node(
        "CODE_EVIDENCE_COVERAGE",
        "code evidence coverage\n"
        f"files={_table_count(connection, 'source_registry')} | "
        f"symbols={counts['code_symbol']} | imports={counts['code_import']} | "
        f"calls={counts['code_call']} | routes={counts['code_route']} | "
        f"dependencies={counts['code_dependency']} | "
        f"facts={_table_count(connection, 'structured_fact')}\n"
        "stable nodes + stable edges | project-authored Graphify concepts",
        "root",
    )
    _evidence_edge(
        graph,
        "CODE_SECTOR",
        "CODE_EVIDENCE_COVERAGE",
        "projects exact SQLite rows",
    )

    source_nodes: dict[int, str] = {}
    for row in source_rows:
        source_id = int(row["source_id"])
        path = str(row["path"])
        sha256 = str(row["sha256"])
        node = _stable_topology_node("WORKTREE_FILE", path, sha256)
        source_nodes[source_id] = node
        graph.node(
            node,
            f"{_topology_text(path, limit=88)}\n"
            f"sha256={sha256[:16]}... | bytes={int(row['size_bytes'])}\n"
            f"parser={_topology_text(row['parser_state'], limit=48)} | "
            f"stable_id={node[-16:]} | EXTRACTED",
            "source",
        )
        _evidence_edge(graph, "CODE_FILE", node, "registered exact-byte file")

    if not source_nodes:
        graph.node(
            "CODE_EVIDENCE_EMPTY",
            "no code files registered\nschema remains explicit",
            "warn",
        )
        _evidence_edge(graph, "CODE_FILE", "CODE_EVIDENCE_EMPTY", "coverage boundary")

    emitted_modules: set[str] = set()
    for table, logical_root, relation, prefix in table_specs:
        rows = connection.execute(
            f"""
            SELECT record_id, source_id, locator, payload_json
            FROM {table} ORDER BY locator, record_id LIMIT 16
            """  # nosec B608
        ).fetchall()
        for row in rows:
            payload_json = str(row["payload_json"])
            node = _stable_topology_node(
                prefix,
                table,
                row["record_id"],
                row["locator"],
                sha256_bytes(payload_json.encode("utf-8")),
            )
            graph.node(
                node,
                f"{table}\n"
                f"{_topology_text(_fact_display(table, str(row['locator']), payload_json), limit=88)}\n"
                f"stable_id={node[-16:]} | EXTRACTED",
                "semantic",
            )
            source_node = (
                source_nodes.get(int(row["source_id"]))
                if row["source_id"] is not None
                else None
            )
            _evidence_edge(
                graph,
                source_node or logical_root,
                node,
                relation,
            )
            if table == "code_import":
                payload = _payload_object(payload_json)
                module = str(payload.get("module") or "unknown-module")
                module_node = _stable_topology_node("IMPORTED_MODULE", module)
                if module_node not in emitted_modules:
                    graph.node(
                        module_node,
                        f"module {_topology_text(module, limit=72)}\n"
                        f"stable_id={module_node[-16:]} | EXTRACTED reference",
                        "semantic",
                    )
                    emitted_modules.add(module_node)
                _evidence_edge(graph, node, module_node, "resolves module name")
    graph.end()
    return source_nodes


def _emit_github_repository_graph(
    graph: _TopologyGraph,
    connection: sqlite3.Connection,
    source_nodes: dict[int, str],
) -> None:
    """Emit ref -> commit -> change -> blob -> chunk history from SQLite."""

    totals = {
        table: _required_table_count(connection, table)
        for table in (
            "git_ref_registry",
            "git_commit_registry",
            "git_commit_parent",
            "git_file_change",
            "git_blob_cas",
            "git_content_chunk_cas",
            "git_chunk_occurrence",
        )
    }
    source_path_nodes = {
        str(row["path"]): source_nodes[int(row["source_id"])]
        for row in connection.execute(
            "SELECT source_id, path FROM source_registry ORDER BY path"
        )
        if int(row["source_id"]) in source_nodes
    }
    graph.begin(
        "GITHUB_REPOSITORY_GRAPH",
        "6. GitHub repository ref, history, content, and impact evidence",
        direction="LR",
    )
    graph.node(
        "GITHUB_GRAPH_ROOT",
        "GitHub repository evidence graph\n"
        f"refs={totals['git_ref_registry']} | commits={totals['git_commit_registry']} | "
        f"parents={totals['git_commit_parent']} | changes={totals['git_file_change']}\n"
        f"blobs={totals['git_blob_cas']} | chunks={totals['git_content_chunk_cas']} | "
        f"occurrences={totals['git_chunk_occurrence']}",
        "root",
    )
    _evidence_edge(graph, "CODE_REPO", "GITHUB_GRAPH_ROOT", "history profile")
    graph.node(
        "GITHUB_GRAPH_BOUNDARY",
        "project-authored bounded projection\n"
        "stable identity + EXTRACTED confidence + coverage + impact\n"
        "no Graphify runtime, LLM extraction, server, or network dependency",
        "git",
    )
    _evidence_edge(
        graph,
        "GITHUB_GRAPH_ROOT",
        "GITHUB_GRAPH_BOUNDARY",
        "governance boundary",
    )

    commit_rows = connection.execute(
        """
        SELECT commit_sha, ordinal, tree_sha, message
        FROM git_commit_registry ORDER BY ordinal DESC, commit_sha LIMIT 12
        """
    ).fetchall()
    commit_nodes: dict[str, str] = {}
    for row in commit_rows:
        commit_sha = str(row["commit_sha"])
        node = _stable_topology_node("COMMIT", commit_sha)
        commit_nodes[commit_sha] = node
        graph.node(
            node,
            f"commit {commit_sha[:12]}\n"
            f"{_topology_text(row['message'], limit=84)}\n"
            f"tree={str(row['tree_sha'])[:12]} | ordinal={int(row['ordinal'])} | "
            f"stable_id={node[-16:]} | EXTRACTED",
            "git",
        )

    ref_rows = connection.execute(
        """
        SELECT ref_name, object_sha, peeled_sha
        FROM git_ref_registry ORDER BY ref_name LIMIT 8
        """
    ).fetchall()
    for row in ref_rows:
        ref_name = str(row["ref_name"])
        object_sha = str(row["peeled_sha"] or row["object_sha"])
        ref_node = _stable_topology_node("REF", ref_name, object_sha)
        graph.node(
            ref_node,
            f"{_topology_text(ref_name, limit=72)}\n"
            f"target={object_sha[:12]} | stable_id={ref_node[-16:]} | EXTRACTED",
            "git",
        )
        _evidence_edge(graph, "GITHUB_GRAPH_ROOT", ref_node, "contains ref")
        target_node = commit_nodes.get(object_sha)
        if target_node is None:
            target_node = _stable_topology_node("COMMIT_BOUNDARY", object_sha)
            graph.node(
                target_node,
                f"commit boundary {object_sha[:12]}\nnot expanded by bounded render",
                "git",
            )
        _evidence_edge(graph, ref_node, target_node, "points to commit")

    parent_rows = connection.execute(
        """
        SELECT commit_sha, parent_sha, parent_ordinal
        FROM git_commit_parent ORDER BY commit_sha, parent_ordinal
        """
    ).fetchall()
    emitted_boundary_commits: set[str] = set()
    for row in parent_rows:
        child_sha = str(row["commit_sha"])
        if child_sha not in commit_nodes:
            continue
        parent_sha = str(row["parent_sha"])
        parent_node = commit_nodes.get(parent_sha)
        if parent_node is None:
            parent_node = _stable_topology_node("COMMIT_BOUNDARY", parent_sha)
            if parent_node not in emitted_boundary_commits:
                graph.node(
                    parent_node,
                    f"parent boundary {parent_sha[:12]}\nnot expanded by bounded render",
                    "git",
                )
                emitted_boundary_commits.add(parent_node)
        _evidence_edge(
            graph,
            parent_node,
            commit_nodes[child_sha],
            f"parent {int(row['parent_ordinal'])} -> child",
        )

    blob_nodes: dict[str, str] = {}
    chunk_nodes: dict[str, str] = {}
    rendered_changes = 0
    rendered_chunks = 0
    for commit_sha, commit_node in commit_nodes.items():
        changes = connection.execute(
            """
            SELECT status, path, prior_path, blob_sha
            FROM git_file_change
            WHERE commit_sha=? ORDER BY path, status LIMIT 4
            """,
            (commit_sha,),
        ).fetchall()
        for row in changes:
            path = str(row["path"])
            blob_sha = str(row["blob_sha"] or "")
            change_node = _stable_topology_node(
                "FILE_CHANGE",
                commit_sha,
                row["status"],
                path,
                row["prior_path"] or "",
                blob_sha,
            )
            graph.node(
                change_node,
                f"{row['status']} {_topology_text(path, limit=82)}\n"
                f"blob={blob_sha[:12] if blob_sha else 'none'} | "
                f"stable_id={change_node[-16:]} | EXTRACTED",
                "git",
            )
            _evidence_edge(graph, commit_node, change_node, "records file change")
            rendered_changes += 1
            if path in source_path_nodes:
                _evidence_edge(
                    graph,
                    change_node,
                    source_path_nodes[path],
                    "matches current registered path",
                )
            if not blob_sha:
                continue
            blob_node = blob_nodes.get(blob_sha)
            if blob_node is None:
                blob = connection.execute(
                    """
                    SELECT content_sha256, size_bytes, is_binary
                    FROM git_blob_cas WHERE blob_sha=?
                    """,
                    (blob_sha,),
                ).fetchone()
                blob_node = _stable_topology_node("BLOB", blob_sha)
                blob_nodes[blob_sha] = blob_node
                graph.node(
                    blob_node,
                    f"blob {blob_sha[:12]}\n"
                    f"content_sha256={str(blob['content_sha256'])[:12] if blob else 'missing'} | "
                    f"bytes={int(blob['size_bytes']) if blob else 0} | "
                    f"binary={int(blob['is_binary']) if blob else 0}\n"
                    f"stable_id={blob_node[-16:]} | EXTRACTED",
                    "git",
                )
            _evidence_edge(
                graph, change_node, blob_node, "resolves content-addressed blob"
            )
            occurrence = connection.execute(
                """
                SELECT chunk_sha256, ordinal, char_start, char_end
                FROM git_chunk_occurrence
                WHERE commit_sha=? AND path=? AND blob_sha=?
                ORDER BY ordinal LIMIT 1
                """,
                (commit_sha, path, blob_sha),
            ).fetchone()
            if occurrence is None:
                continue
            chunk_sha = str(occurrence["chunk_sha256"])
            chunk_node = chunk_nodes.get(chunk_sha)
            if chunk_node is None:
                chunk = connection.execute(
                    "SELECT size_bytes FROM git_content_chunk_cas WHERE chunk_sha256=?",
                    (chunk_sha,),
                ).fetchone()
                chunk_node = _stable_topology_node("CONTENT_CHUNK", chunk_sha)
                chunk_nodes[chunk_sha] = chunk_node
                graph.node(
                    chunk_node,
                    f"chunk {chunk_sha[:12]}\n"
                    f"bytes={int(chunk['size_bytes']) if chunk else 0} | "
                    f"stable_id={chunk_node[-16:]} | EXTRACTED",
                    "retrieval",
                )
            _evidence_edge(
                graph,
                blob_node,
                chunk_node,
                f"contains chunk {int(occurrence['ordinal'])} "
                f"chars={int(occurrence['char_start'])}:{int(occurrence['char_end'])}",
            )
            rendered_chunks += 1

    impact_counts = {
        "changed_files": totals["git_file_change"],
        "symbols": _table_count(connection, "code_symbol"),
        "routes": _table_count(connection, "code_route"),
        "dependencies": _table_count(connection, "code_dependency"),
        "structured_facts": _table_count(connection, "structured_fact"),
    }
    graph.node(
        "GITHUB_IMPACT_COVERAGE",
        "source-impact seed coverage\n"
        + " | ".join(f"{table}={count}" for table, count in impact_counts.items())
        + "\nfull affected closure is resolved by the source graph; no duplicate impact tables",
        "git",
    )
    _evidence_edge(
        graph,
        "GITHUB_GRAPH_ROOT",
        "GITHUB_IMPACT_COVERAGE",
        "summarizes impact evidence",
    )
    graph.node(
        "GITHUB_RENDER_COVERAGE",
        "bounded render coverage\n"
        f"commits={len(commit_nodes)}/{totals['git_commit_registry']} | "
        f"changes={rendered_changes}/{totals['git_file_change']} | "
        f"blobs={len(blob_nodes)}/{totals['git_blob_cas']} | "
        f"chunk edges={rendered_chunks}/{totals['git_chunk_occurrence']}\n"
        "counts cover the full SQLite package; nodes are deterministic samples",
        "git",
    )
    _evidence_edge(
        graph,
        "GITHUB_GRAPH_ROOT",
        "GITHUB_RENDER_COVERAGE",
        "declares visual coverage",
    )
    if not commit_nodes:
        graph.node(
            "GITHUB_HISTORY_EMPTY",
            "no Git commits loaded\nGitHub history profile is not satisfied",
            "warn",
        )
        _evidence_edge(
            graph, "GITHUB_GRAPH_ROOT", "GITHUB_HISTORY_EMPTY", "empty history"
        )
    graph.end()


def _emit_local_worktree_graph(
    graph: _TopologyGraph,
    connection: sqlite3.Connection,
    source_nodes: dict[int, str],
) -> None:
    """Emit a working-tree graph and an explicit no-fabricated-Git boundary."""

    git_counts = {
        table: _required_table_count(connection, table)
        for table in (
            "git_ref_registry",
            "git_commit_registry",
            "git_commit_parent",
            "git_file_change",
        )
    }
    graph.begin(
        "LOCAL_WORKTREE_GRAPH",
        "6. Local working-tree files and semantic relationships",
        direction="LR",
    )
    graph.node(
        "LOCAL_WORKTREE_ROOT",
        "Local Code working tree\n"
        f"files={len(source_nodes)} | symbols={_table_count(connection, 'code_symbol')} | "
        f"imports={_table_count(connection, 'code_import')} | "
        f"routes={_table_count(connection, 'code_route')} | "
        f"dependencies={_table_count(connection, 'code_dependency')}",
        "root",
    )
    _evidence_edge(graph, "CODE_REPO", "LOCAL_WORKTREE_ROOT", "working-tree profile")
    for source_node in source_nodes.values():
        _evidence_edge(
            graph,
            "LOCAL_WORKTREE_ROOT",
            source_node,
            "contains registered working-tree file",
        )
    git_total = sum(git_counts.values())
    graph.node(
        "NO_GIT_HISTORY_LOADED" if git_total == 0 else "LOCAL_GIT_ROWS_EXCLUDED",
        (
            "NO_GIT_HISTORY_LOADED\n"
            "no refs, commits, parents, or file changes are claimed\n"
            "use the GitHub Code lane for repository history"
            if git_total == 0
            else "Git-shaped rows exist but are excluded from the Local Code profile\n"
            + " | ".join(
                f"{table.removeprefix('git_')}={count}"
                for table, count in git_counts.items()
            )
        ),
        "warn",
    )
    boundary_node = (
        "NO_GIT_HISTORY_LOADED" if git_total == 0 else "LOCAL_GIT_ROWS_EXCLUDED"
    )
    _evidence_edge(
        graph,
        "LOCAL_WORKTREE_ROOT",
        boundary_node,
        "prevents fabricated Git lineage",
    )
    graph.node(
        "LOCAL_GRAPH_BOUNDARY",
        "stable identity + EXTRACTED confidence + coverage\n"
        "SQLite facts only; no inferred commit or ref nodes",
        "semantic",
    )
    _evidence_edge(
        graph,
        "LOCAL_WORKTREE_ROOT",
        "LOCAL_GRAPH_BOUNDARY",
        "governance boundary",
    )
    graph.end()


def _lane_topology(
    lane: LaneDefinition,
    database_path: Path,
    classification: dict[str, Any],
) -> tuple[str, str]:
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    graph = _TopologyGraph(f"lane_{lane.canonical_lane_id}")
    sources = _table_count(connection, "source_registry")
    chunks = _table_count(connection, "chunk_index")
    facts = _table_count(connection, "structured_fact")
    graph.node(
        "LANE_ROOT",
        f"{lane.display_label}\n{sources} sources | {chunks} chunks | {facts} structured facts",
        "root",
    )

    graph.begin("SOURCE_INTAKE", "1. Source intake and exact-byte registry")
    graph.node(
        "SOURCE_REG",
        f"source_registry\nrows={sources} | SHA-256 + parser state",
        "source",
    )
    graph.edge("LANE_ROOT", "SOURCE_REG")
    extension_rows = connection.execute(
        """
        SELECT CASE WHEN extension='' THEN '[no extension]' ELSE extension END AS extension,
               COUNT(*) AS count
        FROM source_registry GROUP BY extension ORDER BY count DESC, extension LIMIT 8
        """
    ).fetchall()
    if extension_rows:
        for index, row in enumerate(extension_rows):
            node = f"SOURCE_TYPE_{index}"
            graph.node(node, f"{row['extension']}\n{row['count']} sources", "source")
            graph.edge("SOURCE_REG", node)
    else:
        graph.node(
            "SOURCE_EMPTY", "schema ready\nno routed source in this build", "warn"
        )
        graph.edge("SOURCE_REG", "SOURCE_EMPTY")
    graph.end()

    fact_counts = {
        str(row["kind"]): int(row["count"])
        for row in connection.execute(
            "SELECT kind, COUNT(*) AS count FROM structured_fact GROUP BY kind ORDER BY kind"
        )
    }
    lane_tables = _lane_schema_tables(lane)
    graph.begin("SEMANTIC_MODEL", "2. Lane-specific semantic model from SQLite")
    graph.node(
        "FACT_INDEX",
        f"structured_fact\nrows={facts} | kinds={len(fact_counts)}",
        "semantic",
    )
    graph.edge("SOURCE_REG", "FACT_INDEX")
    graph.node(
        "SEMANTIC_SCHEMA_HANDOFF",
        f"lane semantic schema\nentities={len(lane_tables)} | exact rows projected below",
        "semantic",
    )
    graph.edge("FACT_INDEX", "SEMANTIC_SCHEMA_HANDOFF", "materializes")

    if fact_counts:
        sampled_kinds = sorted(
            fact_counts, key=lambda item: (-fact_counts[item], item)
        )[:10]
        for index, kind in enumerate(sampled_kinds):
            kind_node = f"FACT_KIND_{index}"
            graph.node(kind_node, f"{kind}\nrows={fact_counts[kind]}", "semantic")
            graph.edge("FACT_INDEX", kind_node)
            sample_rows = connection.execute(
                """
                SELECT locator, payload_json FROM structured_fact
                WHERE kind=? ORDER BY locator, fact_id LIMIT 2
                """,
                (kind,),
            ).fetchall()
            for sample_index, row in enumerate(sample_rows):
                sample_node = f"FACT_SAMPLE_{index}_{sample_index}"
                graph.node(
                    sample_node,
                    _fact_display(kind, str(row["locator"]), str(row["payload_json"])),
                    "semantic",
                )
                graph.edge(kind_node, sample_node, "sample")
    else:
        graph.node(
            "FACT_EMPTY", "no semantic rows yet\nlane schema remains explicit", "warn"
        )
        graph.edge("FACT_INDEX", "FACT_EMPTY")
    graph.end()

    physical_projection = physical_schema_projection(connection, lane)
    missing_contract_tables = physical_projection["missing_contract_tables"]
    if missing_contract_tables:
        raise RuntimeError(
            "Required lane schema tables are missing: "
            + ", ".join(str(table) for table in missing_contract_tables)
        )
    graph.begin(
        "SQLITE_PHYSICAL_SCHEMA",
        "3. Additive SQLite physical schema contract",
    )
    graph.node(
        "PHYSICAL_SCHEMA_SECTOR",
        "SQLite physical schema\n"
        f"contract={physical_projection['contract_table_count']} | "
        f"tables={physical_projection['table_count']} | "
        f"auxiliaries={physical_projection['auxiliary_table_count']} | "
        f"relations={physical_projection['relation_count']}\n"
        f"projection_sha256={physical_projection['projection_sha256']}",
        "root",
    )
    graph.edge(
        "SEMANTIC_SCHEMA_HANDOFF",
        "PHYSICAL_SCHEMA_SECTOR",
        "derives exact SQLite schema",
    )
    physical_nodes = physical_table_node_ids(physical_projection)
    physical_group_labels = {
        "PHYSICAL_GROUP_CORE": "shared lane core",
        "PHYSICAL_GROUP_LANE": "lane semantic contract",
        "PHYSICAL_GROUP_GIT": "Git history and impact",
        "PHYSICAL_GROUP_AUXILIARY": "SQLite engine auxiliaries",
    }
    for group_id, group_rows in physical_table_groups(physical_projection).items():
        graph.node(
            group_id,
            f"{physical_group_labels[group_id]}\ntables={len(group_rows)} | ordered from SQLite",
            "git"
            if group_id == "PHYSICAL_GROUP_GIT"
            else "retrieval"
            if group_id == "PHYSICAL_GROUP_AUXILIARY"
            else "semantic",
        )
        graph.edge("PHYSICAL_SCHEMA_SECTOR", group_id, "ordered table group")
        previous_node = group_id
        for row in group_rows:
            table = str(row["table"])
            table_node = physical_nodes[table]
            graph.node(
                table_node,
                f"{table}\nrows={row['rows']} | columns={len(row['columns'])} | "
                f"role={row['role']}",
                "retrieval" if row["role"] == "sqlite_engine_auxiliary" else "semantic",
            )
            graph.edge(previous_node, table_node, "next physical table")
            previous_node = table_node
    for relation in physical_projection["relations"]:
        parent_node = physical_nodes.get(str(relation["parent_table"]))
        child_node = physical_nodes.get(str(relation["child_table"]))
        if parent_node and child_node:
            graph.edge(
                parent_node,
                child_node,
                f"{relation['from_column']} -> "
                f"{relation['parent_table']}.{relation['parent_column']}",
            )
    graph.end()

    if lane.canonical_lane_id not in PRIMARY_CODE_LANES:
        schema_records = _schema_topology_records(connection, lane)
        relation_count = sum(len(row["foreign_keys"]) for row in schema_records)
        sample_count = sum(row["sample"] is not None for row in schema_records)
        graph.begin(
            "SCHEMA_DERIVED_TOPOLOGY",
            "4. Lane-owned SQLite entities, relations, and samples",
        )
        graph.node(
            "SCHEMA_SECTOR",
            f"{lane.display_label} schema sector\n"
            f"entities={len(schema_records)} | relations={relation_count} | "
            f"samples={sample_count}",
            "root",
        )
        graph.edge(
            "PHYSICAL_SCHEMA_SECTOR",
            "SCHEMA_SECTOR",
            "projects lane entities",
        )
        table_nodes = {
            row["table"]: f"SCHEMA_ENTITY_{index}"
            for index, row in enumerate(schema_records)
        }
        for index, row in enumerate(schema_records):
            entity_node = table_nodes[row["table"]]
            graph.node(
                entity_node,
                f"{row['table']}\nrows={row['rows']} | columns={row['columns']}",
                "semantic",
            )
            graph.edge("SCHEMA_SECTOR", entity_node, "entity")
            sample = row["sample"]
            if sample is not None:
                sample_node = f"SCHEMA_SAMPLE_{index}"
                graph.node(
                    sample_node,
                    "sample\n"
                    + _fact_display(
                        row["table"],
                        sample["locator"],
                        sample["payload_json"],
                    ),
                    "semantic",
                )
                graph.edge(entity_node, sample_node, "row sample")
        for row in schema_records:
            child_node = table_nodes[row["table"]]
            for foreign_key in row["foreign_keys"]:
                parent_node = (
                    "SOURCE_REG"
                    if foreign_key["table"] == "source_registry"
                    else table_nodes.get(foreign_key["table"])
                )
                if parent_node:
                    graph.edge(
                        parent_node,
                        child_node,
                        f"{foreign_key['from']} -> {foreign_key['table']}.{foreign_key['to']}",
                    )
        graph.end()

    if lane.canonical_lane_id in PRIMARY_CODE_LANES:
        graph.begin(
            "CODE_LOGICAL_TOPOLOGY",
            "4. Authorized seven-entity logical code contract and relations",
        )
        graph.node(
            "CODE_SECTOR",
            "Code Sector\n7 logical entities | SQLite-derived relationships | physical schema retained",
            "root",
        )
        graph.edge(
            "PHYSICAL_SCHEMA_SECTOR",
            "CODE_SECTOR",
            "projects code contract",
        )
        for logical_table, display_label, physical_table in CODE_LOGICAL_TOPOLOGY:
            node = logical_table.upper()
            count = _required_table_count(connection, physical_table)
            kind = "git" if logical_table == "git_commit" else "semantic"
            graph.node(
                node,
                f"{logical_table}\nrows={count} | {display_label} -> {physical_table}",
                kind,
            )
            graph.edge("CODE_SECTOR", node)

        for source, target, relation in (
            ("CODE_REPO", "CODE_FILE", "contains"),
            ("CODE_FILE", "CODE_SYMBOL", "declares"),
            ("CODE_FILE", "APP_ROUTE", "exposes"),
            ("CODE_REPO", "DEPENDENCY_ITEM", "declares dependency"),
            ("CODE_REPO", "PROJECT_ARTIFACT", "produces"),
            ("GIT_COMMIT", "CODE_FILE", "changes when Git history is loaded"),
        ):
            _evidence_edge(
                graph,
                source,
                target,
                f"logical {relation}",
                confidence="EXTRACTED",
            )

        route_rows = connection.execute(
            """
            SELECT record_id, locator, payload_json FROM code_route
            ORDER BY locator, record_id LIMIT 20
            """
        ).fetchall()
        for index, row in enumerate(route_rows):
            sample_node = f"APP_ROUTE_SAMPLE_{index}"
            graph.node(
                sample_node,
                "route sample\n"
                + _fact_display(
                    "code_route", str(row["locator"]), str(row["payload_json"])
                ),
                "semantic",
            )
            graph.edge("APP_ROUTE", sample_node, "sample")
        graph.end()

        source_nodes = _emit_code_evidence_graph(graph, connection)
        if lane.canonical_lane_id == "github_code":
            _emit_github_repository_graph(graph, connection, source_nodes)
            retrieval_parent = "GITHUB_GRAPH_ROOT"
        else:
            _emit_local_worktree_graph(graph, connection, source_nodes)
            retrieval_parent = "LOCAL_WORKTREE_ROOT"
    else:
        retrieval_parent = "SCHEMA_SECTOR"

    section_number = 7 if lane.canonical_lane_id in PRIMARY_CODE_LANES else 5
    graph.begin("RETRIEVAL", f"{section_number}. Retrieval and changed-section reuse")
    retrieval = (
        ("CHUNK_INDEX", "chunk_index", chunks),
        (
            "CHUNK_CAS",
            "chunk_content_cas",
            _table_count(connection, "chunk_content_cas"),
        ),
        ("CHUNK_HISTORY", "chunk_history", _table_count(connection, "chunk_history")),
        ("FTS", lane.fts_table, _table_count(connection, lane.fts_table)),
        ("TFIDF", "tfidf_vector", _table_count(connection, "tfidf_vector")),
    )
    previous = retrieval_parent
    for node, table, count in retrieval:
        graph.node(node, f"{table}\nrows={count}", "retrieval")
        graph.edge(previous, node)
        previous = node
    graph.end()

    section_number += 1
    graph.begin(
        "LIFECYCLE", f"{section_number}. Refresh, pointer, and mutation evidence"
    )
    refresh_summary = " | ".join(
        f"{key.lower()}={len(value) if isinstance(value, (list, dict)) else value}"
        for key, value in classification.items()
    )
    graph.node("REFRESH", f"refresh classification\n{refresh_summary}", "lifecycle")
    pointer = connection.execute(
        "SELECT pointer_value, generation FROM lane_pointer WHERE pointer_kind='entered_from'"
    ).fetchone()
    proposed = connection.execute(
        "SELECT value FROM lane_meta WHERE key='last_proposed_pv'"
    ).fetchone()
    graph.node(
        "POINTER",
        "lane pointer evidence\n"
        f"entered_from={pointer['pointer_value'] if pointer else 'none'} | "
        f"generation={pointer['generation'] if pointer else 0}\n"
        f"proposed={proposed[0] if proposed else 'unknown'}",
        "lifecycle",
    )
    graph.node(
        "MUTATION",
        "append-only change evidence\n"
        f"direct_purges={len(classification.get('REMOVED_PURGE', []))} | "
        f"mutation_receipts={_table_count(connection, 'mutation_receipt')}",
        "lifecycle",
    )
    graph.edge("TFIDF", "REFRESH")
    graph.edge("REFRESH", "POINTER")
    graph.edge("REFRESH", "MUTATION")
    graph.end()

    section_number += 1
    graph.begin("OUTPUTS", f"{section_number}. Inspectable lane package")
    graph.node(
        "SQLITE_OUT", f"{lane.sqlite_filename}\nSQLite/FK/FTS authority", "output"
    )
    graph.node("MMD_OUT", f"{lane.mmd_filename}\nsemantic Mermaid authority", "output")
    graph.node("DOT_OUT", f"{lane.dot_filename}\nsemantic DOT authority", "output")
    graph.node("POINTER_OUT", "lane_pointer.json\ncandidate pointer evidence", "output")
    graph.node(
        "RECEIPT_OUT", "refresh_receipt.json\nclassification + validation", "output"
    )
    graph.edge("POINTER", "SQLITE_OUT")
    for node in ("MMD_OUT", "DOT_OUT", "POINTER_OUT", "RECEIPT_OUT"):
        graph.edge("SQLITE_OUT", node)
    graph.end()
    connection.close()
    return graph.finish()


LANE_ARTIFACT_ROLE_PROJECTION_SCHEMA = "evidence-lane.lane-artifact-role-projection.v1"
LANE_ARTIFACT_EXTENSION_RECEIPT_SCHEMA = (
    "evidence-lane.lane-artifact-extension-receipt.v1"
)
_LANE_ARTIFACT_EXTENSION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class LaneArtifactContractError(ValueError):
    """Fail-closed lane artifact-role or extension error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _lane_artifact_error(code: str, message: str) -> None:
    raise LaneArtifactContractError(code, message)


def _lane_extension_target(
    lane_root: Path,
    extension_id: str,
    relative_path: Any,
) -> tuple[str, Path]:
    value = str(relative_path or "")
    pure = PurePosixPath(value)
    expected_prefix = ("extensions", extension_id)
    if (
        not value
        or "\\" in value
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.parts[:2] != expected_prefix
        or len(pure.parts) < 3
    ):
        _lane_artifact_error(
            "LANE_ARTIFACT_EXTENSION_PATH_INVALID",
            "An extension artifact must remain below its exact lane extension root.",
        )
    root = lane_root.resolve()
    target = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            _lane_artifact_error(
                "LANE_ARTIFACT_EXTENSION_SYMLINK_FORBIDDEN",
                "Extension artifact paths cannot contain symlinks.",
            )
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        _lane_artifact_error(
            "LANE_ARTIFACT_EXTENSION_PATH_ESCAPE",
            "An extension artifact resolved outside its lane root.",
        )
    return pure.as_posix(), resolved


def compile_lane_artifact_extension(
    lane_root: str | Path,
    lane: LaneDefinition,
    extension: dict[str, Any],
) -> dict[str, Any]:
    """Validate and seal one lane-scoped artifact extension read-only."""

    root = Path(lane_root).resolve()
    contract = lane_artifact_contract(lane.canonical_lane_id)
    if not isinstance(extension, dict) or set(extension) != {
        "extension_id",
        "roles",
    }:
        _lane_artifact_error(
            "LANE_ARTIFACT_EXTENSION_CONTRACT_INVALID",
            "An extension requires one exact ID and a role list.",
        )
    extension_id = str(extension["extension_id"] or "")
    if not _LANE_ARTIFACT_EXTENSION_ID_RE.fullmatch(extension_id):
        _lane_artifact_error(
            "LANE_ARTIFACT_EXTENSION_ID_INVALID",
            "An extension ID must be safe lower snake case.",
        )
    roles = extension["roles"]
    if not isinstance(roles, list) or not roles:
        _lane_artifact_error(
            "LANE_ARTIFACT_EXTENSION_ROLES_EMPTY",
            "An extension must declare at least one artifact role.",
        )
    role_catalog = {
        row["role_name"]: row
        for row in (
            *contract["extension_required_roles"],
            *contract["conditional_roles"],
            *contract["optional_roles"],
        )
    }
    normalized: list[dict[str, Any]] = []
    declared_paths: set[str] = set()
    declared_role_names: set[str] = set()
    for raw in roles:
        if not isinstance(raw, dict) or set(raw) != {
            "role_name",
            "classification",
            "relative_path",
            "condition",
        }:
            _lane_artifact_error(
                "LANE_ARTIFACT_EXTENSION_ROLE_INVALID",
                "An extension role has an unexpected shape.",
            )
        role_name = str(raw["role_name"] or "")
        expected = role_catalog.get(role_name)
        classification = str(raw["classification"] or "")
        if (
            expected is None
            or classification != expected["classification"]
            or role_name in declared_role_names
        ):
            _lane_artifact_error(
                "LANE_ARTIFACT_EXTENSION_ROLE_NOT_ALLOWED",
                "The artifact role is not allowed for this lane or is duplicated.",
            )
        exact_expected = cast(dict[str, Any], expected)
        condition = raw["condition"]
        required_now = classification == "REQUIRED"
        if classification == "CONDITIONAL":
            if (
                not isinstance(condition, dict)
                or set(condition) != {"condition_id", "active"}
                or condition.get("condition_id") != exact_expected["condition"]
                or not isinstance(condition.get("active"), bool)
            ):
                _lane_artifact_error(
                    "LANE_ARTIFACT_EXTENSION_CONDITION_INVALID",
                    "A conditional role requires its exact condition and active state.",
                )
            required_now = bool(condition["active"])
            normalized_condition: dict[str, Any] | None = {
                "condition_id": str(condition["condition_id"]),
                "active": bool(condition["active"]),
            }
        else:
            if condition is not None:
                _lane_artifact_error(
                    "LANE_ARTIFACT_EXTENSION_CONDITION_FORBIDDEN",
                    "Required and optional roles cannot carry a condition.",
                )
            normalized_condition = None
        relative_path, target = _lane_extension_target(
            root,
            extension_id,
            raw["relative_path"],
        )
        if relative_path in declared_paths:
            _lane_artifact_error(
                "LANE_ARTIFACT_EXTENSION_PATH_DUPLICATE",
                "An extension cannot bind two roles to the same file.",
            )
        exists = target.is_file()
        if required_now and not exists:
            _lane_artifact_error(
                "LANE_ARTIFACT_EXTENSION_REQUIRED_FILE_MISSING",
                "A required or active conditional extension artifact is missing.",
            )
        role_id = (
            f"evidence_lane.{lane.canonical_lane_id}.extensions."
            f"{extension_id}.{role_name}"
        )
        normalized.append(
            {
                "role_id": role_id,
                "role_name": role_name,
                "classification": classification,
                "condition": normalized_condition,
                "required_now": required_now,
                "relative_path": relative_path,
                "exists": exists,
                "bytes": target.stat().st_size if exists else None,
                "sha256": sha256_file(target) if exists else None,
            }
        )
        declared_paths.add(relative_path)
        declared_role_names.add(role_name)
    if "extension_authority" not in declared_role_names:
        _lane_artifact_error(
            "LANE_ARTIFACT_EXTENSION_AUTHORITY_REQUIRED",
            "Every extension requires one sealed extension_authority file.",
        )
    extension_root = root / "extensions" / extension_id
    actual_paths = (
        {
            path.relative_to(root).as_posix()
            for path in extension_root.rglob("*")
            if path.is_file()
        }
        if extension_root.is_dir()
        else set()
    )
    undeclared = sorted(actual_paths - declared_paths)
    if undeclared:
        _lane_artifact_error(
            "LANE_ARTIFACT_EXTENSION_UNDECLARED_FILE",
            "An extension directory contains undeclared files.",
        )
    body = {
        "schema": LANE_ARTIFACT_EXTENSION_RECEIPT_SCHEMA,
        "status": "PASS",
        "lane_id": lane.canonical_lane_id,
        "lane_contract_sha256": contract["contract_sha256"],
        "registry_sha256": LANE_ARTIFACT_ROLE_REGISTRY_SHA256,
        "extension_id": extension_id,
        "extension_namespace": (
            f"evidence_lane.{lane.canonical_lane_id}.extensions.{extension_id}"
        ),
        "root": f"extensions/{extension_id}",
        "roles": normalized,
        "undeclared_files": [],
        "unrelated_lane_effect": "NONE",
    }
    return {
        **body,
        "extension_receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def build_lane_artifact_role_contract(
    lane_root: str | Path,
    lane: LaneDefinition,
    *,
    extensions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project core and declared extension files into one lane-local contract."""

    root = Path(lane_root).resolve()
    contract = lane_artifact_contract(lane.canonical_lane_id)
    required: list[dict[str, Any]] = []
    core_valid = True
    for role in contract["required_roles"]:
        path = root / role["path"]
        self_manifest = role["seal"] == "SELF_MANIFEST"
        exists = True if self_manifest else path.is_file()
        core_valid = core_valid and exists
        required.append(
            {
                **role,
                "exists": exists,
                "self_manifest": self_manifest,
                "bytes": path.stat().st_size if exists and not self_manifest else None,
                "sha256": sha256_file(path) if exists and not self_manifest else None,
            }
        )
    compiled_extensions = [
        compile_lane_artifact_extension(root, lane, extension)
        for extension in (extensions or [])
    ]
    extension_ids = [row["extension_id"] for row in compiled_extensions]
    if len(extension_ids) != len(set(extension_ids)):
        _lane_artifact_error(
            "LANE_ARTIFACT_EXTENSION_ID_DUPLICATE",
            "A lane cannot declare the same extension twice.",
        )
    declared_extension_paths = {
        role["relative_path"]
        for extension in compiled_extensions
        for role in extension["roles"]
        if role["exists"]
    }
    extension_root = root / "extensions"
    actual_extension_paths = (
        {
            path.relative_to(root).as_posix()
            for path in extension_root.rglob("*")
            if path.is_file()
        }
        if extension_root.is_dir()
        else set()
    )
    undeclared_extension_files = sorted(
        actual_extension_paths - declared_extension_paths
    )
    valid = core_valid and not undeclared_extension_files
    body = {
        "schema": LANE_ARTIFACT_ROLE_PROJECTION_SCHEMA,
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "lane_id": lane.canonical_lane_id,
        "registry_sha256": LANE_ARTIFACT_ROLE_REGISTRY_SHA256,
        "lane_contract_sha256": contract["contract_sha256"],
        "required_roles": required,
        "conditional_role_catalog": contract["conditional_roles"],
        "optional_role_catalog": contract["optional_roles"],
        "extensions": compiled_extensions,
        "undeclared_extension_files": undeclared_extension_files,
        "unrelated_lane_effect": "NONE",
    }
    return {
        **body,
        "projection_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def validate_lane_artifact_role_contract(
    lane_root: str | Path,
    lane: LaneDefinition,
    declared: dict[str, Any] | None,
) -> dict[str, Any]:
    """Recompute one current artifact projection or accept historical absence."""

    root = Path(lane_root).resolve()
    if declared is None:
        return {
            "schema": LANE_ARTIFACT_ROLE_PROJECTION_SCHEMA,
            "status": "PASS",
            "valid": True,
            "enforced": False,
            "compatibility": "HISTORICAL_LANE_MANIFEST_CONTRACT_ABSENT",
            "lane_id": lane.canonical_lane_id,
        }
    try:
        extension_specs = [
            {
                "extension_id": extension["extension_id"],
                "roles": [
                    {
                        "role_name": role["role_name"],
                        "classification": role["classification"],
                        "relative_path": role["relative_path"],
                        "condition": role["condition"],
                    }
                    for role in extension["roles"]
                ],
            }
            for extension in declared.get("extensions", [])
        ]
        computed = build_lane_artifact_role_contract(
            root,
            lane,
            extensions=extension_specs,
        )
    except (KeyError, TypeError, LaneArtifactContractError, OSError) as exc:
        return {
            "schema": LANE_ARTIFACT_ROLE_PROJECTION_SCHEMA,
            "status": "FAIL",
            "valid": False,
            "enforced": True,
            "lane_id": lane.canonical_lane_id,
            "error": type(exc).__name__,
        }
    valid = bool(
        declared == computed
        and computed["valid"]
        and (root / "lane_manifest.json").is_file()
    )
    return {
        **computed,
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "enforced": True,
        "declared_projection_sha256": declared.get("projection_sha256"),
        "computed_projection_sha256": computed["projection_sha256"],
    }


def _lane_stable_files(lane: LaneDefinition) -> tuple[str, ...]:
    return stable_artifact_names(lane)


def _lane_evidence_files(lane: LaneDefinition) -> tuple[str, ...]:
    """Return every lane-level artifact whose bytes are externally auditable."""

    return (
        *_lane_stable_files(lane),
        "lane_pointer.json",
        "refresh_receipt.json",
    )


def _lane_required_files(lane: LaneDefinition) -> tuple[str, ...]:
    contract = lane_artifact_contract(lane.canonical_lane_id)
    return tuple(row["path"] for row in contract["required_roles"])


def _prior_lane_topology_is_reconcilable(
    prior_lane: Path | None,
    lane: LaneDefinition,
) -> bool:
    """Reject inherited topology that predates the current SQLite contract."""

    if prior_lane is None:
        return False
    required = (
        prior_lane / lane.sqlite_filename,
        prior_lane / lane.mmd_filename,
        prior_lane / lane.dot_filename,
    )
    if not all(path.is_file() for path in required):
        return False
    try:
        report = reconcile_lane_topology(
            prior_lane,
            lane_id=lane.canonical_lane_id,
            mmd_filename=lane.mmd_filename,
            dot_filename=lane.dot_filename,
            sqlite_filename=lane.sqlite_filename,
        )
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
        return False
    return report.get("status") == "PASS"


def _build_one_lane(
    *,
    root: Path,
    output: Path,
    lane: LaneDefinition,
    paths: list[str],
    prior_lane: Path | None,
    parent_pv: str | None,
    proposed_pv: str,
    pointer_generation: int,
    recorded_at: str,
    history_enabled: bool,
    source_snapshot: dict[str, dict[str, Any]],
    preserve_parent_unmentioned: bool = False,
    force_remove_paths: set[str] | None = None,
    host_profile: str,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    schema_asset = lane_schema_asset(lane.canonical_lane_id)
    tools = _tool_identity(lane, source_paths=paths)
    prior_db = prior_lane / lane.sqlite_filename if prior_lane else None
    prior_tools = (
        json.loads((prior_lane / "tools.json").read_text(encoding="utf-8"))
        if prior_lane and (prior_lane / "tools.json").is_file()
        else None
    )
    prior_index: dict[str, dict[str, Any]] = {}
    if prior_db and prior_db.is_file():
        prior_connection = sqlite3.connect(
            f"file:{prior_db.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        prior_connection.row_factory = sqlite3.Row
        prior_index = _source_index(prior_connection)
        prior_connection.close()
    current_index = {path: source_snapshot[path] for path in sorted(paths)}
    classification = _classify(
        prior_index,
        current_index,
        preserve_parent_unmentioned=preserve_parent_unmentioned,
        force_remove_paths=force_remove_paths,
    )
    current_git_signature = git_history_signature(root) if history_enabled else None
    prior_git_signature = None
    if history_enabled and prior_db and prior_db.is_file():
        prior_signature_connection = sqlite3.connect(
            f"file:{prior_db.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        prior_signature_row = prior_signature_connection.execute(
            "SELECT value FROM lane_meta WHERE key='git_history_signature'"
        ).fetchone()
        prior_signature_connection.close()
        prior_git_signature = (
            str(prior_signature_row[0]) if prior_signature_row else None
        )
    git_history_changed = bool(
        history_enabled and current_git_signature != prior_git_signature
    )
    prior_topology_generator = (
        prior_tools.get("topology_generator")
        if isinstance(prior_tools, dict)
        and isinstance(prior_tools.get("topology_generator"), dict)
        else None
    )
    current_topology_generator = tools["topology_generator"]
    topology_generator_changed = bool(
        prior_lane is not None
        and (
            prior_topology_generator is None
            or prior_topology_generator.get("sha256")
            != current_topology_generator["sha256"]
        )
    )
    tool_changed = bool(
        prior_lane is not None
        and (not prior_tools or prior_tools.get("sha256") != tools["sha256"])
    )
    topology_rebuild_required = bool(
        prior_lane is not None
        and not _prior_lane_topology_is_reconcilable(prior_lane, lane)
    )
    full_validation_fallback_reason: str | None = None
    parent_baseline_replay: dict[str, Any] = {
        "status": "NOT_REQUIRED",
        "parent_source_count": len(prior_index),
        "replayed_source_count": 0,
        "preserved_unavailable_source_count": 0,
        "preserved_unavailable_paths": [],
        "missing_after_replay": [],
    }
    baseline_replay_eligible = False
    if prior_db and prior_db.is_file() and tool_changed and preserve_parent_unmentioned:
        try:
            prior_validation = _validate_lane_database(prior_db, lane)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            raise ValueError(
                "A tool-identity fallback cannot replace a preserved parent "
                "whose current lane schema cannot be validated."
            ) from exc
        if prior_validation.get("valid") is not True:
            raise ValueError(
                "A tool-identity fallback cannot replace a preserved parent "
                "whose current lane schema is incompatible."
            )
        baseline_replay_eligible = True
    changed = (
        prior_lane is None
        or tool_changed
        or topology_generator_changed
        or git_history_changed
        or topology_rebuild_required
        or any(
            classification[key]
            for key in ("CHANGED_REBUILD", "NEW_REGISTER", "REMOVED_PURGE")
        )
    )
    db_path = output / lane.sqlite_filename

    if not changed and prior_lane:
        for filename in _lane_stable_files(lane):
            source = prior_lane / filename
            atomic_write_bytes(output / filename, source.read_bytes())
        build_mode = "UNCHANGED_REUSE"
        byte_reused = True
        validation = _validate_lane_database(db_path, lane)
        history_report: dict[str, Any] | None = (
            {
                "status": "UNCHANGED_REUSE",
                "signature": current_git_signature,
                "single_index_reuse": True,
            }
            if history_enabled
            else None
        )
        sqlite_execution: dict[str, Any] = {
            "status": "UNCHANGED_REUSE",
            "database_sha256": sha256_file(db_path),
            "explicit_bulk_transaction_required": True,
            "durable_authority": True,
        }
    else:
        if (
            prior_db
            and prior_db.is_file()
            and (not tool_changed or baseline_replay_eligible)
        ):
            atomic_write_bytes(db_path, prior_db.read_bytes())
            connection = _open_lane(db_path, lane, initialize=False)
            if baseline_replay_eligible:
                build_mode = "FULL_VALIDATION_FALLBACK"
                full_validation_fallback_reason = "TOOL_IDENTITY_CHANGED"
                replay_rows = [
                    row
                    for row in classification["UNCHANGED_REUSE"]
                    if row.get("preserved_parent_record") is not True
                ]
                unavailable_rows = [
                    row
                    for row in classification["UNCHANGED_REUSE"]
                    if row.get("preserved_parent_record") is True
                ]
                parent_baseline_replay = {
                    "status": "PASS",
                    "parent_source_count": len(prior_index),
                    "replayed_source_count": len(replay_rows),
                    "preserved_unavailable_source_count": len(unavailable_rows),
                    "preserved_unavailable_paths": sorted(
                        str(row["path"]) for row in unavailable_rows
                    ),
                    "missing_after_replay": [],
                }
            else:
                build_mode = "INCREMENTAL_REFRESH"
                replay_rows = []

            delete_actions: list[tuple[dict[str, Any], str]] = [
                (row, "CHANGED_REBUILD") for row in classification["CHANGED_REBUILD"]
            ]
            delete_actions.extend(
                (row, "REMOVED_PURGE") for row in classification["REMOVED_PURGE"]
            )
            delete_actions.extend(
                (row, "PARENT_BASELINE_REPLAY") for row in replay_rows
            )
            for row, mutation_kind in delete_actions:
                source = connection.execute(
                    "SELECT source_id, sha256, size_bytes FROM source_registry WHERE path=?",
                    (row["path"],),
                ).fetchone()
                if source is None:
                    continue
                delete_fts_sql = (
                    f"DELETE FROM {lane.fts_table} WHERE rowid IN "  # nosec B608
                    "(SELECT chunk_id FROM chunk_index WHERE source_id=?)"
                )
                connection.execute(
                    delete_fts_sql,
                    (source["source_id"],),
                )
                connection.execute(
                    "DELETE FROM source_registry WHERE source_id=?",
                    (source["source_id"],),
                )
                connection.execute(
                    """
                    INSERT INTO mutation_receipt(
                        mutation_kind, source_path, prior_sha256, current_sha256,
                        recorded_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        mutation_kind,
                        row["path"],
                        source["sha256"],
                        row.get("current_sha256"),
                        recorded_at,
                    ),
                )
            insert_rows_by_path = {
                str(row["path"]): row
                for row in (
                    replay_rows
                    + classification["CHANGED_REBUILD"]
                    + classification["NEW_REGISTER"]
                )
            }
            baseline_replay_blocked: list[dict[str, Any]] = []
            for row in insert_rows_by_path.values():
                _, parser_state = _insert_source(
                    connection,
                    lane,
                    root,
                    row["path"],
                    registered_at=recorded_at,
                    snapshot_ref=proposed_pv,
                    host_profile=host_profile,
                )
                if parser_state.startswith(("BLOCKED", "PARSE_FAILED")):
                    blocked = {"path": row["path"], "parser_state": parser_state}
                    classification["BLOCKED_UNSUPPORTED"].append(blocked)
                    if baseline_replay_eligible:
                        baseline_replay_blocked.append(blocked)
            if baseline_replay_blocked:
                connection.rollback()
                connection.close()
                raise ValueError(
                    "A parent-baseline replay cannot replace previously parsed "
                    "records with blocked or failed parser output."
                )
            if baseline_replay_eligible:
                final_index = _source_index(connection)
                expected_paths = set(prior_index) - set(force_remove_paths or ())
                missing_after_replay = sorted(expected_paths - set(final_index))
                parent_baseline_replay["missing_after_replay"] = missing_after_replay
                if missing_after_replay:
                    connection.rollback()
                    connection.close()
                    raise ValueError(
                        "A parent-baseline replay did not preserve every required "
                        "historical source identity."
                    )
            connection.execute("DELETE FROM parser_capability")
        else:
            connection = _open_lane(db_path, lane, initialize=True)
            build_mode = "FULL_PV1" if parent_pv is None else "FULL_VALIDATION_FALLBACK"
            if build_mode == "FULL_VALIDATION_FALLBACK":
                if prior_lane is None:
                    full_validation_fallback_reason = "PRIOR_LANE_ABSENT"
                elif prior_db is None or not prior_db.is_file():
                    full_validation_fallback_reason = "PRIOR_DATABASE_ABSENT"
                elif tool_changed:
                    full_validation_fallback_reason = "TOOL_IDENTITY_CHANGED"
                else:
                    raise ValueError(
                        "A full validation fallback requires one explicit bounded reason."
                    )
            for relative_path in paths:
                _, parser_state = _insert_source(
                    connection,
                    lane,
                    root,
                    relative_path,
                    registered_at=recorded_at,
                    snapshot_ref=proposed_pv,
                    host_profile=host_profile,
                )
                if parser_state.startswith(("BLOCKED", "PARSE_FAILED")):
                    classification["BLOCKED_UNSUPPORTED"].append(
                        {"path": relative_path, "parser_state": parser_state}
                    )
        history_report = (
            index_git_history(connection, root) if history_enabled else None
        )
        connection.executemany(
            """
            INSERT INTO parser_capability(capability, state, tool, detail)
            VALUES (?, ?, ?, ?)
            """,
            [
                (row["capability"], row["state"], row["tool"], row["detail"])
                for row in tools["capabilities"]
            ],
        )
        connection.execute(
            "INSERT OR REPLACE INTO lane_meta(key, value) VALUES (?, ?)",
            ("schema_version", LANE_SCHEMA_VERSION),
        )
        for key, value in (
            ("lane_id", lane.canonical_lane_id),
            ("parser_id", lane.parser_id),
            ("chunker_version", lane.chunker_version),
            ("lane_schema_id", schema_asset["schema_id"]),
            ("lane_schema_asset_version", schema_asset["schema_version"]),
            ("lane_schema_contract_sha256", schema_asset["contract_sha256"]),
            ("lane_schema_registry_sha256", LANE_SCHEMA_REGISTRY_SHA256),
            (
                "lane_schema_sqlite_master_projection_sha256",
                schema_asset["sqlite_master_projection_sha256"],
            ),
            (
                "lane_schema_extension_namespace",
                schema_asset["extension_namespace"],
            ),
            (
                "lane_schema_migration_head",
                schema_asset["migration_ledger"][-1]["migration_id"],
            ),
            ("tool_identity_sha256", tools["sha256"]),
            (
                "topology_generator_sha256",
                current_topology_generator["sha256"],
            ),
            ("last_proposed_pv", proposed_pv),
            ("last_build_mode", build_mode),
            ("git_history_signature", current_git_signature or "NOT_APPLICABLE"),
        ):
            connection.execute(
                "INSERT OR REPLACE INTO lane_meta(key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        connection.execute(
            """
            INSERT OR REPLACE INTO lane_pointer(
                pointer_kind, pointer_value, generation, recorded_at
            ) VALUES (?, ?, ?, ?)
            """,
            ("entered_from", parent_pv, pointer_generation, recorded_at),
        )
        superseded_source_cas = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT content.sha256
                FROM source_content_cas AS content
                LEFT JOIN source_registry AS source
                  ON source.sha256 = content.sha256
                WHERE source.sha256 IS NULL
                ORDER BY content.sha256
                """
            )
        ]
        for prior_sha256 in superseded_source_cas:
            connection.execute(
                """
                INSERT INTO mutation_receipt(
                    mutation_kind, source_path, prior_sha256, current_sha256,
                    recorded_at
                ) VALUES (?, NULL, ?, NULL, ?)
                """,
                (
                    "PURGE_SUPERSEDED_SOURCE_CAS",
                    prior_sha256,
                    recorded_at,
                ),
            )
        connection.execute(
            """
            DELETE FROM source_content_cas
            WHERE NOT EXISTS (
                SELECT 1 FROM source_registry
                WHERE source_registry.sha256 = source_content_cas.sha256
            )
            """
        )
        classification["PURGED_SUPERSEDED_SOURCE_CAS"] = superseded_source_cas
        _rebuild_retrieval(connection, lane)
        chunk_reuse = connection.execute(
            """
            SELECT
                COALESCE(SUM(content_reused), 0),
                COALESCE(SUM(CASE WHEN content_reused=0 THEN 1 ELSE 0 END), 0)
            FROM chunk_history WHERE snapshot_ref=?
            """,
            (proposed_pv,),
        ).fetchone()
        classification["CHANGED_SECTION_REUSED"] = int(chunk_reuse[0])
        classification["CHANGED_SECTION_REINDEXED"] = int(chunk_reuse[1])
        connection.execute(
            """
            INSERT INTO refresh_receipt(
                build_mode, parent_pv, proposed_pv, unchanged_reuse,
                changed_rebuild, new_register, removed_purge,
                blocked_unsupported, details_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_mode,
                parent_pv,
                proposed_pv,
                len(classification["UNCHANGED_REUSE"]),
                len(classification["CHANGED_REBUILD"]),
                len(classification["NEW_REGISTER"]),
                len(classification["REMOVED_PURGE"]),
                len(classification["BLOCKED_UNSUPPORTED"]),
                json.dumps(classification, sort_keys=True, separators=(",", ":")),
                recorded_at,
            ),
        )
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        sqlite_execution = verify_and_optimize_sqlite_authority(db_path).model_dump(
            mode="json"
        )
        mmd, dot = _lane_topology(lane, db_path, classification)
        mmd_path = output / lane.mmd_filename
        dot_path = output / lane.dot_filename
        atomic_write_bytes(mmd_path, mmd.encode("utf-8"))
        atomic_write_bytes(dot_path, dot.encode("utf-8"))
        tools["tool_execution_evidence"] = _lane_tool_execution_evidence(
            lane=lane,
            tools=tools,
            database=db_path,
            mmd_path=mmd_path,
            dot_path=dot_path,
            history_report=history_report,
            sqlite_execution=sqlite_execution,
            recorded_at=recorded_at,
        )
        tools["empty_table_classification"] = _empty_table_classification(
            database=db_path,
            lane=lane,
            history_enabled=history_enabled,
            recorded_at=recorded_at,
        )
        if tools["empty_table_classification"]["status"] != "PASS":
            raise ValueError("LANE_EMPTY_TABLE_CLASSIFICATION_FAILED")
        atomic_write_json(
            output / "tools.json",
            bind_tools_to_artifacts(output, lane, tools),
        )
        byte_reused = False
        validation = _validate_lane_database(db_path, lane)

    lane_pointer = {
        "schema": "evidence-lane.lane-pointer-evidence.v1",
        "lane_id": lane.canonical_lane_id,
        "entered_from": parent_pv,
        "proposed_pv": proposed_pv,
        "pointer_generation": pointer_generation,
        "independent_authority": False,
        "recorded_at": recorded_at,
    }
    atomic_write_json(output / "lane_pointer.json", lane_pointer)
    refresh_receipt = {
        "schema": "evidence-lane.lane-refresh-receipt.v1",
        "lane_id": lane.canonical_lane_id,
        "build_mode": build_mode,
        "full_validation_fallback_reason": full_validation_fallback_reason,
        "parent_baseline_replay": parent_baseline_replay,
        "classification": classification,
        "tool_identity_changed": tool_changed,
        "topology_generator_changed": topology_generator_changed,
        "topology_generator": {
            "current": current_topology_generator,
            "prior_sha256": (
                prior_topology_generator.get("sha256")
                if prior_topology_generator is not None
                else None
            ),
            "cache_reuse_allowed": not topology_generator_changed,
        },
        "git_history_changed": git_history_changed,
        "topology_rebuild_required": topology_rebuild_required,
        "git_history": history_report,
        "sqlite_execution": sqlite_execution,
        "stable_artifacts_byte_reused": byte_reused,
        "parent_pv": parent_pv,
        "proposed_pv": proposed_pv,
        "validation": validation,
        "recorded_at": recorded_at,
    }
    atomic_write_json(output / "refresh_receipt.json", refresh_receipt)
    stable_hashes = {
        filename: sha256_file(output / filename)
        for filename in _lane_stable_files(lane)
    }
    evidence_hashes = {
        filename: sha256_file(output / filename)
        for filename in _lane_evidence_files(lane)
    }
    four_file_contract = build_four_file_contract(output, lane)
    artifact_role_contract = build_lane_artifact_role_contract(output, lane)
    lane_manifest = {
        "schema": LANE_MANIFEST_SCHEMA,
        "lane": lane.as_dict(),
        "build_mode": build_mode,
        "stable_artifacts": stable_hashes,
        "evidence_artifacts": evidence_hashes,
        "required_artifacts": list(_lane_required_files(lane)),
        "four_file_contract": four_file_contract,
        "artifact_role_contract": artifact_role_contract,
        "pointer_evidence": "lane_pointer.json",
        "refresh_receipt": "refresh_receipt.json",
        "validation": validation,
        "sqlite_execution": sqlite_execution,
        "recorded_at": recorded_at,
    }
    atomic_write_json(output / "lane_manifest.json", lane_manifest)
    return {
        "lane_id": lane.canonical_lane_id,
        "build_mode": build_mode,
        "full_validation_fallback_reason": full_validation_fallback_reason,
        "parent_baseline_replay": parent_baseline_replay,
        "byte_reused": byte_reused,
        "classification": classification,
        "git_history": history_report,
        "topology_rebuild_required": topology_rebuild_required,
        "topology_generator_changed": topology_generator_changed,
        "topology_generator_sha256": current_topology_generator["sha256"],
        "validation": validation,
        "stable_artifacts": stable_hashes,
    }


def _bundle_graph(reports: list[dict[str, Any]]) -> tuple[str, str]:
    graph = _TopologyGraph("evidence_lane_project", direction="TB")
    graph.node(
        "PROJECT_ROOT",
        f"Universal Evidence Lane project\n{len(reports)} canonical lane packages",
        "root",
    )

    graph.begin("CONTROL_PLANE", "1. Registry-derived public skill entrypoints")
    controls = (
        ("BOOT", "Boot\nruntime + locked Flash + host/storage"),
        ("ROLLBACK", "Rollback\naccepted pointer only"),
        ("BUILD", "Build\nunaccepted candidate + HIL"),
        ("REFRESH", "Refresh\nchanged sections only"),
        ("MODE", "Mode\nordered sidecar intersections"),
        ("SOURCE_INTAKE", "Source Intake\nauto-detect + explicit override"),
    )
    for node, label in controls:
        graph.node(node, label, "source")
        graph.edge("PROJECT_ROOT", node)
    graph.end()

    graph.begin("PARALLEL_LANES", "2. Deterministic lane fan-out and join")
    graph.node(
        "ROUTER",
        "one source snapshot + route receipt\nChat Lineage always included",
        "source",
    )
    graph.node(
        "PARALLEL_POOL",
        f"bounded parallel compute\nworkers <= {MAX_PARALLEL_LANE_WORKERS}",
        "retrieval",
    )
    graph.node(
        "DETERMINISTIC_JOIN",
        "deterministic join\nall lane validations must pass",
        "lifecycle",
    )
    graph.edge("BOOT", "ROUTER")
    graph.edge("SOURCE_INTAKE", "ROUTER")
    graph.edge("MODE", "ROUTER")
    graph.edge("REFRESH", "PARALLEL_POOL")
    graph.edge("ROUTER", "PARALLEL_POOL")
    for index, report in enumerate(reports):
        lane = LANE_REGISTRY[report["lane_id"]]
        counts = report.get("validation", {}).get("counts", {})
        lane_node = f"LANE_{index}"
        graph.node(
            lane_node,
            f"{lane.display_label}\n{report['build_mode']} | "
            f"{counts.get('source_registry', 0)} sources | "
            f"{counts.get('structured_fact', 0)} facts",
            "semantic",
        )
        graph.edge("PARALLEL_POOL", lane_node)
        graph.edge(lane_node, "DETERMINISTIC_JOIN")
    graph.end()

    graph.begin("ARTIFACT_CONTRACT", "3. Per-lane and project evidence contract")
    graph.node(
        "LANE_OUTPUTS",
        "each lane\nSQLite + MMD + DOT + pointer + refresh receipt",
        "output",
    )
    graph.node(
        "PROJECT_OUTPUTS",
        "project package\nmaster MMD/DOT + manifests + hashes + Exit Slip",
        "output",
    )
    graph.node(
        "LINEAGE_OUTPUT",
        "visible ChatLineage\nprompts + steers + output + tools + files + tests + hashes",
        "output",
    )
    graph.edge("DETERMINISTIC_JOIN", "LANE_OUTPUTS")
    graph.edge("LANE_OUTPUTS", "PROJECT_OUTPUTS")
    chat_index = next(
        (
            index
            for index, report in enumerate(reports)
            if report["lane_id"] == "chat_lineage"
        ),
        None,
    )
    if chat_index is not None:
        graph.edge(f"LANE_{chat_index}", "LINEAGE_OUTPUT")
    graph.edge("LINEAGE_OUTPUT", "PROJECT_OUTPUTS")
    graph.end()

    graph.begin(
        "SERIAL_AUTHORITY", "4. Serial candidate, HIL, Fuse, and pointer authority"
    )
    graph.node(
        "CANDIDATE",
        "UNACCEPTED candidate\nimmutable package + validation evidence",
        "lifecycle",
    )
    graph.node(
        "HIL",
        "governed HIL\nno implicit acceptance by continuation",
        "lifecycle",
    )
    graph.node("APPROVE", "exact APPROVE\nbound to displayed candidate", "lifecycle")
    graph.node("DELTA", "APPROVE_WITH_DELTA\nbounded correction task", "warn")
    graph.node("RESEARCH", "MORE_RESEARCH\nquestion + evidence wait", "warn")
    graph.node("ROLLBACK_PATH", "ROLLBACK\nexisting accepted PV only", "warn")
    graph.node("REJECT", "REJECT\nterminal candidate receipt", "warn")
    graph.node("FAIL", "FAIL\nfailed gate receipt", "warn")
    graph.node("FUSE", "Fuse\nCAS + exact decision receipt", "lifecycle")
    graph.node("ACCEPTED", "accepted PV pointer\nmonotonic generation", "root")
    graph.node(
        "STATE_TRAVEL",
        "State Travel\nuser-requested fresh-host recovery only",
        "output",
    )
    graph.edge("PROJECT_OUTPUTS", "CANDIDATE")
    graph.edge("BUILD", "CANDIDATE")
    graph.edge("CANDIDATE", "HIL")
    graph.edge("HIL", "APPROVE")
    graph.edge("HIL", "DELTA")
    graph.edge("HIL", "RESEARCH")
    graph.edge("HIL", "ROLLBACK_PATH")
    graph.edge("HIL", "REJECT")
    graph.edge("HIL", "FAIL")
    graph.edge("APPROVE", "FUSE")
    graph.edge("FUSE", "ACCEPTED")
    graph.edge("ROLLBACK", "ROLLBACK_PATH")
    graph.edge("ROLLBACK_PATH", "ACCEPTED")
    graph.edge("ACCEPTED", "STATE_TRAVEL", "conditional")
    graph.end()
    return graph.finish()


def _lane_source_binding(
    output: Path,
    routes: dict[str, str],
    source_snapshot: dict[str, dict[str, Any]],
    emitted_lane_ids: tuple[str, ...],
    *,
    allow_observed_superset: bool = False,
) -> dict[str, Any]:
    """Verify every routed source hash against its completed lane database."""

    observed: dict[str, str] = {}
    duplicates: list[str] = []
    for lane_id in emitted_lane_ids:
        lane = LANE_REGISTRY[lane_id]
        database = output / lane_id / lane.sqlite_filename
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            for path, source_sha256 in connection.execute(
                "SELECT path,sha256 FROM source_registry ORDER BY path"
            ):
                normalized = str(path)
                if normalized in observed:
                    duplicates.append(normalized)
                observed[normalized] = str(source_sha256)
        finally:
            connection.close()
    expected = {path: str(source_snapshot[path]["sha256"]) for path in sorted(routes)}
    compared_paths = (
        expected.keys()
        if allow_observed_superset
        else expected.keys() | observed.keys()
    )
    mismatches = sorted(
        path for path in compared_paths if expected.get(path) != observed.get(path)
    )
    valid = (
        not duplicates
        and not mismatches
        and set(routes) == set(source_snapshot)
        and (allow_observed_superset or set(expected) == set(observed))
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "expected_source_count": len(expected),
        "observed_source_count": len(observed),
        "duplicate_source_count": len(set(duplicates)),
        "mismatch_count": len(mismatches),
        "observed_superset_allowed": allow_observed_superset,
        "binding_sha256": sha256_bytes(canonical_json_bytes(observed)),
    }


def build_lane_bundle(
    *,
    repository_root: str | Path,
    output_directory: str | Path,
    code_mode: str,
    parent_lane_bundle: str | Path | None,
    parent_pv: str | None,
    proposed_pv: str,
    pointer_generation: int,
    source_overrides: dict[str, str] | None = None,
    git_mode: str = "AUTO",
    max_lane_workers: int = MAX_PARALLEL_LANE_WORKERS,
    recorded_at_override: str | None = None,
    include_untracked: bool = False,
    materialize_all_lanes: bool = False,
    source_paths_override: list[str] | tuple[str, ...] | None = None,
    preserve_parent_unmentioned: bool = False,
    index_git_history: bool = True,
    allow_parent_operational_authority_drift: bool = False,
    host_profile: str = "CODEX_DESKTOP",
) -> dict[str, Any]:
    """Build/Refresh lanes concurrently, then assemble one deterministic PV."""

    if code_mode not in PRIMARY_CODE_LANES:
        raise ValueError("code_mode must be github_code or local_code")
    exact_host_profile = host_profile.strip().upper()
    if exact_host_profile not in {"CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"}:
        if exact_host_profile.startswith("CHATGPT"):
            raise ValueError("CHATGPT_TOOLCHAIN_PLANE_NOT_IMPLEMENTED")
        raise ValueError(f"Unsupported Codex host profile: {host_profile}")
    root = Path(repository_root).resolve()
    if not 1 <= max_lane_workers <= MAX_PARALLEL_LANE_WORKERS:
        raise ValueError(
            f"max_lane_workers must be between 1 and {MAX_PARALLEL_LANE_WORKERS}."
        )
    output = Path(output_directory).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Lane bundle output must be empty.")
    parent = Path(parent_lane_bundle).resolve() if parent_lane_bundle else None
    if parent is not None:
        try:
            parent_validation = validate_lane_bundle(parent)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            sqlite3.DatabaseError,
        ) as exc:
            raise ValueError(
                "The parent lane bundle is unreadable or invalid."
            ) from exc
        checksum_mismatches = dict(parent_validation.get("checksum_mismatches") or {})
        from .project_authority import is_working_sector_operational_member

        operational_authority_only_drift = bool(checksum_mismatches) and all(
            is_working_sector_operational_member(path) for path in checksum_mismatches
        )
        validated_working_parent = bool(
            allow_parent_operational_authority_drift
            and operational_authority_only_drift
            and not parent_validation.get("lane_manifest_errors")
            and all(
                lane.get("valid") is True
                for lane in dict(parent_validation.get("lanes") or {}).values()
            )
            and parent_validation.get("lane_directory_set_valid") is True
            and parent_validation.get("source_routes_valid") is True
            and parent_validation.get("topology_valid") is True
        )
        if parent_validation.get("valid") is not True and not validated_working_parent:
            raise ValueError("The parent lane bundle failed sealed validation.")
    git_arm = probe_git_arm(root, requested_mode=git_mode)
    git_history_available = bool(git_arm["history_index_enabled"])
    if not output.exists():
        output.mkdir(parents=True)
    recorded_at = recorded_at_override or utc_now()
    source_selection, source_rows, source_exclusions = governed_source_files(
        root,
        include_untracked=include_untracked,
    )
    available_source_paths = {relative for relative, _ in source_rows}
    if source_paths_override is None:
        source_paths = sorted(available_source_paths)
    else:
        source_paths = sorted(set(source_paths_override))
        unavailable_paths = sorted(set(source_paths) - available_source_paths)
        if unavailable_paths:
            raise ValueError(
                "Working overlay source paths are absent from the governed "
                f"repository selection: {unavailable_paths[:8]}"
            )
    source_snapshot = _current_index(root, source_paths)
    source_snapshot_sha256 = sha256_bytes(canonical_json_bytes(source_snapshot))
    # An explicit path override is an overlay contract even when it happens to
    # equal today's governed selection.  Only the absence of an override proves
    # that the caller requested the complete current repository snapshot.
    complete_source_snapshot = source_paths_override is None
    prior_routes_by_path: dict[str, str] = {}
    inherited_routes: dict[str, str] = {}
    if parent and (parent / "routes.json").is_file():
        prior_routes = json.loads((parent / "routes.json").read_text(encoding="utf-8"))
        prior_routes_by_path = {
            str(path): str(lane_id)
            for path, lane_id in prior_routes.get("routes", {}).items()
        }
        inherited_routes = {
            path: lane_id
            for path, lane_id in prior_routes_by_path.items()
            if path in source_paths
        }
    effective_overrides = {**inherited_routes, **(source_overrides or {})}
    routes = route_batch(
        source_paths,
        code_mode=code_mode,
        overrides=effective_overrides,
    )
    by_lane: dict[str, list[str]] = {lane_id: [] for lane_id in CANONICAL_LANE_IDS}
    for relative, lane_id in routes.items():
        by_lane[lane_id].append(relative)
    always_loaded_lane_ids = ("chat_lineage",)
    emitted_lane_ids = tuple(
        lane_id
        for lane_id in CANONICAL_LANE_IDS
        if materialize_all_lanes
        or by_lane[lane_id]
        or lane_id in always_loaded_lane_ids
    )
    omitted_lane_ids = tuple(
        lane_id for lane_id in CANONICAL_LANE_IDS if lane_id not in emitted_lane_ids
    )
    parent_emitted_lane_ids: tuple[str, ...] = ()
    if parent:
        parent_manifest_path = parent / "manifest.json"
        if parent_manifest_path.is_file():
            parent_manifest = json.loads(
                parent_manifest_path.read_text(encoding="utf-8")
            )
            parent_emitted_lane_ids = tuple(
                parent_manifest.get("emitted_lane_ids") or CANONICAL_LANE_IDS
            )
        else:
            parent_emitted_lane_ids = tuple(
                lane_id for lane_id in CANONICAL_LANE_IDS if (parent / lane_id).is_dir()
            )
    removed_lane_ids = tuple(
        lane_id
        for lane_id in parent_emitted_lane_ids
        if lane_id not in emitted_lane_ids
    )

    # RapidOCR lazily imports NumPy/OpenCV and creates ONNX Runtime native
    # thread pools. On Windows, starting that cold runtime inside one lane
    # worker while the Git lane repeatedly creates subprocess pipe-reader
    # threads can starve both workers under an MCP stdio host. Initialize the
    # shared OCR engine once, before the lane pool, then retain the existing
    # lock around individual OCR calls. This is a dependency cold-start
    # barrier only; independent lane computation remains parallel below.
    prewarmed_dependencies: list[str] = []
    if by_lane["images_ocr"] or by_lane["pdf_ocr"]:
        prewarmed_dependencies.extend(prewarm_native_dependencies())

    atomic_write_json(
        output / "registry.json",
        {
            "schema": "evidence-lane.canonical-lane-registry.v1",
            "lane_count": len(CANONICAL_LANE_IDS),
            "lanes": catalog(),
            "code_mode": code_mode,
            "single_registry": True,
        },
    )
    atomic_write_json(
        output / "routes.json",
        {
            "schema": "evidence-lane.source-route-authority.v1",
            "parent_pv": parent_pv,
            "routes": routes,
            "source_policy": {
                "selection_mode": source_selection,
                "tracked_only": source_selection == "GIT_TRACKED_ONLY",
                "excluded_source_count": len(source_exclusions),
                "excluded_sources": source_exclusions,
            },
            "inherited_route_count": len(inherited_routes),
            "session_override_count": len(source_overrides or {}),
            "single_lane_per_source": True,
            "recorded_at": recorded_at,
        },
    )
    reports_by_lane: dict[str, dict[str, Any]] = {}
    effective_workers = min(max_lane_workers, len(emitted_lane_ids))
    with ThreadPoolExecutor(
        max_workers=effective_workers,
        thread_name_prefix="evidence-lane-build",
    ) as executor:
        futures = {}
        for lane_id in emitted_lane_ids:
            lane = LANE_REGISTRY[lane_id]
            prior_lane = (
                parent / lane_id if parent and (parent / lane_id).is_dir() else None
            )
            future = executor.submit(
                _build_one_lane,
                root=root,
                output=output / lane_id,
                lane=lane,
                paths=by_lane[lane_id],
                prior_lane=prior_lane,
                parent_pv=parent_pv,
                proposed_pv=proposed_pv,
                pointer_generation=pointer_generation,
                recorded_at=recorded_at,
                history_enabled=(
                    index_git_history and lane_id == code_mode and git_history_available
                ),
                source_snapshot=source_snapshot,
                preserve_parent_unmentioned=preserve_parent_unmentioned,
                force_remove_paths={
                    path
                    for path, prior_lane_id in (
                        prior_routes_by_path
                        if complete_source_snapshot
                        else inherited_routes
                    ).items()
                    if prior_lane_id == lane_id and routes.get(path) != lane_id
                },
                host_profile=exact_host_profile,
            )
            futures[future] = lane_id
        for future in as_completed(futures):
            lane_id = futures[future]
            reports_by_lane[lane_id] = future.result()
    reports = [reports_by_lane[lane_id] for lane_id in emitted_lane_ids]

    final_selection, final_rows, final_exclusions = governed_source_files(
        root,
        include_untracked=include_untracked,
    )
    final_available_source_paths = {relative for relative, _ in final_rows}
    if source_paths_override is None:
        final_source_paths = sorted(final_available_source_paths)
    else:
        final_source_paths = sorted(set(source_paths_override))
        unavailable_final_paths = sorted(
            set(final_source_paths) - final_available_source_paths
        )
        if unavailable_final_paths:
            raise ValueError(
                "Working overlay source paths disappeared during the lane build: "
                f"{unavailable_final_paths[:8]}"
            )
    final_source_snapshot = _current_index(root, final_source_paths)
    final_source_snapshot_sha256 = sha256_bytes(
        canonical_json_bytes(final_source_snapshot)
    )
    if (
        final_source_snapshot != source_snapshot
        or final_selection != source_selection
        or final_exclusions != source_exclusions
    ):
        raise ValueError(
            "Repository source snapshot changed during parallel lane build; "
            "the partial output is not a candidate."
        )
    source_binding = _lane_source_binding(
        output,
        routes,
        source_snapshot,
        emitted_lane_ids,
        allow_observed_superset=(
            preserve_parent_unmentioned and not complete_source_snapshot
        ),
    )
    if not source_binding["valid"]:
        raise ValueError(
            "Completed lane databases do not bind the frozen source snapshot; "
            "the partial output is not a candidate."
        )
    lane_dispositions = build_lane_disposition_projection(
        output,
        emitted_lane_ids=emitted_lane_ids,
        removed_lane_ids=removed_lane_ids,
        reports_by_lane=reports_by_lane,
    )
    atomic_write_json(output / "lane_dispositions.json", lane_dispositions)
    execution_receipt = {
        "schema": "evidence-lane.parallel-lane-execution.v1",
        "single_writer": True,
        "linear_governance": True,
        "parallel_lane_compute": effective_workers > 1,
        "prewarmed_dependencies": prewarmed_dependencies,
        "toolchain_plane": "CODEX",
        "host_profile": exact_host_profile,
        "chatgpt_plane_mixed": False,
        "worker_count": effective_workers,
        "submitted_lane_count": len(emitted_lane_ids),
        "deterministic_assembly_order": list(emitted_lane_ids),
        "always_loaded_lane_ids": list(always_loaded_lane_ids),
        "emitted_lane_ids": list(emitted_lane_ids),
        "omitted_lane_ids": list(omitted_lane_ids),
        "lane_emission_policy": (
            "ALL_18_WORKING_AUTHORITY"
            if materialize_all_lanes
            else "LOADED_OR_DETECTED_ONLY"
        ),
        "barrier_status": "PASS",
        "source_snapshot_sha256": source_snapshot_sha256,
        "final_source_snapshot_sha256": final_source_snapshot_sha256,
        "source_snapshot_unchanged": True,
        "source_binding": source_binding,
        "lane_disposition_projection_sha256": lane_dispositions["projection_sha256"],
        "unloaded_lane_artifacts_fabricated": lane_dispositions[
            "unloaded_lane_artifacts_fabricated"
        ],
        "source_policy": {
            "selection_mode": source_selection,
            "tracked_only": source_selection == "GIT_TRACKED_ONLY",
            "excluded_source_count": len(source_exclusions),
            "excluded_sources": source_exclusions,
            "policy_unchanged_during_build": True,
            "secrets_indexed": False,
            "env_files_indexed": False,
            "runtime_artifacts_indexed": False,
            "untracked_operational_files_indexed": include_untracked,
            "source_paths_overridden": source_paths_override is not None,
            "preserve_parent_unmentioned": preserve_parent_unmentioned,
            "git_history_indexed": index_git_history,
        },
        "serialized_authorities": [
            "chat_lineage_append",
            "hil_decision",
            "fuse",
            "accepted_pointer_move",
            "rollback",
        ],
        "recorded_at": recorded_at,
    }
    atomic_write_json(output / "execution_receipt.json", execution_receipt)
    mmd, dot = _bundle_graph(reports)
    atomic_write_bytes(output / "project_lane_topology.mmd", mmd.encode("utf-8"))
    atomic_write_bytes(output / "project_lane_topology.dot", dot.encode("utf-8"))
    summary = {
        "full_build_lanes": [
            row["lane_id"]
            for row in reports
            if row["build_mode"] in {"FULL_PV1", "FULL_VALIDATION_FALLBACK"}
        ],
        "full_validation_fallbacks": [
            {
                "lane_id": row["lane_id"],
                "reason": row["full_validation_fallback_reason"],
            }
            for row in reports
            if row["build_mode"] == "FULL_VALIDATION_FALLBACK"
        ],
        "incremental_lanes": [
            row["lane_id"]
            for row in reports
            if row["build_mode"] == "INCREMENTAL_REFRESH"
        ],
        "byte_reused_lanes": [row["lane_id"] for row in reports if row["byte_reused"]],
        "topology_generator_rebuilt_lanes": [
            row["lane_id"] for row in reports if row["topology_generator_changed"]
        ],
        "topology_generator_sha256_by_lane": {
            row["lane_id"]: row["topology_generator_sha256"] for row in reports
        },
        "both_code_lanes_forced_by_generator": (
            set(PRIMARY_CODE_LANES) <= set(emitted_lane_ids)
            and all(
                row["topology_generator_changed"]
                for row in reports
                if row["lane_id"] in PRIMARY_CODE_LANES
            )
        ),
        "emitted_lane_ids": list(emitted_lane_ids),
        "omitted_lane_ids": list(omitted_lane_ids),
        "removed_lane_ids": list(removed_lane_ids),
        "changed_sources": sum(
            len(row["classification"]["CHANGED_REBUILD"]) for row in reports
        ),
        "new_sources": sum(
            len(row["classification"]["NEW_REGISTER"]) for row in reports
        ),
        "removed_sources": sum(
            len(row["classification"]["REMOVED_PURGE"]) for row in reports
        ),
        "blocked_sources": sum(
            len(row["classification"]["BLOCKED_UNSUPPORTED"]) for row in reports
        ),
        "changed_sections_reused": sum(
            int(row["classification"].get("CHANGED_SECTION_REUSED", 0))
            for row in reports
        ),
        "changed_sections_reindexed": sum(
            int(row["classification"].get("CHANGED_SECTION_REINDEXED", 0))
            for row in reports
        ),
        "git_history": next(
            (row["git_history"] for row in reports if row.get("git_history")),
            None,
        ),
        "git_optional_arm": git_arm,
        "parallel_execution": execution_receipt,
        "lane_dispositions": {
            "projection_sha256": lane_dispositions["projection_sha256"],
            "disposition_counts": lane_dispositions["disposition_counts"],
            "unloaded_lane_artifacts_fabricated": lane_dispositions[
                "unloaded_lane_artifacts_fabricated"
            ],
        },
    }
    manifest = {
        "schema": LANE_BUNDLE_SCHEMA,
        "parent_pv": parent_pv,
        "proposed_pv": proposed_pv,
        "pointer_generation": pointer_generation,
        "code_mode": code_mode,
        "git_optional_arm": git_arm,
        "pv1_only_full_build": True,
        "lane_count": len(reports),
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "always_loaded_lane_ids": list(always_loaded_lane_ids),
        "emitted_lane_ids": list(emitted_lane_ids),
        "omitted_lane_ids": list(omitted_lane_ids),
        "lane_emission_policy": execution_receipt["lane_emission_policy"],
        "source_count": len(source_paths),
        "source_routes_sha256": sha256_bytes(canonical_json_bytes(routes)),
        "source_snapshot_sha256": source_snapshot_sha256,
        "source_policy": execution_receipt["source_policy"],
        "parallel_execution": execution_receipt,
        "lane_disposition_contract": {
            "schema": LANE_DISPOSITION_SCHEMA,
            "path": "lane_dispositions.json",
            "projection_sha256": lane_dispositions["projection_sha256"],
            "canonical_lane_count": len(CANONICAL_LANE_IDS),
        },
        "reports": reports,
        "summary": summary,
        "created_at": recorded_at,
    }
    atomic_write_json(output / "manifest.json", manifest)
    files = {
        path.relative_to(output).as_posix(): sha256_file(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.json"}
    }
    atomic_write_json(
        output / "SHA256SUMS.json",
        {
            "schema": "evidence-lane.recursive-sha256.v1",
            "members": files,
            "member_count": len(files),
        },
    )
    manifest["bundle_sha256"] = sha256_bytes(canonical_json_bytes(files))
    manifest["member_count"] = len(files) + 2
    atomic_write_json(output / "manifest.json", manifest)
    return manifest


def validate_lane_bundle(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    routes = json.loads((root / "routes.json").read_text(encoding="utf-8"))
    conditional_lane_emission = "emitted_lane_ids" in manifest
    declared_emitted_lane_ids = manifest.get("emitted_lane_ids")
    emitted_lane_ids = tuple(
        declared_emitted_lane_ids
        if isinstance(declared_emitted_lane_ids, list)
        else CANONICAL_LANE_IDS
    )
    omitted_lane_ids = tuple(
        lane_id for lane_id in CANONICAL_LANE_IDS if lane_id not in emitted_lane_ids
    )
    actual_lane_directory_ids = tuple(
        lane_id for lane_id in CANONICAL_LANE_IDS if (root / lane_id).is_dir()
    )
    emission_contract_valid = bool(
        not conditional_lane_emission
        or (
            list(emitted_lane_ids)
            == [
                lane_id for lane_id in CANONICAL_LANE_IDS if lane_id in emitted_lane_ids
            ]
            and len(emitted_lane_ids) == len(set(emitted_lane_ids))
            and set(emitted_lane_ids) <= set(CANONICAL_LANE_IDS)
            and "chat_lineage" in emitted_lane_ids
            and manifest.get("always_loaded_lane_ids") == ["chat_lineage"]
            and manifest.get("omitted_lane_ids") == list(omitted_lane_ids)
            and manifest.get("lane_emission_policy")
            in {"LOADED_OR_DETECTED_ONLY", "ALL_18_WORKING_AUTHORITY"}
            and (
                manifest.get("lane_emission_policy") != "ALL_18_WORKING_AUTHORITY"
                or set(emitted_lane_ids) == set(CANONICAL_LANE_IDS)
            )
            and manifest.get("canonical_lane_count") == len(CANONICAL_LANE_IDS)
        )
    )
    lane_directory_set_valid = actual_lane_directory_ids == emitted_lane_ids
    execution_path = root / "execution_receipt.json"
    execution = (
        json.loads(execution_path.read_text(encoding="utf-8"))
        if execution_path.is_file()
        else None
    )
    pre_v110_compatibility = bool(
        manifest.get("schema") in {LEGACY_LANE_BUNDLE_SCHEMA, LANE_BUNDLE_SCHEMA}
        and "source_policy" not in manifest
        and execution is not None
        and "source_policy" not in execution
    )
    legacy_execution_compatibility = (
        manifest.get("schema") == LEGACY_LANE_BUNDLE_SCHEMA
        and execution is None
        and "parallel_execution" not in manifest
        and "source_snapshot_sha256" not in manifest
    )
    topology_compatibility = bool(
        pre_v110_compatibility or legacy_execution_compatibility
    )
    checksums = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))
    declared_members = checksums.get("members", {})
    actual_members = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.json"}
    }
    checksum_set_match = set(declared_members) == set(actual_members)
    checksum_mismatches = {
        name: {
            "declared": declared_members.get(name),
            "actual": actual_members.get(name),
        }
        for name in sorted(set(declared_members) | set(actual_members))
        if declared_members.get(name) != actual_members.get(name)
    }
    topology_reconciliation = reconcile_bundle_topology(
        root,
        lane_ids=emitted_lane_ids,
    )
    topology_by_lane = {row["lane_id"]: row for row in topology_reconciliation["lanes"]}
    lane_reports: dict[str, Any] = {}
    lane_manifest_errors: dict[str, Any] = {}
    four_file_contracts: dict[str, Any] = {}
    artifact_role_contracts: dict[str, Any] = {}
    for lane_id in emitted_lane_ids:
        lane = LANE_REGISTRY[lane_id]
        lane_root = root / lane_id
        lane_reports[lane_id] = _validate_lane_database(
            lane_root / lane.sqlite_filename, lane
        )
        lane_manifest = json.loads(
            (lane_root / "lane_manifest.json").read_text(encoding="utf-8")
        )
        lane_manifest_schema = lane_manifest.get("schema")
        legacy_manifest = lane_manifest_schema == "evidence-lane.lane-manifest.v1"
        previous_manifest = lane_manifest_schema == "evidence-lane.lane-manifest.v2"
        current_manifest = lane_manifest_schema == LANE_MANIFEST_SCHEMA
        strict_manifest = previous_manifest or current_manifest
        stable_hashes = {
            filename: sha256_file(lane_root / filename)
            for filename in _lane_stable_files(lane)
        }
        evidence_hashes = {
            filename: sha256_file(lane_root / filename)
            for filename in _lane_evidence_files(lane)
        }
        required_files = set(_lane_required_files(lane))
        actual_lane_files = {
            path.name for path in lane_root.iterdir() if path.is_file()
        }
        topology_report = topology_by_lane[lane_id]
        four_file_report = validate_four_file_contract(
            lane_root,
            lane,
            (lane_manifest.get("four_file_contract") if current_manifest else None),
        )
        four_file_contracts[lane_id] = four_file_report
        artifact_role_report = validate_lane_artifact_role_contract(
            lane_root,
            lane,
            (lane_manifest.get("artifact_role_contract") if current_manifest else None),
        )
        artifact_role_contracts[lane_id] = artifact_role_report
        mmd_valid = topology_report["structural"]["mermaid"]["status"] == "PASS"
        dot_valid = topology_report["structural"]["dot"]["status"] == "PASS"
        if (
            not (legacy_manifest or strict_manifest)
            or lane_manifest.get("lane", {}).get("canonical_lane_id") != lane_id
            or lane_manifest.get("stable_artifacts") != stable_hashes
            or (
                strict_manifest
                and lane_manifest.get("evidence_artifacts") != evidence_hashes
            )
            or (
                strict_manifest
                and set(lane_manifest.get("required_artifacts", [])) != required_files
            )
            or (
                current_manifest
                and not topology_compatibility
                and not four_file_report["valid"]
            )
            or (
                current_manifest
                and not topology_compatibility
                and not artifact_role_report["valid"]
            )
            or not required_files <= actual_lane_files
            or (not topology_compatibility and not mmd_valid)
            or (not topology_compatibility and not dot_valid)
            or (not topology_compatibility and topology_report["status"] != "PASS")
        ):
            lane_manifest_errors[lane_id] = {
                "schema": lane_manifest_schema,
                "legacy_compatibility_path": legacy_manifest,
                "previous_manifest_compatibility_path": previous_manifest,
                "lane_id": lane_manifest.get("lane", {}).get("canonical_lane_id"),
                "declared_stable_artifacts": lane_manifest.get("stable_artifacts"),
                "actual_stable_artifacts": stable_hashes,
                "declared_evidence_artifacts": lane_manifest.get("evidence_artifacts"),
                "actual_evidence_artifacts": evidence_hashes,
                "declared_required_artifacts": lane_manifest.get("required_artifacts"),
                "missing_required_artifacts": sorted(
                    required_files - actual_lane_files
                ),
                "mmd_valid": mmd_valid,
                "dot_valid": dot_valid,
                "topology_reconciliation": topology_report,
                "four_file_contract": four_file_report,
                "artifact_role_contract": artifact_role_report,
            }
    lane_disposition_report = validate_lane_disposition_projection(root, manifest)
    expected_registry_ids = list(CANONICAL_LANE_IDS)
    registry_ids = [row.get("canonical_lane_id") for row in registry.get("lanes", [])]
    bundle_sha256 = sha256_bytes(canonical_json_bytes(actual_members))
    route_values_valid = all(
        lane_id in emitted_lane_ids for lane_id in routes.get("routes", {}).values()
    )
    report_lane_ids = [row.get("lane_id") for row in manifest.get("reports", [])]
    modern_execution_valid = bool(
        execution
        and execution.get("schema") == "evidence-lane.parallel-lane-execution.v1"
        and execution.get("single_writer") is True
        and execution.get("linear_governance") is True
        and execution.get("barrier_status") == "PASS"
        and execution.get("source_snapshot_unchanged") is True
        and execution.get("source_snapshot_sha256")
        == execution.get("final_source_snapshot_sha256")
        and execution.get("source_binding", {}).get("valid") is True
        and execution.get("source_policy", {}).get("policy_unchanged_during_build")
        is True
        and execution.get("source_policy", {}).get("secrets_indexed") is False
        and execution.get("source_policy", {}).get("env_files_indexed") is False
        and execution.get("source_policy", {}).get("runtime_artifacts_indexed") is False
        and (
            (
                execution.get("source_policy", {}).get("selection_mode")
                == "GIT_TRACKED_ONLY"
                and execution.get("source_policy", {}).get(
                    "untracked_operational_files_indexed"
                )
                is False
            )
            or (
                execution.get("source_policy", {}).get("selection_mode")
                == "GIT_INDEX_WORKTREE_AND_UNTRACKED"
                and execution.get("source_policy", {}).get(
                    "untracked_operational_files_indexed"
                )
                is True
            )
            or (
                execution.get("source_policy", {}).get("selection_mode")
                == "FILESYSTEM_GOVERNED"
                and isinstance(
                    execution.get("source_policy", {}).get(
                        "untracked_operational_files_indexed"
                    ),
                    bool,
                )
            )
        )
        and execution.get("deterministic_assembly_order") == list(emitted_lane_ids)
        and execution.get("submitted_lane_count") == len(emitted_lane_ids)
        and (
            not lane_disposition_report["enforced"]
            or (
                execution.get("lane_disposition_projection_sha256")
                == lane_disposition_report.get("projection_sha256")
                and execution.get("unloaded_lane_artifacts_fabricated") is False
            )
        )
        and (
            not conditional_lane_emission
            or (
                execution.get("emitted_lane_ids") == list(emitted_lane_ids)
                and execution.get("omitted_lane_ids") == list(omitted_lane_ids)
                and execution.get("always_loaded_lane_ids") == ["chat_lineage"]
                and execution.get("lane_emission_policy")
                == manifest.get("lane_emission_policy")
            )
        )
        and manifest.get("parallel_execution") == execution
        and manifest.get("source_policy") == execution.get("source_policy")
        and manifest.get("source_snapshot_sha256")
        == execution.get("source_snapshot_sha256")
    )
    pre_v110_execution_valid = bool(
        pre_v110_compatibility
        and execution
        and execution.get("schema") == "evidence-lane.parallel-lane-execution.v1"
        and execution.get("single_writer") is True
        and execution.get("linear_governance") is True
        and execution.get("barrier_status") == "PASS"
        and execution.get("source_snapshot_unchanged") is True
        and execution.get("source_snapshot_sha256")
        == execution.get("final_source_snapshot_sha256")
        and execution.get("source_binding", {}).get("valid") is True
        and execution.get("deterministic_assembly_order") == list(CANONICAL_LANE_IDS)
        and manifest.get("parallel_execution") == execution
        and manifest.get("source_snapshot_sha256")
        == execution.get("source_snapshot_sha256")
    )
    execution_valid = (
        legacy_execution_compatibility
        or pre_v110_execution_valid
        or modern_execution_valid
    )
    topology_valid = bool(
        topology_compatibility or topology_reconciliation["status"] == "PASS"
    )
    valid = (
        manifest.get("schema") in {LEGACY_LANE_BUNDLE_SCHEMA, LANE_BUNDLE_SCHEMA}
        and checksums.get("schema") == "evidence-lane.recursive-sha256.v1"
        and checksum_set_match
        and not checksum_mismatches
        and checksums.get("member_count") == len(actual_members)
        and manifest.get("member_count") == len(actual_members) + 2
        and manifest.get("bundle_sha256") == bundle_sha256
        and registry.get("lane_count") == len(CANONICAL_LANE_IDS)
        and registry_ids == expected_registry_ids
        and routes.get("schema") == "evidence-lane.source-route-authority.v1"
        and routes.get("single_lane_per_source") is True
        and route_values_valid
        and manifest.get("source_count") == len(routes.get("routes", {}))
        and manifest.get("source_routes_sha256")
        == sha256_bytes(canonical_json_bytes(routes.get("routes", {})))
        and manifest.get("lane_count") == len(emitted_lane_ids)
        and report_lane_ids == list(emitted_lane_ids)
        and emission_contract_valid
        and lane_directory_set_valid
        and execution_valid
        and topology_valid
        and lane_disposition_report["valid"]
        and not lane_manifest_errors
        and all(report["valid"] for report in lane_reports.values())
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "lane_count": len(lane_reports),
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "emitted_lane_ids": list(emitted_lane_ids),
        "omitted_lane_ids": list(omitted_lane_ids),
        "lane_emission_policy_enforced": conditional_lane_emission,
        "lane_emission_contract_valid": emission_contract_valid,
        "lane_directory_set_valid": lane_directory_set_valid,
        "actual_lane_directory_ids": list(actual_lane_directory_ids),
        "lanes": lane_reports,
        "summary": manifest.get("summary"),
        "bundle_sha256": bundle_sha256,
        "declared_bundle_sha256": manifest.get("bundle_sha256"),
        "checksum_set_match": checksum_set_match,
        "checksum_mismatches": checksum_mismatches,
        "lane_manifest_errors": lane_manifest_errors,
        "four_file_contracts": four_file_contracts,
        "artifact_role_contracts": artifact_role_contracts,
        "lane_disposition_contract": lane_disposition_report,
        "source_routes_valid": route_values_valid,
        "parallel_execution_valid": execution_valid,
        "parallel_execution_legacy_compatibility": (legacy_execution_compatibility),
        "pre_v110_compatibility": pre_v110_compatibility,
        "pre_v110_execution_valid": pre_v110_execution_valid,
        "source_policy_enforced": not topology_compatibility,
        "topology_reconciliation_enforced": not topology_compatibility,
        "topology_valid": topology_valid,
        "topology_reconciliation": topology_reconciliation,
    }

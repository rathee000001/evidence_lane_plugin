"""ENV/UOP-governed full toolchain selection for public actions and lanes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .hardware_acceleration import (
    HardwareAccelerationProbe,
    resolve_hardware_acceleration,
)
from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import CANONICAL_LANE_IDS

AI_TOOLCHAIN_SCHEMA = "evidence-lane.ai-toolchain-runtime.v1"

CODEX_HOST_PROFILES: tuple[str, ...] = (
    "CODEX_DESKTOP",
    "CODEX_CLI",
    "CODEX_VM",
)

FORBIDDEN_TOOLCHAIN_HOST_PROFILES: tuple[str, ...] = ()


def _duckdb_stage_inventory(
    *,
    tools: dict[str, dict[str, Any]],
    action_rows: list[tuple[Any, ...]],
    lane_rows: list[tuple[Any, ...]],
    host_rows: list[tuple[Any, ...]],
) -> dict[str, Any]:
    """Stage and reconcile generated authority rows without owning them."""

    import duckdb  # type: ignore[import-not-found]

    connection = duckdb.connect(":memory:")
    try:
        connection.execute("CREATE TABLE tools(tool_id VARCHAR, requirement VARCHAR)")
        connection.executemany(
            "INSERT INTO tools VALUES(?,?)",
            [(tool_id, row["requirement"]) for tool_id, row in sorted(tools.items())],
        )
        connection.execute(
            "CREATE TABLE actions(action_name VARCHAR, action_class VARCHAR)"
        )
        connection.executemany(
            "INSERT INTO actions VALUES(?,?)",
            [(str(row[0]), str(row[1])) for row in action_rows],
        )
        connection.execute("CREATE TABLE lanes(lane_id VARCHAR)")
        connection.executemany(
            "INSERT INTO lanes VALUES(?)", [(str(row[0]),) for row in lane_rows]
        )
        connection.execute("CREATE TABLE hosts(host_profile VARCHAR, plane VARCHAR)")
        connection.executemany(
            "INSERT INTO hosts VALUES(?,?)",
            [(str(row[0]), str(row[1])) for row in host_rows],
        )

        def count(query: str) -> int:
            row = connection.execute(query).fetchone()
            if row is None:
                raise RuntimeError("DUCKDB_STAGING_COUNT_UNAVAILABLE")
            return int(row[0])

        counts = {
            "tool_count": count("SELECT COUNT(*) FROM tools"),
            "action_count": count("SELECT COUNT(*) FROM actions"),
            "lane_count": count("SELECT COUNT(*) FROM lanes"),
            "host_count": count("SELECT COUNT(*) FROM hosts"),
            "distinct_tool_count": count("SELECT COUNT(DISTINCT tool_id) FROM tools"),
            "distinct_action_count": count(
                "SELECT COUNT(DISTINCT action_name) FROM actions"
            ),
            "distinct_lane_count": count("SELECT COUNT(DISTINCT lane_id) FROM lanes"),
        }
    finally:
        connection.close()
    core = {
        "schema": "evidence-lane.env-uop-duckdb-staging.v1",
        "status": "PASS",
        "duckdb_version": str(duckdb.__version__),
        **counts,
        "persistent_authority": False,
        "sqlite_remains_authority": True,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


ACTION_CLASS_TOOL_ORDER: dict[str, tuple[str, ...]] = {
    "GOVERNANCE": (
        "ENV_UOP_classifier",
        "PCM_MBA_operators",
        "Pydantic",
        "SQLite_CAS",
        "Hash_chain_writer",
        "OpenAI_Agents_SDK",
    ),
    "SOURCE_ROUTING": (
        "hashlib_pathlib",
        "Secret_redactor",
        "Safe_archive_intake",
        "Project_inventory",
        "Git_detector",
        "Compatibility_mapper",
        "Custom_schema_compiler",
        "Citation_binder",
    ),
    "RETRIEVAL": (
        "LangChain",
        "LlamaIndex_SQLite_indexer",
        "APSW_SQLite_engine",
        "SQLite_FTS5_BM25",
        "sqlite_vec",
        "rank_bm25",
        "RapidFuzz",
        "SentenceTransformers",
        "FAISS_CPU",
        "Pinecone",
        "Weaviate",
        "Milvus",
        "OpenSearch",
        "deterministic_TFIDF",
        "OpenAI_Agents_SDK",
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
        "GitHub_MCP_Server",
        "Filesystem_MCP_Server",
        "OpenAI_Agents_SDK",
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
        "OpenAI_Agents_SDK",
    ),
    "OCR_MEDIA": (
        "RapidOCR_ONNX_Runtime",
        "pytesseract_Tesseract",
        "OpenCV",
        "Pillow",
        "Poppler_pdftotext_pdfinfo",
        "Ghostscript",
        "FFmpeg",
        "OpenAI_Agents_SDK",
    ),
    "DATA": (
        "DuckDB",
        "Polars",
        "APSW_SQLite_engine",
        "pandas",
        "pyarrow",
        "openpyxl",
        "python_calamine",
        "Tableau_Hyper_API",
        "SQLAlchemy",
        "OpenXML_CSV_JSON_parser",
        "Custom_schema_compiler",
        "SQLite_immutable_URI_reader",
        "Compatibility_mapper",
        "PostgreSQL_MCP_Server",
        "OpenAI_Agents_SDK",
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
        "OpenAI_Agents_SDK",
    ),
    "GRAPH": (
        "LangChain",
        "LangGraph_Mermaid_engine",
        "rustworkx",
        "Python_Graphviz_DOT_engine",
        "Graphviz_dot",
        "Mermaid_CLI_mmdc",
        "OpenAI_Agents_SDK",
    ),
    "RUNTIME_API": (
        "Python",
        "FastAPI",
        "Uvicorn",
        "FastMCP",
        "MCP_Python_SDK",
        "Pydantic",
        "Pydantic_Settings",
        "python_multipart",
        "aiofiles",
        "orjson",
        "Tenacity",
        "psutil",
        "PowerShell_Win32_APIs",
        "Cryptography_PyJWT",
        "SevenZip_NSIS_extractor",
        "python_dotenv",
        "HuggingFace_Hub_ModelSnapshot",
        "GitHub_MCP_Server",
        "Filesystem_MCP_Server",
        "PostgreSQL_MCP_Server",
        "Slack_MCP_Server",
        "OpenAI_Agents_SDK",
    ),
    "MCP_COMPOSITION": (
        "FastMCP",
        "MCP_Python_SDK",
    ),
    "EVALUATION": (
        "LangSmith",
        "TruLens",
        "DeepEval",
        "Promptfoo",
    ),
    "OBSERVABILITY": (
        "OpenTelemetry",
        "Langfuse",
        "Helicone",
        "Grafana",
    ),
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

ORCHESTRATION_ACTION_CLASS_ORDER: tuple[str, ...] = (
    "GOVERNANCE",
    "SOURCE_ROUTING",
    "CODE",
    "DOCUMENT",
    "OCR_MEDIA",
    "DATA",
    "WEB_RESEARCH",
    "RETRIEVAL",
    "GRAPH",
    "RUNTIME_API",
    "MCP_COMPOSITION",
    "EVALUATION",
    "OBSERVABILITY",
    "DEPLOYMENT",
)

LANE_ACTION_CLASSES: dict[str, tuple[str, ...]] = {
    "github_code": (
        "CODE",
        "RETRIEVAL",
        "GRAPH",
        "EVALUATION",
        "DEPLOYMENT",
        "OBSERVABILITY",
    ),
    "local_code": (
        "CODE",
        "RETRIEVAL",
        "GRAPH",
        "EVALUATION",
        "DEPLOYMENT",
        "OBSERVABILITY",
    ),
    "docs": ("DOCUMENT", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "pdf_ocr": ("DOCUMENT", "OCR_MEDIA", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "images_ocr": ("OCR_MEDIA", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "ppt": ("DOCUMENT", "OCR_MEDIA", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "data_excel": ("DATA", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "research": ("WEB_RESEARCH", "RETRIEVAL", "GRAPH", "EVALUATION", "OBSERVABILITY"),
    "brain_loader": ("DOCUMENT", "DATA", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "sqlite_brain": ("DATA", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "project_engulf": (
        "CODE",
        "DOCUMENT",
        "DATA",
        "WEB_RESEARCH",
        "RETRIEVAL",
        "GRAPH",
        "EVALUATION",
        "OBSERVABILITY",
    ),
    "artifacts": (
        "DOCUMENT",
        "OCR_MEDIA",
        "DATA",
        "RETRIEVAL",
        "GRAPH",
        "DEPLOYMENT",
        "OBSERVABILITY",
    ),
    "analysis": (
        "DATA",
        "RETRIEVAL",
        "GRAPH",
        "GOVERNANCE",
        "EVALUATION",
        "OBSERVABILITY",
    ),
    "discussion": ("RETRIEVAL", "GOVERNANCE", "OBSERVABILITY"),
    "plan": ("GOVERNANCE", "RETRIEVAL", "GRAPH", "EVALUATION", "OBSERVABILITY"),
    "mode": ("GOVERNANCE", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "chat_lineage": ("GOVERNANCE", "RETRIEVAL", "GRAPH", "OBSERVABILITY"),
    "custom": (
        "GOVERNANCE",
        "DATA",
        "RETRIEVAL",
        "GRAPH",
        "EVALUATION",
        "OBSERVABILITY",
    ),
}

# Every sector intake begins with the same bounded source-routing phase before
# its lane-specific parser, retrieval, graph, evaluation, or delivery phases.
LANE_ACTION_CLASSES = {
    lane_id: (*classes, "SOURCE_ROUTING")
    for lane_id, classes in LANE_ACTION_CLASSES.items()
}

_ALL_LANE_SURFACES = frozenset(
    {
        "all_18_project_sectors",
        "all_structured_project_sectors",
        "all_graph_surfaces",
        "all_queryable_authorities",
        "every_queryable_project_authority",
        "every_source_policy",
        "every_sqlite_authority",
        "all_workflows",
    }
)


def _tool_applies_to_lane(tool: dict[str, Any], lane_id: str) -> bool:
    surfaces = {str(value) for value in tool.get("surfaces") or []}
    if surfaces.intersection(_ALL_LANE_SURFACES):
        return True
    explicit = surfaces.intersection(CANONICAL_LANE_IDS)
    if explicit:
        return lane_id in explicit
    return False


def _runtime_tool_matrix() -> dict[str, dict[str, Any]]:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "toolchains" / "tool-requirement-matrix.v1.json"
        if candidate.is_file():
            value = json.loads(candidate.read_text(encoding="utf-8"))
            return {str(row["tool"]): dict(row) for row in value["requirements"]}
    raise RuntimeError("AI_TOOLCHAIN_MATRIX_UNAVAILABLE")


def _action_class(tool: dict[str, Any]) -> str:
    route = dict(tool.get("route_contract") or {})
    text = " ".join(
        str(value).lower()
        for value in (
            tool.get("name"),
            tool.get("title"),
            tool.get("description"),
            route.get("owner_skill"),
            route.get("route"),
        )
    )
    if any(term in text for term in ("git", "code", "repository", "worktree")):
        return "CODE"
    if any(term in text for term in ("render", "topology", "graph", "overlay")):
        return "GRAPH"
    if any(term in text for term in ("source", "intake", "research", "web")):
        return "WEB_RESEARCH"
    if any(
        term in text
        for term in (
            "memory",
            "learning",
            "query",
            "search",
            "fetch",
            "universe",
            "canon",
        )
    ):
        return "RETRIEVAL"
    if any(
        term in text
        for term in ("plugin", "storage", "runtime", "session", "tunnel", "boot")
    ):
        return "RUNTIME_API"
    return "GOVERNANCE"


def resolve_lane_toolchain(
    *,
    lane_id: str,
    host_profile: str,
    available_tools: set[str] | None = None,
    accelerator_profile: str = "cpu",
    enabled_accelerator_plugins: list[str] | tuple[str, ...] = (),
    accelerator_memory_budget_percent: int = 80,
    accelerator_temperature_limit_c: int | None = None,
    accelerator_probe: HardwareAccelerationProbe | None = None,
) -> dict[str, Any]:
    """Resolve one conditional lane toolchain for a Codex host profile.

    Tool presence never means every tool runs.  The returned ordering is a
    governed candidate chain; the lane executor selects only the tools needed
    by the current source/action and records unavailable fallbacks explicitly.
    """

    exact_lane = lane_id.strip().lower()
    if exact_lane not in LANE_ACTION_CLASSES:
        raise ValueError(f"Unknown canonical lane: {lane_id}")
    exact_host = host_profile.strip().upper()
    if exact_host not in CODEX_HOST_PROFILES:
        if exact_host in FORBIDDEN_TOOLCHAIN_HOST_PROFILES:
            raise ValueError("CHATGPT_TOOLCHAIN_PLANE_NOT_IMPLEMENTED")
        raise ValueError(f"Unsupported Codex host profile: {host_profile}")
    lane_action_classes = set(LANE_ACTION_CLASSES[exact_lane])
    lane_action_classes.update({"GOVERNANCE", "SOURCE_ROUTING"})
    action_classes = tuple(
        action_class
        for action_class in ORCHESTRATION_ACTION_CLASS_ORDER
        if action_class in lane_action_classes
    )
    matrix = _runtime_tool_matrix()
    ordered = list(
        dict.fromkeys(
            tool
            for action_class in action_classes
            for tool in ACTION_CLASS_TOOL_ORDER[action_class]
            if _tool_applies_to_lane(matrix[tool], exact_lane)
        )
    )
    runtime_inventory: dict[str, Any] | None = None
    runtime_rows: dict[str, dict[str, Any]] = {}
    if available_tools is None:
        from .runtime_toolchain import inspect_runtime_toolchain

        runtime_inventory = inspect_runtime_toolchain(prewarm_native=False)
        runtime_rows = {
            str(row["tool"]): dict(row)
            for row in runtime_inventory.get("results") or []
        }
        available_states = {
            "ACTIVE",
            "REPOSITORY_ONLY_AVAILABLE",
            "BUILD_GATE_NOT_RUNTIME_REQUIRED",
            "DECLARED_COMPONENT",
        }
        available = {
            tool
            for tool in ordered
            if runtime_rows.get(tool, {}).get("status") == "PASS"
            and str(runtime_rows.get(tool, {}).get("state") or "")
            in available_states
        }
    else:
        available = set(available_tools)
    runnable = [tool for tool in ordered if tool in available]
    unavailable = [tool for tool in ordered if tool not in available]
    required_runtime_unavailable = [
        tool
        for tool in ordered
        if runtime_rows.get(tool, {}).get("startup_required") is True
        and runtime_rows.get(tool, {}).get("status") != "PASS"
    ]
    hardware_acceleration = resolve_hardware_acceleration(
        action_classes=list(action_classes),
        requested_profile=accelerator_profile,
        enabled_vendor_plugins=list(enabled_accelerator_plugins),
        memory_budget_percent=accelerator_memory_budget_percent,
        temperature_limit_c=accelerator_temperature_limit_c,
        probe=accelerator_probe,
    )
    core = {
        "schema": "evidence-lane.ai-toolchain-lane-resolution.v1",
        "status": (
            "BLOCKED_REQUIRED_RUNTIME_TOOL_UNAVAILABLE"
            if required_runtime_unavailable
            else "PASS"
            if runnable
            else "BLOCKED_NO_RUNNABLE_TOOL"
        ),
        "plane": "CODEX",
        "host_profile": exact_host,
        "lane_id": exact_lane,
        "action_classes": list(action_classes),
        "ordered_tools": ordered,
        "runnable_tools": runnable,
        "unavailable_tools": unavailable,
        "required_runtime_unavailable_tools": required_runtime_unavailable,
        "availability_source": (
            "MEASURED_RUNTIME_TOOLCHAIN"
            if runtime_inventory is not None
            else "CALLER_SUPPLIED_EXACT_TOOL_SET"
        ),
        "runtime_toolchain_receipt_sha256": (
            runtime_inventory.get("receipt_sha256")
            if runtime_inventory is not None
            else None
        ),
        "runtime_tool_states": {
            tool: str(runtime_rows.get(tool, {}).get("state") or "UNMEASURED")
            for tool in ordered
        },
        "hardware_acceleration": hardware_acceleration,
        "conditional_execution": True,
        "run_every_tool": False,
        "hidden_runtime_required": True,
        "workspace_install_allowed": False,
        "chatgpt_plane_mixed": False,
    }
    return {**core, "resolution_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "ACTION_CLASS_TOOL_ORDER",
    "AI_TOOLCHAIN_SCHEMA",
    "CODEX_HOST_PROFILES",
    "FORBIDDEN_TOOLCHAIN_HOST_PROFILES",
    "LANE_ACTION_CLASSES",
    "ORCHESTRATION_ACTION_CLASS_ORDER",
    "resolve_lane_toolchain",
]

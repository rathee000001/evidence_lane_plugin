"""ENV/UOP-governed full toolchain selection for public actions and lanes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import CANONICAL_LANE_IDS
from .sqlite_indexing import rebuild_connection_authority_index
from .timeutil import utc_now

AI_TOOLCHAIN_SCHEMA = "evidence-lane.ai-toolchain-runtime.v1"

CODEX_HOST_PROFILES: tuple[str, ...] = (
    "CODEX_DESKTOP",
    "CODEX_CLI",
    "CODEX_VM",
)

FORBIDDEN_TOOLCHAIN_HOST_PROFILES: tuple[str, ...] = (
    "CHATGPT",
    "CHATGPT_DESKTOP",
    "CHATGPT_WORK",
)


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
        connection.execute(
            "CREATE TABLE tools(tool_id VARCHAR, requirement VARCHAR)"
        )
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
        counts = {
            "tool_count": int(connection.execute("SELECT COUNT(*) FROM tools").fetchone()[0]),
            "action_count": int(connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0]),
            "lane_count": int(connection.execute("SELECT COUNT(*) FROM lanes").fetchone()[0]),
            "host_count": int(connection.execute("SELECT COUNT(*) FROM hosts").fetchone()[0]),
            "distinct_tool_count": int(connection.execute("SELECT COUNT(DISTINCT tool_id) FROM tools").fetchone()[0]),
            "distinct_action_count": int(connection.execute("SELECT COUNT(DISTINCT action_name) FROM actions").fetchone()[0]),
            "distinct_lane_count": int(connection.execute("SELECT COUNT(DISTINCT lane_id) FROM lanes").fetchone()[0]),
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
    ),
    "RETRIEVAL": (
        "LlamaIndex_SQLite_indexer",
        "APSW_SQLite_engine",
        "SQLite_FTS5_BM25",
        "sqlite_vec",
        "rank_bm25",
        "RapidFuzz",
        "SentenceTransformers",
        "FAISS_CPU",
        "ChromaDB",
        "deterministic_TFIDF",
    ),
    "CODE": (
        "Git",
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
    ),
    "OCR_MEDIA": (
        "RapidOCR_ONNX_Runtime",
        "pytesseract_Tesseract",
        "OpenCV",
        "Pillow",
        "Poppler_pdftotext_pdfinfo",
        "Ghostscript",
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
        "Tableau_Hyper_API",
        "SQLAlchemy",
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
        "LangGraph_Mermaid_engine",
        "rustworkx",
        "Python_Graphviz_DOT_engine",
        "Graphviz_dot",
        "Mermaid_CLI_mmdc",
    ),
    "RUNTIME_API": (
        "FastAPI",
        "Uvicorn",
        "MCP_Python_SDK",
        "Pydantic",
        "Pydantic_Settings",
        "python_multipart",
        "aiofiles",
        "orjson",
        "Tenacity",
        "psutil",
        "PowerShell_Win32_APIs",
    ),
}

LANE_ACTION_CLASSES: dict[str, tuple[str, ...]] = {
    "github_code": ("CODE", "RETRIEVAL", "GRAPH"),
    "local_code": ("CODE", "RETRIEVAL", "GRAPH"),
    "docs": ("DOCUMENT", "RETRIEVAL", "GRAPH"),
    "pdf_ocr": ("DOCUMENT", "OCR_MEDIA", "RETRIEVAL", "GRAPH"),
    "images_ocr": ("OCR_MEDIA", "RETRIEVAL", "GRAPH"),
    "ppt": ("DOCUMENT", "OCR_MEDIA", "RETRIEVAL", "GRAPH"),
    "data_excel": ("DATA", "RETRIEVAL", "GRAPH"),
    "research": ("WEB_RESEARCH", "RETRIEVAL", "GRAPH"),
    "brain_loader": ("DOCUMENT", "DATA", "RETRIEVAL", "GRAPH"),
    "sqlite_brain": ("DATA", "RETRIEVAL", "GRAPH"),
    "project_engulf": (
        "CODE",
        "DOCUMENT",
        "DATA",
        "WEB_RESEARCH",
        "RETRIEVAL",
        "GRAPH",
    ),
    "artifacts": ("DOCUMENT", "OCR_MEDIA", "DATA", "RETRIEVAL", "GRAPH"),
    "analysis": ("DATA", "RETRIEVAL", "GRAPH", "GOVERNANCE"),
    "discussion": ("RETRIEVAL", "GOVERNANCE"),
    "plan": ("GOVERNANCE", "RETRIEVAL", "GRAPH"),
    "mode": ("GOVERNANCE", "RETRIEVAL", "GRAPH"),
    "chat_lineage": ("GOVERNANCE", "RETRIEVAL", "GRAPH"),
    "custom": ("GOVERNANCE", "DATA", "RETRIEVAL", "GRAPH"),
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
    if any(term in text for term in ("memory", "learning", "query", "search", "fetch", "universe", "canon")):
        return "RETRIEVAL"
    if any(term in text for term in ("plugin", "storage", "runtime", "session", "tunnel", "boot")):
        return "RUNTIME_API"
    return "GOVERNANCE"


def _schema(connection: sqlite3.Connection, *, uop: bool) -> None:
    if uop:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS uop_toolchain_policy_v16(
                action_class TEXT PRIMARY KEY,
                selection_rule TEXT NOT NULL,
                fallback_rule TEXT NOT NULL,
                can_override_env INTEGER NOT NULL CHECK(can_override_env=0),
                can_override_project INTEGER NOT NULL CHECK(can_override_project=0),
                status TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS uop_toolchain_host_policy_v16(
                host_profile TEXT PRIMARY KEY,
                plane TEXT NOT NULL,
                execution_allowed INTEGER NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL
            ) STRICT;
            """
        )
        return
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_toolchain_registry_v16(
            tool_id TEXT PRIMARY KEY,
            requirement TEXT NOT NULL,
            surfaces_json TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS ai_toolchain_action_binding_v16(
            action_name TEXT PRIMARY KEY,
            action_class TEXT NOT NULL,
            owner_skill TEXT,
            primary_tool TEXT NOT NULL,
            fallback_tools_json TEXT NOT NULL,
            ordered_tools_json TEXT NOT NULL,
            schema_sha256 TEXT NOT NULL,
            binding_sha256 TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS ai_toolchain_lane_binding_v16(
            lane_id TEXT PRIMARY KEY,
            action_classes_json TEXT NOT NULL,
            ordered_tools_json TEXT NOT NULL,
            binding_sha256 TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS ai_toolchain_host_binding_v16(
            host_profile TEXT PRIMARY KEY,
            plane TEXT NOT NULL,
            execution_allowed INTEGER NOT NULL,
            hidden_runtime_required INTEGER NOT NULL,
            workspace_install_allowed INTEGER NOT NULL,
            binding_sha256 TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS ai_toolchain_sync_receipt_v16(
            sequence INTEGER PRIMARY KEY,
            tool_count INTEGER NOT NULL,
            action_count INTEGER NOT NULL,
            lane_count INTEGER NOT NULL,
            tool_matrix_sha256 TEXT NOT NULL,
            public_catalog_sha256 TEXT NOT NULL,
            prior_receipt_sha256 TEXT,
            receipt_json TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL UNIQUE,
            recorded_at TEXT NOT NULL
        ) STRICT;
        """
    )


def sync_ai_toolchain_authority(
    *,
    env_database: str | Path,
    uop_database: str | Path,
    tool_matrix_path: str | Path,
    public_catalog_path: str | Path,
) -> dict[str, Any]:
    matrix_path = Path(tool_matrix_path).resolve()
    catalog_path = Path(public_catalog_path).resolve()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_actions = catalog.get("tools")
    if not isinstance(catalog_actions, list):
        raise TypeError(
            "AI toolchain synchronization requires the canonical public-action "
            "catalog with an iterable tools collection; the compact runtime count "
            "projection is not execution authority."
        )
    declared_action_count = int(catalog.get("tool_count", len(catalog_actions)))
    if declared_action_count != len(catalog_actions):
        raise ValueError(
            "Canonical public-action catalog count does not match its tools collection."
        )
    tools = {str(row["tool"]): dict(row) for row in matrix["requirements"]}
    unknown = sorted(
        {
            tool
            for ordered in ACTION_CLASS_TOOL_ORDER.values()
            for tool in ordered
            if tool not in tools
        }
    )
    if unknown:
        raise ValueError(f"AI toolchain binding references undeclared tools: {unknown}")
    env = sqlite3.connect(Path(env_database).resolve(), timeout=30)
    env.row_factory = sqlite3.Row
    uop = sqlite3.connect(Path(uop_database).resolve(), timeout=30)
    uop.row_factory = sqlite3.Row
    try:
        _schema(env, uop=False)
        _schema(uop, uop=True)
        env.execute("DELETE FROM ai_toolchain_registry_v16")
        env.execute("DELETE FROM ai_toolchain_action_binding_v16")
        env.execute("DELETE FROM ai_toolchain_lane_binding_v16")
        env.execute("DELETE FROM ai_toolchain_host_binding_v16")
        env.executemany(
            "INSERT INTO ai_toolchain_registry_v16 VALUES(?,?,?,?,?)",
            [
                (
                    tool_id,
                    row["requirement"],
                    canonical_json_bytes(row["surfaces"]).decode("utf-8"),
                    row["role"],
                    "ACTIVE_DECLARED",
                )
                for tool_id, row in sorted(tools.items())
            ],
        )
        action_rows = []
        for action in catalog_actions:
            action_name = str(action["name"])
            action_class = _action_class(action)
            ordered = list(ACTION_CLASS_TOOL_ORDER[action_class])
            body = {
                "action_name": action_name,
                "action_class": action_class,
                "owner_skill": dict(action.get("route_contract") or {}).get(
                    "owner_skill"
                ),
                "primary_tool": ordered[0],
                "fallback_tools": ordered[1:],
                "ordered_tools": ordered,
                "schema_sha256": action["schema_sha256"],
            }
            action_rows.append(
                (
                    action_name,
                    action_class,
                    body["owner_skill"],
                    ordered[0],
                    canonical_json_bytes(ordered[1:]).decode("utf-8"),
                    canonical_json_bytes(ordered).decode("utf-8"),
                    action["schema_sha256"],
                    sha256_bytes(canonical_json_bytes(body)),
                    "ACTIVE",
                )
            )
        env.executemany(
            "INSERT INTO ai_toolchain_action_binding_v16 VALUES(?,?,?,?,?,?,?,?,?)",
            action_rows,
        )
        lane_rows = []
        for lane_id in CANONICAL_LANE_IDS:
            classes = LANE_ACTION_CLASSES[lane_id]
            ordered = list(
                dict.fromkeys(
                    tool
                    for action_class in classes
                    for tool in ACTION_CLASS_TOOL_ORDER[action_class]
                    if _tool_applies_to_lane(tools[tool], lane_id)
                )
            )
            body = {
                "lane_id": lane_id,
                "action_classes": list(classes),
                "ordered_tools": ordered,
            }
            lane_rows.append(
                (
                    lane_id,
                    canonical_json_bytes(list(classes)).decode("utf-8"),
                    canonical_json_bytes(ordered).decode("utf-8"),
                    sha256_bytes(canonical_json_bytes(body)),
                    "ACTIVE",
                )
            )
        env.executemany(
            "INSERT INTO ai_toolchain_lane_binding_v16 VALUES(?,?,?,?,?)",
            lane_rows,
        )
        host_rows = []
        for host_profile in CODEX_HOST_PROFILES:
            body = {
                "host_profile": host_profile,
                "plane": "CODEX",
                "execution_allowed": True,
                "hidden_runtime_required": True,
                "workspace_install_allowed": False,
            }
            host_rows.append(
                (
                    host_profile,
                    "CODEX",
                    1,
                    1,
                    0,
                    sha256_bytes(canonical_json_bytes(body)),
                    "ACTIVE",
                )
            )
        for host_profile in FORBIDDEN_TOOLCHAIN_HOST_PROFILES:
            body = {
                "host_profile": host_profile,
                "plane": "CHATGPT",
                "execution_allowed": False,
                "hidden_runtime_required": False,
                "workspace_install_allowed": False,
            }
            host_rows.append(
                (
                    host_profile,
                    "CHATGPT",
                    0,
                    0,
                    0,
                    sha256_bytes(canonical_json_bytes(body)),
                    "SEPARATE_PLANE_NOT_IMPLEMENTED",
                )
            )
        env.executemany(
            "INSERT INTO ai_toolchain_host_binding_v16 VALUES(?,?,?,?,?,?,?)",
            host_rows,
        )
        duckdb_staging = _duckdb_stage_inventory(
            tools=tools,
            action_rows=action_rows,
            lane_rows=lane_rows,
            host_rows=host_rows,
        )
        uop.execute("DELETE FROM uop_toolchain_policy_v16")
        uop.executemany(
            "INSERT INTO uop_toolchain_policy_v16 VALUES(?,?,?,?,?,?)",
            [
                (
                    action_class,
                    "ENV_ACTION_CLASS_PRIMARY_THEN_ORDERED_AVAILABLE_FALLBACK",
                    "NO_CROSS_CLASS_OR_SILENT_FALLBACK",
                    0,
                    0,
                    "ACTIVE",
                )
                for action_class in ACTION_CLASS_TOOL_ORDER
            ],
        )
        uop.execute("DELETE FROM uop_toolchain_host_policy_v16")
        uop.executemany(
            "INSERT INTO uop_toolchain_host_policy_v16 VALUES(?,?,?,?,?)",
            [
                (
                    host_profile,
                    "CODEX",
                    1,
                    "RUN_CONDITIONALLY_FROM_HIDDEN_CODEX_PLUGIN_RUNTIME",
                    "ACTIVE",
                )
                for host_profile in CODEX_HOST_PROFILES
            ]
            + [
                (
                    host_profile,
                    "CHATGPT",
                    0,
                    "SEPARATE_CHATGPT_PLANE_DEFERRED_BY_USER",
                    "BLOCKED_SEPARATE_PLANE",
                )
                for host_profile in FORBIDDEN_TOOLCHAIN_HOST_PROFILES
            ],
        )
        env_index_receipt = rebuild_connection_authority_index(
            env, authority_id="env"
        )
        uop_index_receipt = rebuild_connection_authority_index(
            uop, authority_id="uop"
        )
        prior = env.execute(
            "SELECT receipt_sha256 FROM ai_toolchain_sync_receipt_v16 "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(
            env.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM ai_toolchain_sync_receipt_v16"
            ).fetchone()[0]
        )
        core = {
            "schema": AI_TOOLCHAIN_SCHEMA,
            "status": "PASS",
            "sequence": sequence,
            "tool_count": len(tools),
            "action_count": len(action_rows),
            "lane_count": len(lane_rows),
            "action_class_count": len(ACTION_CLASS_TOOL_ORDER),
            "codex_host_profiles": list(CODEX_HOST_PROFILES),
            "chatgpt_plane_mixed": False,
            "tool_matrix_sha256": sha256_bytes(matrix_path.read_bytes()),
            "public_catalog_sha256": sha256_bytes(catalog_path.read_bytes()),
            "prior_receipt_sha256": str(prior[0]) if prior else None,
            "sqlite_is_authority": True,
            "uop_can_override_env": False,
            "uop_can_override_project": False,
            "duckdb_staging": duckdb_staging,
            "llama_index_authority_receipts": {
                "env": env_index_receipt,
                "uop": uop_index_receipt,
            },
            "recorded_at": utc_now(),
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(core))
        receipt = {**core, "receipt_sha256": receipt_sha256}
        env.execute(
            "INSERT INTO ai_toolchain_sync_receipt_v16 VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                sequence,
                len(tools),
                len(action_rows),
                len(lane_rows),
                core["tool_matrix_sha256"],
                core["public_catalog_sha256"],
                core["prior_receipt_sha256"],
                canonical_json_bytes(receipt).decode("utf-8"),
                receipt_sha256,
                core["recorded_at"],
            ),
        )
        env.commit()
        uop.commit()
        return receipt
    finally:
        env.close()
        uop.close()


def resolve_lane_toolchain(
    *,
    lane_id: str,
    host_profile: str,
    available_tools: set[str] | None = None,
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
    action_classes = LANE_ACTION_CLASSES[exact_lane]
    matrix = _runtime_tool_matrix()
    ordered = list(
        dict.fromkeys(
            tool
            for action_class in action_classes
            for tool in ACTION_CLASS_TOOL_ORDER[action_class]
            if _tool_applies_to_lane(matrix[tool], exact_lane)
        )
    )
    available = set(ordered) if available_tools is None else set(available_tools)
    runnable = [tool for tool in ordered if tool in available]
    unavailable = [tool for tool in ordered if tool not in available]
    core = {
        "schema": "evidence-lane.ai-toolchain-lane-resolution.v1",
        "status": "PASS" if runnable else "BLOCKED_NO_RUNNABLE_TOOL",
        "plane": "CODEX",
        "host_profile": exact_host,
        "lane_id": exact_lane,
        "action_classes": list(action_classes),
        "ordered_tools": ordered,
        "runnable_tools": runnable,
        "unavailable_tools": unavailable,
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
    "resolve_lane_toolchain",
    "sync_ai_toolchain_authority",
]

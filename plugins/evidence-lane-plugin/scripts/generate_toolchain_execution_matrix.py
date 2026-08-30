#!/usr/bin/env python3
"""Generate the human-readable full Codex toolchain execution matrix."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.ai_toolchain import (
    ACTION_CLASS_TOOL_ORDER,
    CODEX_HOST_PROFILES,
    LANE_ACTION_CLASSES,
    _tool_applies_to_lane,
)

MATRIX = PLUGIN_ROOT / "toolchains" / "tool-requirement-matrix.v1.json"
OUTPUT = PLUGIN_ROOT / "toolchains" / "TOOLCHAIN_EXECUTION_MATRIX.md"
ROUTING_OUTPUT = PLUGIN_ROOT / "toolchains" / "tool-execution-routing.v1.json"
LICENSE_INVENTORY = PLUGIN_ROOT / "toolchains" / "tool-license-inventory.v1.json"

OWNER: dict[str, str] = {
    "Git": "git_optional.py + git_history.py",
    "Python": "scripts/bootstrap.py hidden runtime",
    "hashlib_pathlib": "hashing.py",
    "SQLite_CAS": "lane_engine.py + store.py",
    "SQLite_FTS5_BM25": "sqlite_indexing.py + lane_reader.py",
    "APSW_SQLite_engine": "sqlite_execution.py",
    "deterministic_TFIDF": "lane_engine.py",
    "LangGraph_Mermaid_engine": "graph_pipeline.py",
    "Python_Graphviz_DOT_engine": "graph_pipeline.py",
    "LlamaIndex_SQLite_indexer": "sqlite_indexing.py",
    "Mermaid_CLI_mmdc": "native_toolchain.py (optional renderer)",
    "Graphviz_dot": "native_toolchain.py + graph_pipeline.py",
    "Python_structural_parser": "lane_engine.py + ingest.py",
    "Package_sealer": "sealing.py + pv_package.py",
    "NodeJS_TypeScript": "repository CI and remote_adapter build",
    "pytest": "plugin tests + repository tests",
    "Ruff": "repository quality gate",
    "MyPy": "repository type gate",
    "Secret_redactor": "redaction.py",
    "Hash_chain_writer": "lineage.py + receipt_ledger.py",
    "ENV_UOP_classifier": "mode_governance.py",
    "PCM_MBA_operators": "mode_governance.py + formula engine",
    "DOCX_OpenXML": "lane_engine.py",
    "defusedxml": "lane_engine.py",
    "OpenXML_CSV_JSON_parser": "lane_engine.py",
    "openpyxl": "data_toolchain.py",
    "pandas": "data_toolchain.py",
    "python_calamine": "lane_engine.py",
    "pyarrow": "lane_engine.py + tabular_toolchain.py",
    "PPTX_OpenXML": "lane_engine.py",
    "pypdf": "lane_engine.py",
    "pypdfium2": "lane_engine.py",
    "pdfplumber": "lane_engine.py",
    "RapidOCR_ONNX_Runtime": "lane_engine.py",
    "pytesseract_Tesseract": "lane_engine.py + native_toolchain.py",
    "Pillow": "lane_engine.py",
    "Poppler_pdftotext_pdfinfo": "native_toolchain.py + PDF fallbacks",
    "Ghostscript": "native_toolchain.py license-gated PDF fallback",
    "OpenCV": "lane_engine.py OCR preprocessing",
    "Custom_schema_compiler": "custom_source_schema.py",
    "SQLite_immutable_URI_reader": "lane_engine.py",
    "Safe_archive_intake": "lane_engine.py + source_authority.py",
    "Citation_binder": "web_toolchain.py + research lane",
    "Project_inventory": "lane_engine.py project_engulf",
    "Git_detector": "git_optional.py",
    "Compatibility_mapper": "lane_engine.py sqlite_brain",
    "MCP_Python_SDK": "mcp_server.py + internal_sdk.py",
    "Pydantic": "typed tool/request/result modules",
    "HTTPX": "web_toolchain.py + persistence.py + GitHub adapter",
    "Cryptography_PyJWT": "auth.py + protected remote profiles",
    "PowerShell_Win32_APIs": "codex_release scripts + hook/tunnel hosts",
    "ripgrep_15_2_0": "search_toolchain.py",
    "SevenZip_NSIS_extractor": "install_native_toolchain.py non-elevated Tesseract extraction",
    "jq": "native_toolchain.py JSON validation/projection",
    "FFmpeg": "native_toolchain.py media probe/extraction",
    "PyMuPDF": "lane_engine.py PDF primary",
    "Docling": "document_toolchain.py",
    "lxml": "web_toolchain.py + lane XML processing",
    "BeautifulSoup4": "web_toolchain.py",
    "markdownify": "web_toolchain.py",
    "html2text": "web_toolchain.py",
    "trafilatura": "web_toolchain.py",
    "DuckDB": "tabular_toolchain.py",
    "SQLAlchemy": "data_toolchain.py",
    "Tableau_Hyper_API": "data_toolchain.py",
    "LangChain": "hybrid_retrieval.py + graph_pipeline.py + SDK",
    "SentenceTransformers": "semantic_retrieval.py",
    "FAISS_CPU": "hybrid_retrieval.py",
    "rank_bm25": "hybrid_retrieval.py + sqlite_indexing.py",
    "FastAPI": "runtime_api.py",
    "Uvicorn": "runtime_api.py",
    "Pydantic_Settings": "runtime_api.py",
    "python_multipart": "runtime_api.py source staging",
    "Requests": "web_toolchain.py fallback",
    "aiofiles": "runtime_api.py bounded staging",
    "orjson": "runtime_api.py response transport",
    "python_dotenv": "runtime_api.py maintainer-only loader",
    "Tenacity": "web_toolchain.py bounded retry",
    "psutil": "runtime_toolchain.py bounded process/resource telemetry",
    "DDGS": "web_toolchain.py explicit discovery",
    "PyGithub": "github_toolchain.py",
    "GitPython": "git_optional.py parity",
    "tldextract": "web_toolchain.py",
    "validators": "web_toolchain.py",
    "readability_lxml": "web_toolchain.py",
    "Polars": "tabular_toolchain.py",
    "TreeSitter_LanguagePack": "code_toolchain.py + ingest.py",
    "RapidFuzz": "entity_reconciliation.py + project_engulf",
    "rustworkx": "graph_pipeline.py",
    "sqlite_vec": "semantic_retrieval.py",
    "HuggingFace_Hub_ModelSnapshot": "install_native_toolchain.py model prefetch",
    "NextJS_React_ThreeJS_FramerMotion": "remote_adapter documentation projection",
    "GitHub_Actions": ".github/workflows delivery proof",
    "Vercel_Git_integration": "branch preview delivery proof",
    "OpenAI_Agents_SDK": "ecosystem_toolchain.py + internal_sdk.py",
    "FastMCP": "ecosystem_toolchain.py + mcp_server.py",
    "GitHub_MCP_Server": "mcp_adapter_routing.py + outer SDK tunnel route",
    "Filesystem_MCP_Server": "mcp_adapter_routing.py + root-scoped outer SDK route",
    "PostgreSQL_MCP_Server": "mcp_adapter_routing.py + database outer SDK route",
    "Slack_MCP_Server": "mcp_adapter_routing.py + approved-message outer SDK route",
    "Pinecone": "context_index_routing.py + hybrid_retrieval.py",
    "Weaviate": "context_index_routing.py + hybrid_retrieval.py",
    "Milvus": "context_index_routing.py + hybrid_retrieval.py",
    "OpenSearch": "context_index_routing.py + hybrid_retrieval.py",
    "LangSmith": "evaluation_toolchain.py + ai_toolchain.py",
    "TruLens": "evaluation_toolchain.py + ai_toolchain.py",
    "DeepEval": "evaluation_toolchain.py + ai_toolchain.py",
    "Promptfoo": "evaluation_toolchain.py + repository evaluation gate",
    "Langfuse": "observability_toolchain.py + runtime_toolchain.py",
    "Helicone": "observability_toolchain.py + runtime_toolchain.py",
    "OpenTelemetry": "observability_toolchain.py + runtime trace correlation",
    "Grafana": "observability_toolchain.py + runtime evidence route",
    "Docker": "deployment_toolchain.py + delivery gate",
    "Kubernetes": "deployment_toolchain.py + delivery gate",
    "AWS_Lambda": "deployment_toolchain.py + deployment evidence route",
    "Google_Cloud_Run": "deployment_toolchain.py + deployment evidence route",
    "AWS": "deployment_toolchain.py + deployment evidence route",
    "Azure": "deployment_toolchain.py + deployment evidence route",
    "Google_Cloud": "deployment_toolchain.py + deployment evidence route",
}

NAMED_AUTHORITIES = {
    "Agent Learning": "Learning SQLite + MMD/DOT + LlamaIndex/FTS; vector side index only by policy",
    "Canon Input/Consequence": "Canon SQLite and nested consequence graph; LangGraph, Graphviz, rustworkx",
    "Project Memory": "Memory SQLite, LlamaIndex, FTS5/BM25, optional local semantic retrieval",
    "Project Overlay": "HIL-only progressive blast-radius SQLite + graph analytics",
    "Source Authority": "CAS/source identities, extraction, FTS, citations, changed-only refresh",
    "Project Universe": "Per-project relationship graph only",
    "Connector Brain": "Project-to-project mini-brain federation with explicit grants and hashes",
    "Project Authority": "Registration, layout, pointer/member identities and project routing",
    "Receipt Ledger": "Append-only exact receipt bytes, links, FTS and hash chain",
    "Session Authority": "Sessions, attachments, State Travel and Goal projection",
    "Instructions": "AGENTS.md + host MEMORY.md instruction arm; separate non-SQLite authority",
}


def group_for(tool: str, surfaces: list[str]) -> str:
    if tool in {
        "SQLite_CAS", "SQLite_FTS5_BM25", "APSW_SQLite_engine",
        "deterministic_TFIDF", "LlamaIndex_SQLite_indexer", "sqlite_vec",
        "SentenceTransformers", "FAISS_CPU", "rank_bm25",
        "hashlib_pathlib", "Hash_chain_writer",
        "HuggingFace_Hub_ModelSnapshot",
        "Pinecone", "Weaviate", "Milvus", "OpenSearch",
    }:
        return "Authority, indexing, and retrieval"
    if tool in {
        "LangGraph_Mermaid_engine", "Python_Graphviz_DOT_engine", "Graphviz_dot",
        "Mermaid_CLI_mmdc", "rustworkx", "TreeSitter_LanguagePack",
        "Python_structural_parser", "RapidFuzz",
    }:
        return "Graphs, AST, and reconciliation"
    if tool in {
        "DOCX_OpenXML", "defusedxml", "PPTX_OpenXML", "pypdf", "pypdfium2",
        "pdfplumber", "RapidOCR_ONNX_Runtime", "pytesseract_Tesseract", "Pillow",
        "Poppler_pdftotext_pdfinfo", "Ghostscript", "OpenCV", "PyMuPDF", "Docling", "FFmpeg",
    }:
        return "Documents, OCR, and media"
    if tool in {
        "OpenXML_CSV_JSON_parser", "openpyxl", "pandas", "python_calamine",
        "pyarrow", "DuckDB", "Polars", "SQLAlchemy", "Tableau_Hyper_API",
    }:
        return "Data, Excel, and databases"
    if tool in {
        "HTTPX", "Requests", "DDGS", "trafilatura", "readability_lxml",
        "BeautifulSoup4", "lxml", "markdownify", "html2text", "tldextract",
        "validators", "Citation_binder", "Tenacity",
    }:
        return "Web, research, and source intake"
    if tool in {
        "FastAPI", "Uvicorn", "Pydantic", "Pydantic_Settings", "python_multipart",
        "aiofiles", "orjson", "python_dotenv", "psutil", "MCP_Python_SDK",
        "PowerShell_Win32_APIs", "Cryptography_PyJWT", "Python",
        "OpenAI_Agents_SDK", "FastMCP", "GitHub_MCP_Server",
        "Filesystem_MCP_Server", "PostgreSQL_MCP_Server", "Slack_MCP_Server",
    }:
        return "Hidden runtime, MCP, API, and tunnel"
    if tool in {
        "Git", "GitPython", "PyGithub", "Git_detector", "ripgrep_15_2_0", "SevenZip_NSIS_extractor", "jq",
        "NodeJS_TypeScript", "GitHub_Actions", "Vercel_Git_integration",
        "NextJS_React_ThreeJS_FramerMotion",
        "Docker", "Kubernetes", "AWS_Lambda", "Google_Cloud_Run",
        "AWS", "Azure", "Google_Cloud",
    }:
        return "Git, search, JSON, CI, and public adapter"
    if tool in {"LangChain"}:
        return "RAG and workflow composition"
    if tool in {"LangSmith", "TruLens", "DeepEval", "Promptfoo"}:
        return "Evaluation and testing"
    if tool in {"Langfuse", "Helicone", "OpenTelemetry", "Grafana"}:
        return "Observability and telemetry"
    return "Internal governance, build, and quality"


def main() -> int:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    rows = list(matrix["requirements"])
    license_inventory = json.loads(LICENSE_INVENTORY.read_text(encoding="utf-8"))
    license_rows = {
        str(row["tool"]): dict(row) for row in license_inventory["rows"]
    }
    tools_by_name = {str(row["tool"]): dict(row) for row in rows}
    tool_names = {str(row["tool"]) for row in rows}
    if tool_names != set(OWNER):
        raise SystemExit(
            "Tool owner map mismatch: "
            + json.dumps(
                {"missing": sorted(tool_names - set(OWNER)), "extra": sorted(set(OWNER) - tool_names)}
            )
        )
    if (
        license_inventory.get("all_tool_requirements_classified") is not True
        or set(license_rows) != tool_names
    ):
        raise SystemExit("Every tool requirement must have one license classification.")
    lines = [
        "# Evidence Lane Codex Toolchain Execution Matrix",
        "",
        f"Derived tool count: **{len(rows)}**. Public MCP action count is separate.",
        "",
        "Host plane: **Codex Desktop, Codex CLI, and Codex VM only**. ChatGPT is a separate future plane.",
        "",
        "Execution law: tools run only when the active lane/action/source type selects them. Presence never means run everything. SQLite remains durable authority; analytical, vector, graph, and web tools produce bounded evidence for SQLite persistence.",
        "",
        "## Eighteen project-sector lanes",
        "",
        "| Lane | Action classes | Ordered eligible tools |",
        "|---|---|---|",
    ]
    for lane_id, classes in LANE_ACTION_CLASSES.items():
        ordered = list(
            dict.fromkeys(
                tool
                for group in classes
                for tool in ACTION_CLASS_TOOL_ORDER[group]
                if _tool_applies_to_lane(tools_by_name[tool], lane_id)
            )
        )
        lines.append(f"| `{lane_id}` | {', '.join(classes)} | {', '.join(ordered)} |")
    lines.extend([
        "",
        "## Named/root authority surfaces",
        "",
        "| Authority | Execution role |",
        "|---|---|",
    ])
    for authority, role in NAMED_AUTHORITIES.items():
        lines.append(f"| {authority} | {role} |")
    grouped: dict[str, list[dict[str, object]]] = {}
    execution_rows: list[dict[str, object]] = []
    for row in rows:
        tool = str(row["tool"])
        action_classes = [name for name, ordered in ACTION_CLASS_TOOL_ORDER.items() if tool in ordered]
        primary = [name for name in action_classes if ACTION_CLASS_TOOL_ORDER[name][0] == tool]
        fallback = [name for name in action_classes if ACTION_CLASS_TOOL_ORDER[name][0] != tool]
        lanes = [
            lane_id
            for lane_id, classes in LANE_ACTION_CLASSES.items()
            if set(classes).intersection(action_classes)
            and _tool_applies_to_lane(dict(row), lane_id)
        ]
        execution_row = {
                **row,
                "action_classes": action_classes,
                "primary": primary,
                "fallback": fallback,
                "lanes": lanes,
                "action_order": {
                    action_class: ACTION_CLASS_TOOL_ORDER[action_class].index(tool) + 1
                    for action_class in action_classes
                },
                "implementation_owner": OWNER[tool],
                "license_record": license_rows[tool]["license_record"],
                "license_record_sha256": license_rows[tool][
                    "license_record_sha256"
                ],
                "runs_only_when_selected": True,
            }
        execution_rows.append(execution_row)
        grouped.setdefault(group_for(tool, list(row["surfaces"])), []).append(
            execution_row
        )
    for group, group_rows in grouped.items():
        lines.extend([
            "",
            f"## {group}",
            "",
            "| Tool | Requirement | Exact role | Declared surfaces | ENV-eligible lanes | Primary/fallback | Implementation owner | License/terms evidence |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for row in group_rows:
            role = str(row["role"]).replace("|", "\\|")
            surfaces = ", ".join(f"`{value}`" for value in row["surfaces"])
            lanes = ", ".join(f"`{value}`" for value in row["lanes"]) or "Non-lane gate"
            routing = "; ".join(
                value
                for value in (
                    "primary: " + ", ".join(row["primary"]) if row["primary"] else "",
                    "fallback/conditional: " + ", ".join(row["fallback"]) if row["fallback"] else "",
                )
                if value
            ) or "surface-owned"
            license_row = license_rows[str(row["tool"])]
            license_text = (
                f"{license_row['license_expression_or_terms']} — "
                f"{license_row['license_evidence']}"
            ).replace("|", "\\|")
            lines.append(
                f"| `{row['tool']}` | `{row['requirement']}` | {role} | {surfaces} | {lanes} | {routing} | `{OWNER[str(row['tool'])]}` | {license_text} |"
            )
    lines.extend([
        "",
        "## Derived control facts",
        "",
        f"- Codex host profiles: {', '.join(CODEX_HOST_PROFILES)}.",
        "- ChatGPT plane mixed: false.",
        "- Public action count fixed by this matrix: false.",
        "- Skill count fixed by this matrix: false.",
        "- Hook class count fixed by this matrix: false.",
        "- Every retained tool requires a package/runtime identity and an execution test before local install.",
        f"- License-classified tool requirements: {len(license_rows)} of {len(rows)}.",
        "- Exact copied runtime distribution licenses are materialized before tunnel startup; MCP actions remain a separate inventory.",
        "",
    ])
    routing_body = {
        "schema": "evidence-lane.tool-execution-routing.v1",
        "status": "PASS",
        "requirement_count": len(execution_rows),
        "host_profiles": list(CODEX_HOST_PROFILES),
        "chatgpt_plane_mixed": False,
        "conditional_execution_not_run_everything": True,
        "primary_and_fallback_order_explicit": True,
        "rows": execution_rows,
    }
    routing_body["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            routing_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    ).hexdigest().upper()
    ROUTING_OUTPUT.write_text(
        json.dumps(routing_body, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUTPUT)
    print(ROUTING_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

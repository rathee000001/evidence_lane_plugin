"""Installed runtime and tunnel toolchain parity without new public actions."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .deployment_toolchain import deployment_tool_catalog
from .ecosystem_toolchain import (
    ECOSYSTEM_ADAPTERS,
    inspect_ecosystem_adapter_runtime,
)
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .native_toolchain import try_resolve_native_tool
from .observability_toolchain import observability_tool_catalog
from .package_root import resolve_plugin_root
from .tunnel_identity_routing import tunnel_identity_routing_catalog

RUNTIME_TOOLCHAIN_SCHEMA = "evidence-lane.runtime-toolchain-prewarm.v1"


def _plugin_root() -> Path:
    return resolve_plugin_root(__file__)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _command_available(*names: str) -> str | None:
    return next((path for name in names if (path := shutil.which(name))), None)


def _fts5_available() -> bool:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(text)")
        connection.execute("INSERT INTO probe_fts(text) VALUES('evidence lane')")
        return connection.execute(
            "SELECT COUNT(*) FROM probe_fts WHERE probe_fts MATCH 'evidence'"
        ).fetchone() == (1,)
    except sqlite3.DatabaseError:
        return False
    finally:
        connection.close()


def inspect_runtime_toolchain(
    plugin_root: str | Path | None = None,
    *,
    prewarm_native: bool = False,
) -> dict[str, Any]:
    """Resolve all runtime, lane, build-gate, optional, and tunnel tool roles."""

    root = Path(plugin_root).resolve() if plugin_root else _plugin_root()
    matrix_path = root / "toolchains" / "tool-requirement-matrix.v1.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    rows = list(matrix["requirements"])
    module_map = {
        "MCP_Python_SDK": ("mcp",),
        "Pydantic": ("pydantic",),
        "HTTPX": ("httpx",),
        "Cryptography_PyJWT": ("cryptography", "jwt"),
        "defusedxml": ("defusedxml",),
        "openpyxl": ("openpyxl",),
        "pandas": ("pandas",),
        "python_calamine": ("python_calamine",),
        "pyarrow": ("pyarrow",),
        "pypdf": ("pypdf",),
        "pypdfium2": ("pypdfium2",),
        "pdfplumber": ("pdfplumber",),
        "RapidOCR_ONNX_Runtime": ("rapidocr", "onnxruntime"),
        "pytesseract_Tesseract": ("pytesseract",),
        "Pillow": ("PIL",),
        "OpenCV": ("cv2",),
        "LangGraph_Mermaid_engine": ("langgraph", "langchain_core"),
        "Python_Graphviz_DOT_engine": ("graphviz",),
        "LlamaIndex_SQLite_indexer": ("llama_index",),
        "PyMuPDF": ("pymupdf",),
        "Docling": ("docling",),
        "lxml": ("lxml",),
        "BeautifulSoup4": ("bs4",),
        "markdownify": ("markdownify",),
        "html2text": ("html2text",),
        "trafilatura": ("trafilatura",),
        "DuckDB": ("duckdb",),
        "SQLAlchemy": ("sqlalchemy",),
        "Tableau_Hyper_API": ("tableauhyperapi",),
        "LangChain": ("langchain",),
        "SentenceTransformers": ("sentence_transformers",),
        "FAISS_CPU": ("faiss",),
        "rank_bm25": ("rank_bm25",),
        "FastAPI": ("fastapi",),
        "Uvicorn": ("uvicorn",),
        "Pydantic_Settings": ("pydantic_settings",),
        "python_multipart": ("multipart",),
        "Requests": ("requests",),
        "aiofiles": ("aiofiles",),
        "orjson": ("orjson",),
        "python_dotenv": ("dotenv",),
        "Tenacity": ("tenacity",),
        "psutil": ("psutil",),
        "DDGS": ("ddgs",),
        "PyGithub": ("github",),
        "GitPython": ("git",),
        "tldextract": ("tldextract",),
        "validators": ("validators",),
        "readability_lxml": ("readability",),
        "APSW_SQLite_engine": ("apsw",),
        "Polars": ("polars",),
        "TreeSitter_LanguagePack": ("tree_sitter", "tree_sitter_language_pack"),
        "RapidFuzz": ("rapidfuzz",),
        "rustworkx": ("rustworkx",),
        "sqlite_vec": ("sqlite_vec",),
        "FFmpeg": ("imageio_ffmpeg",),
        "HuggingFace_Hub_ModelSnapshot": ("huggingface_hub",),
        "OpenAI_Agents_SDK": ("agents",),
        "FastMCP": ("fastmcp",),
        "LangSmith": ("langsmith",),
        "Langfuse": ("langfuse",),
        "OpenTelemetry": (
            "opentelemetry.sdk",
            "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        ),
    }
    command_map = {
        "Git": ("git",),
        "NodeJS_TypeScript": ("node",),
        "Mermaid_CLI_mmdc": ("mmdc",),
        "Graphviz_dot": ("dot",),
        "pytesseract_Tesseract": ("tesseract",),
        "Poppler_pdftotext_pdfinfo": (
            "pdftotext",
            "pdfinfo",
        ),
        "Ghostscript": ("gs", "gswin64c", "gswin32c"),
        "PowerShell_Win32_APIs": ("powershell", "pwsh"),
        "jq": ("jq",),
        "SevenZip_NSIS_extractor": ("seven_zip",),
    }
    build_gate_tools = {"pytest", "Ruff", "MyPy"}
    external_tools = {
        "GitHub_Actions",
        "Vercel_Git_integration",
    }
    repository_only_tools = {
        "NextJS_React_ThreeJS_FramerMotion",
        "NodeJS_TypeScript",
    }
    internal_source_map = {
        "hashlib_pathlib": "hashing.py",
        "deterministic_TFIDF": "lane_engine.py",
        "Mermaid_emitter": "lane_engine.py",
        "Graphviz_DOT_emitter": "lane_engine.py",
        "Python_structural_parser": "lane_engine.py",
        "Package_sealer": "sealing.py",
        "Secret_redactor": "redaction.py",
        "Hash_chain_writer": "lineage.py",
        "ENV_UOP_classifier": "mode_governance.py",
        "PCM_MBA_operators": "mode_governance.py",
        "DOCX_OpenXML": "lane_engine.py",
        "OpenXML_CSV_JSON_parser": "lane_engine.py",
        "PPTX_OpenXML": "lane_engine.py",
        "Custom_schema_compiler": "custom_source_schema.py",
        "SQLite_immutable_URI_reader": "source_sqlite.py",
        "Safe_archive_intake": "source_authority.py",
        "Citation_binder": "lane_engine.py",
        "Project_inventory": "lane_engine.py",
        "Git_detector": "git_optional.py",
        "Compatibility_mapper": "source_sqlite.py",
    }
    required_runtime_requirements = {
        "REQUIRED",
        "REQUIRED_DEPENDENCY",
        "REQUIRED_INTERNAL",
        "REQUIRED_INTERNAL_AI_ACTION_PLANE",
        "REQUIRED_WINDOWS_HOST",
        "REQUIRED_FOR_CONFIGURED_NETWORK_ADAPTERS",
        "REQUIRED_PROTECTED_REMOTE_PROFILES",
        "REQUIRED_HIDDEN_RUNTIME_BINARY",
        "REQUIRED_HIDDEN_RUNTIME_BINARY_AND_DEPENDENCY",
    }
    results: list[dict[str, Any]] = []
    prewarmed: list[str] = []
    for row in rows:
        tool = str(row["tool"])
        requirement = str(row["requirement"])
        state = "UNAVAILABLE"
        evidence: dict[str, Any] = {}
        hidden_aliases = {
            "Graphviz_dot": ("graphviz",),
            "pytesseract_Tesseract": ("tesseract",),
            "Poppler_pdftotext_pdfinfo": (
                "poppler_pdftotext",
                "poppler_pdfinfo",
            ),
            "Ghostscript": ("ghostscript",),
            "jq": ("jq",),
            "SevenZip_NSIS_extractor": ("seven_zip",),
        }
        if tool in hidden_aliases:
            resolved = {
                alias: try_resolve_native_tool(alias)
                for alias in hidden_aliases[tool]
            }
            state = "ACTIVE" if all(resolved.values()) else "UNAVAILABLE"
            evidence = {
                "hidden_runtime": True,
                "aliases": {
                    alias: (
                        {
                            "version": value.version,
                            "executable_sha256": value.executable_sha256,
                            "license_receipt_sha256": value.license_receipt_sha256,
                        }
                        if value is not None
                        else None
                    )
                    for alias, value in resolved.items()
                },
                "path_lookup_used": False,
            }
        elif tool == "Python":
            state = "ACTIVE"
            evidence = {"executable": sys.executable}
        elif tool in {"SQLite_CAS", "SQLite_immutable_URI_reader"}:
            state = "ACTIVE"
            evidence = {"sqlite_version": sqlite3.sqlite_version}
        elif tool == "SQLite_FTS5_BM25":
            state = "ACTIVE" if _fts5_available() else "UNAVAILABLE"
            evidence = {"fts5_probe": state == "ACTIVE"}
        elif tool == "ripgrep_15_2_0":
            binary = root / "toolchains" / "bin" / "windows-x86_64" / "rg.exe"
            state = "ACTIVE" if binary.is_file() else "UNAVAILABLE"
            evidence = {
                "path": binary.relative_to(root).as_posix(),
                "sha256": sha256_file(binary) if binary.is_file() else None,
            }
        elif tool in build_gate_tools:
            state = "BUILD_GATE_NOT_RUNTIME_REQUIRED"
        elif tool in ECOSYSTEM_ADAPTERS:
            adapter = inspect_ecosystem_adapter_runtime(tool)
            state = str(adapter["state"])
            evidence = {
                "adapter_receipt_sha256": adapter["receipt_sha256"],
                "modules": adapter["modules"],
                "commands": adapter["commands"],
                "credential_names": adapter["credential_names"],
                "credential_values_read": False,
                "network_probe_performed": False,
            }
        elif tool in external_tools:
            state = "EXTERNAL_PROOF_REQUIRED_AT_DELIVERY_GATE"
        elif tool in repository_only_tools:
            command = _command_available(*command_map.get(tool, ()))
            state = "REPOSITORY_ONLY_AVAILABLE" if command else "REPOSITORY_ONLY"
            evidence = {"command": command}
        elif tool in internal_source_map:
            source = root / "src" / "evidence_lane_plugin" / internal_source_map[tool]
            state = "ACTIVE" if source.is_file() else "UNAVAILABLE"
            evidence = {
                "path": source.relative_to(root).as_posix(),
                "sha256": sha256_file(source) if source.is_file() else None,
            }
        elif tool in module_map:
            modules = module_map[tool]
            availability = {name: _module_available(name) for name in modules}
            imageio_binary = None
            if tool == "FFmpeg" and all(availability.values()):
                import imageio_ffmpeg  # type: ignore[import-not-found]

                candidate = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
                imageio_binary = str(candidate) if candidate.is_file() else None
            tree_sitter_languages: list[str] | None = None
            tree_sitter_required_language_resolution: dict[str, bool] | None = None
            if tool == "TreeSitter_LanguagePack" and all(availability.values()):
                from tree_sitter_language_pack import (
                    DownloadError,
                    available_languages,  # type: ignore[import-not-found]
                    get_language,
                )

                from .code_toolchain import (
                    CODE_TOOLCHAIN_LANGUAGES,
                    initialize_hidden_tree_sitter_runtime,
                )

                initialize_hidden_tree_sitter_runtime()
                observed_languages = {str(value) for value in available_languages()}
                tree_sitter_languages = sorted(observed_languages)
                required_language_resolution: dict[str, bool] = {}
                for language_name in CODE_TOOLCHAIN_LANGUAGES:
                    try:
                        get_language(language_name)
                    except (
                        DownloadError,
                        KeyError,
                        OSError,
                        RuntimeError,
                        ValueError,
                    ):
                        required_language_resolution[language_name] = False
                    else:
                        required_language_resolution[language_name] = True
                tree_sitter_required_language_resolution = (
                    required_language_resolution
                )
                availability["all_required_languages"] = all(
                    required_language_resolution.values()
                )
            embedding_model: dict[str, Any] | None = None
            if tool in {
                "SentenceTransformers",
                "HuggingFace_Hub_ModelSnapshot",
            } and all(availability.values()):
                from .semantic_retrieval import configured_embedding_model

                contract = configured_embedding_model()
                availability["hidden_embedding_model"] = contract is not None
                if contract is not None:
                    embedding_model = {
                        "model_id": contract.model_id,
                        "dimension": contract.dimension,
                        "local_model_path_disclosed": False,
                        "network_download_allowed": False,
                    }
            command = (
                _command_available(*command_map[tool])
                if tool in command_map
                else None
            )
            state = (
                "ACTIVE"
                if all(availability.values())
                and (tool not in command_map or command is not None)
                and (tool != "FFmpeg" or imageio_binary is not None)
                else "UNAVAILABLE"
            )
            evidence = {
                "modules": availability,
                "command": command or imageio_binary,
                "binary_sha256": (
                    sha256_file(Path(imageio_binary)) if imageio_binary else None
                ),
                "tree_sitter_languages": tree_sitter_languages,
                "tree_sitter_required_language_resolution": (
                    tree_sitter_required_language_resolution
                ),
                "embedding_model": embedding_model,
            }
        elif tool in command_map:
            command = _command_available(*command_map[tool])
            state = "ACTIVE" if command else "UNAVAILABLE"
            evidence = {"command": command}
        else:
            state = "DECLARED_COMPONENT"
        startup_required = (
            requirement in required_runtime_requirements
            and tool not in repository_only_tools
            and tool not in external_tools
        )
        if startup_required and state == "UNAVAILABLE":
            result_status = "FAIL"
        else:
            result_status = "PASS"
        results.append(
            {
                **row,
                "state": state,
                "startup_required": startup_required,
                "status": result_status,
                "evidence": evidence,
            }
        )
    if prewarm_native:
        from .lane_engine import prewarm_native_dependencies

        prewarmed.extend(prewarm_native_dependencies())
    failures = [row for row in results if row["status"] != "PASS"]
    observability = observability_tool_catalog()
    deployment = deployment_tool_catalog()
    tunnel_identity = tunnel_identity_routing_catalog()
    body = {
        "schema": RUNTIME_TOOLCHAIN_SCHEMA,
        "status": "PASS" if not failures else "FAIL",
        "matrix": "toolchains/tool-requirement-matrix.v1.json",
        "matrix_sha256": sha256_file(matrix_path),
        "requirement_count": len(results),
        "runtime_required_count": sum(bool(row["startup_required"]) for row in results),
        "failure_count": len(failures),
        "results": results,
        "prewarmed_native_dependencies": prewarmed,
        "mcp_public_action_counted_here": False,
        "public_adapter_dependencies_installed_in_plugin_runtime": False,
        "observability_tool_count": observability["tool_count"],
        "observability_catalog_sha256": observability["receipt_sha256"],
        "raw_authority_payload_export_allowed": False,
        "deployment_tool_count": deployment["tool_count"],
        "deployment_catalog_sha256": deployment["receipt_sha256"],
        "deployment_results_are_evidence_only": True,
        "tunnel_identity_routing_sha256": tunnel_identity["receipt_sha256"],
        "tunnel_scheduled_task_owner": False,
        "tunnel_count_is_fixed_ceiling": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = ["RUNTIME_TOOLCHAIN_SCHEMA", "inspect_runtime_toolchain"]

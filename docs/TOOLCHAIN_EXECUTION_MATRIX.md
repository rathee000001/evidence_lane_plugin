# Evidence Lane v4 toolchain execution matrix

This document is generated from the current executable registry and installation contracts. It is a readable projection for GitHub and local inspection; the JSON files listed below remain authoritative.

- Registered operations: **297**
- Retained tools and services: **103**
- First-detection components: **10**
- Provider environments: **4**
- Windows shared root: `C:/Apps/EvidenceLaneStudio`
- Current qualification: `provisioning_inputs_and_routes_only`
- Full installed bundle verified: **no — installed qualification is pending**

Tools are selected by the registered operation and route before invocation. Catalog presence never means every tool runs, and a failed invoked adapter does not silently switch to another adapter. Installation membership and execution proof are separate.

## Source contracts

| Role | Path | SHA-256 |
|---|---|---|
| operation routes | `plugins/evidence-lane-plugin/toolchains/operation-toolchains.v4.json` | `2263ceb583303afd8428dda4fb3ffed9a29cb12c3bb7406533751904301d8754` |
| shared installation | `plugins/evidence-lane-plugin/toolchains/shared-toolchain.v4.json` | `34f9a2a5300424498083f30ab3d2c1bfe7833f63e7b8fb6107ccf027fa59d542` |
| license index | `plugins/evidence-lane-plugin/toolchains/licenses/retained-install-license-index.v4.json` | `50291cfbc9563aa07ea7667dddace6975a2f2b4aa9e15d0618d56d477c6e3d2d` |
| first detection | `plugins/evidence-lane-plugin/provisioning/full-bundle-plan.v4.json` | `084b7919d932ec3e9189c2dd18268cd3bad36ba90a5be7f88aed4b49f9d8227b` |

## First-detection component ownership

| Component | Condition | Purpose | Tool count | Shared across projects |
|---|---|---|---:|---|
| `core-engine-studio` | `always` | Engine Python, locked Python libraries, immutable plugin copy, Studio and service launchers | 88 | yes |
| `native-tools` | `always` | Portable Git and retained native command tools with copied license/source records | 9 | yes |
| `node-runtime` | `always` | Pinned Node runtime and locked JavaScript tool assets | 3 | yes |
| `model-assets` | `always` | Pinned embedding, grammar, OCR, Tesseract and Docling model bytes | 0 | yes |
| `powerbi-runtime` | `always` | Pinned TOM and PBIXRay helper runtimes with source and license inventories | 2 | yes |
| `provider-cpu` | `always` | CPU provider environment and self-test inputs | 0 | yes |
| `provider-cuda` | `nvidia_cuda_compatible` | CUDA provider environment selected only after measured compatible NVIDIA hardware | 0 | yes |
| `provider-directml` | `directml_compatible` | DirectML OCR environment selected only for a measured compatible Windows DirectX 12 device | 0 | yes |
| `provider-rocm` | `amd_rocm_compatible` | ROCm environment selected only after its exact Windows AMD compatibility contract | 0 | yes |
| `ghostscript-runtime` | `explicit_ghostscript_license` | Optional Ghostscript runtime selected only with an explicit accepted license reference | 1 | yes |

## Provider environments

| Provider | Python | Operation coverage | Install state | Hardware execution | Qualification requirement |
|---|---|---|---|---|---|
| `cpu` | `3.14` | `code_embed_text` | `not_verified` | `not_verified` | compatible_host_plus_exact_installed_record_plus_fresh_provider_self_test_and_operation_qualification |
| `cuda` | `3.14` | `code_embed_text` | `not_verified` | `not_verified` | compatible_host_plus_exact_installed_record_plus_fresh_provider_self_test_and_operation_qualification |
| `directml` | `3.12` | `rapidocr_lines` | `not_verified` | `not_verified` | compatible_host_plus_exact_installed_record_plus_fresh_provider_self_test_and_operation_qualification |
| `rocm` | `3.12` | `code_embed_text` | `not_verified` | `not_verified` | compatible_host_plus_exact_installed_record_plus_fresh_provider_self_test_and_operation_qualification |

## Native executables and fixed launch inputs

| Tool | Version | Component | Installed executable paths |
|---|---|---|---|
| `jq` | `1.8.2` | `native-tools` | `jq/bin/jq.exe` |
| `graphviz` | `15.1.1` | `native-tools` | `graphviz/bin/dot.exe` |
| `poppler` | `26.02.0-0` | `native-tools` | `poppler/Library/bin/pdftotext.exe`, `poppler/Library/bin/pdfinfo.exe` |
| `seven_zip_extractor` | `26.01` | `native-tools` | `seven_zip/bin/7z.exe` |
| `tesseract` | `5.5.3.20260724` | `native-tools` | `tesseract/tesseract.exe` |
| `ghostscript` | `10.07.1` | `ghostscript-runtime` | `ghostscript/bin/gswin64c.exe` |
| `ffmpeg` | `imageio-ffmpeg==0.6.0` | `native-tools` | `ffmpeg/ffmpeg.exe` |
| `ripgrep` | `15.2.0` | `native-tools` | `ripgrep/bin/rg.exe` |
| `lessmsi_extractor` | `2.12.9` | `native-tools` | `lessmsi/lessmsi.exe` |
| `libreoffice` | `26.2.6` | `native-tools` | `libreoffice/program/soffice.com`, `libreoffice/program/soffice.exe` |
| `powerbi_tom` | `19.114.12` | `powerbi-runtime` | `powerbi_tom/evidence-lane-powerbi.exe` |
| `powerbi_pbix` | `0.15.5` | `powerbi-runtime` | `powerbi_pbix/python.exe` |
| `git` | `2.54.0.windows.1` | `native-tools` | `git/cmd/git.exe` |

## Retained tool installation and license matrix

| Tool | Kind | Base requirement | Provisioning route | Bundle component | Windows bundle | External configuration | License gate | Install state | Adapter execution | License or terms |
|---|---|---|---|---|---|---|---|---|---|---|
| `Git` | `native_tool` | `REQUIRED_SHARED_PORTABLE_OR_HOST_GIT` | `shared_portable_git` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | GPL-2.0-only |
| `Python` | `native_tool` | `REQUIRED` | `pinned_shared_engine_and_tool_interpreters` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | PSF-2.0 |
| `hashlib_pathlib` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `SQLite_CAS` | `library` | `REQUIRED` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | LicenseRef-SQLite-Public-Domain AND MIT |
| `SQLite_FTS5_BM25` | `library` | `REQUIRED` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | LicenseRef-SQLite-Public-Domain AND MIT |
| `APSW_SQLite_engine` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `deterministic_TFIDF` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `LangGraph_Mermaid_engine` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Python_Graphviz_DOT_engine` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `LlamaIndex_SQLite_indexer` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Mermaid_CLI_mmdc` | `library` | `OPTIONAL_HIDDEN_RUNTIME_RENDERER` | `shared_node_runtime_and_locked_npm_assets` | `node-runtime` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Graphviz_dot` | `native_tool` | `REQUIRED_HIDDEN_RUNTIME_BINARY` | `shared_native_manifest` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | EPL-1.0 |
| `Python_structural_parser` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `NodeJS_TypeScript` | `native_tool` | `REQUIRED_REPOSITORY_ONLY` | `shared_node_runtime_and_locked_npm_assets` | `node-runtime` | yes | no | no | `not_verified` | `not_verified` | EXTERNAL_SERVICE_OR_REPOSITORY_TERMS |
| `pytest` | `library` | `REQUIRED_CODE_ACCEPTANCE` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Ruff` | `library` | `REQUIRED_CONFIGURED_GATE` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `MyPy` | `library` | `REQUIRED_CONFIGURED_GATE` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Secret_redactor` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `Hash_chain_writer` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `ENV_UOP_classifier` | `internal_component` | `REQUIRED_INTERNAL_AI_ACTION_PLANE` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `DOCX_OpenXML` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | MIT |
| `defusedxml` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `OpenXML_CSV_JSON_parser` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `openpyxl` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `pandas` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `python_calamine` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `pyarrow` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `PPTX_OpenXML` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | MIT |
| `pypdf` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `pypdfium2` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `pdfplumber` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `RapidOCR_ONNX_Runtime` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `pytesseract_Tesseract` | `native_tool` | `REQUIRED_HIDDEN_RUNTIME_BINARY_AND_DEPENDENCY` | `shared_native_manifest` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | Apache-2.0 |
| `Pillow` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Poppler_pdftotext_pdfinfo` | `native_tool` | `REQUIRED_HIDDEN_RUNTIME_BINARY` | `shared_native_manifest` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | GPL-2.0-or-later |
| `Ghostscript` | `library` | `OPTIONAL_EXTERNAL_LICENSE_GATED` | `shared_native_manifest` | `ghostscript-runtime` | yes | no | yes | `not_verified` | `not_verified` | AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial |
| `OpenCV` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Custom_schema_compiler` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `SQLite_immutable_URI_reader` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | LicenseRef-SQLite-Public-Domain AND MIT |
| `Safe_archive_intake` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `Citation_binder` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `Project_inventory` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `Git_detector` | `internal_component` | `OPTIONAL_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `Compatibility_mapper` | `internal_component` | `REQUIRED_INTERNAL` | `packaged_engine_code_or_standard_library` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | MIT |
| `MCP_Python_SDK` | `mcp_framework` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Pydantic` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `HTTPX` | `library` | `REQUIRED_FOR_CONFIGURED_NETWORK_ADAPTERS` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Cryptography_PyJWT` | `library` | `REQUIRED_PROTECTED_REMOTE_PROFILES` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `PowerShell_Win32_APIs` | `library` | `REQUIRED_WINDOWS_HOST` | `windows_host_api_with_platform_verification` | `core-engine-studio` | no | no | no | `not_verified` | `not_verified` | HOST_PLATFORM_TERMS |
| `ripgrep_15_2_0` | `library` | `PACKAGED_PRE_INDEX_HELPER` | `shared_native_manifest` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | MIT OR Unlicense |
| `SevenZip_NSIS_extractor` | `native_tool` | `REQUIRED_HIDDEN_RUNTIME_BINARY` | `shared_native_manifest` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | LicenseRef-7-Zip |
| `jq` | `native_tool` | `REQUIRED_HIDDEN_RUNTIME_BINARY` | `shared_native_manifest` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | MIT |
| `FFmpeg` | `native_tool` | `REQUIRED_HIDDEN_RUNTIME_BINARY_AND_DEPENDENCY` | `shared_native_manifest` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | BSD-2-Clause wrapper; bundled FFmpeg license reported at install |
| `PyMuPDF` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Docling` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `lxml` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `BeautifulSoup4` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `markdownify` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `html2text` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `trafilatura` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `DuckDB` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `SQLAlchemy` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Tableau_Hyper_API` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `LangChain` | `mcp_framework` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `SentenceTransformers` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `FAISS_CPU` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `rank_bm25` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `FastAPI` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Uvicorn` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Pydantic_Settings` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `python_multipart` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Requests` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `aiofiles` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `orjson` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `python_dotenv` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Tenacity` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `DDGS` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `PyGithub` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `GitPython` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `tldextract` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `validators` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `readability_lxml` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Polars` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `TreeSitter_LanguagePack` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `rustworkx` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `sqlite_vec` | `library` | `CONDITIONAL_SQLITE_VECTOR_EXTENSION` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `HuggingFace_Hub_ModelSnapshot` | `library` | `REQUIRED_LOCAL_UPDATE_MODEL_ACQUISITION` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `NextJS_React_ThreeJS_FramerMotion` | `library` | `REPOSITORY_PUBLIC_ADAPTER_ONLY` | `shared_node_runtime_and_locked_npm_assets` | `node-runtime` | yes | no | no | `not_verified` | `not_verified` | EXTERNAL_SERVICE_OR_REPOSITORY_TERMS |
| `OpenAI_Agents_SDK` | `mcp_framework` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `FastMCP` | `mcp_framework` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Pinecone` | `external_service` | `CONFIGURED_EXTERNAL_SERVICE` | `packaged_adapter_and_explicit_external_service_configuration` | `—` | no | yes | no | `not_verified` | `not_verified` | EXTERNAL_SERVICE_OR_REPOSITORY_TERMS |
| `Weaviate` | `external_service` | `CONFIGURED_EXTERNAL_SERVICE` | `packaged_adapter_and_explicit_external_service_configuration` | `—` | no | yes | no | `not_verified` | `not_verified` | EXTERNAL_SERVICE_OR_REPOSITORY_TERMS |
| `Milvus` | `external_service` | `CONFIGURED_EXTERNAL_SERVICE` | `packaged_adapter_and_explicit_external_service_configuration` | `—` | no | yes | no | `not_verified` | `not_verified` | EXTERNAL_SERVICE_OR_REPOSITORY_TERMS |
| `OpenSearch` | `external_service` | `CONFIGURED_EXTERNAL_SERVICE` | `packaged_adapter_and_explicit_external_service_configuration` | `—` | no | yes | no | `not_verified` | `not_verified` | EXTERNAL_SERVICE_OR_REPOSITORY_TERMS |
| `LangSmith` | `external_service` | `REQUIRED_DEPENDENCY` | `packaged_adapter_and_explicit_external_service_configuration` | `—` | no | yes | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Langfuse` | `external_service` | `REQUIRED_DEPENDENCY` | `packaged_adapter_and_explicit_external_service_configuration` | `—` | no | yes | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `OpenTelemetry` | `external_service` | `REQUIRED_DEPENDENCY` | `packaged_adapter_and_explicit_external_service_configuration` | `—` | no | yes | no | `not_verified` | `not_verified` | EXACT_INSTALLED_DISTRIBUTION_METADATA |
| `Grafana` | `external_service` | `CONFIGURED_EXTERNAL_SERVICE` | `packaged_adapter_and_explicit_external_service_configuration` | `—` | no | yes | no | `not_verified` | `not_verified` | EXTERNAL_SERVICE_OR_REPOSITORY_TERMS |
| `LibreOffice` | `native_tool` | `REQUIRED_NATIVE_BINARY` | `shared_native_manifest` | `native-tools` | yes | no | no | `not_verified` | `not_verified` | MPL-2.0 AND LGPL-3.0-or-later |
| `PowerBI_TOM` | `native_tool` | `REQUIRED_NATIVE_BINARY` | `shared_native_manifest` | `powerbi-runtime` | yes | no | no | `not_verified` | `not_verified` | LicenseRef-Microsoft-AnalysisServices |
| `PBIXRay` | `native_tool` | `REQUIRED_NATIVE_BINARY` | `shared_native_manifest` | `powerbi-runtime` | yes | no | no | `not_verified` | `not_verified` | LicenseRef-Bundled-Python-and-Package-Licenses |
| `JSONSchema` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | MIT |
| `ReportLab` | `library` | `REQUIRED_DEPENDENCY` | `shared_locked_python_environment` | `core-engine-studio` | yes | no | no | `not_verified` | `not_verified` | BSD-3-Clause |

## Operation coverage by profile

| Profile | Operation count |
|---|---:|
| `artifacts` | 10 |
| `canon` | 16 |
| `chat_lineage` | 2 |
| `chatlineage` | 3 |
| `code` | 21 |
| `continuity` | 5 |
| `core` | 27 |
| `custom` | 7 |
| `data` | 14 |
| `delta` | 4 |
| `document` | 13 |
| `extensions` | 3 |
| `images_ocr` | 11 |
| `learning` | 3 |
| `memory` | 7 |
| `pdf_ocr` | 14 |
| `plan` | 12 |
| `power_bi` | 9 |
| `presentation` | 13 |
| `projects` | 8 |
| `receipts` | 3 |
| `recovery` | 11 |
| `research` | 10 |
| `runtime` | 2 |
| `sessions` | 5 |
| `sources` | 27 |
| `spreadsheet` | 16 |
| `tableau` | 8 |
| `universe` | 13 |

## Registered operation execution matrix

| Operation | Profile | Route IDs | Providers | Ordered route tools | Systems | Host profiles | Worker operations | Fallback after invocation | Installation implied |
|---|---|---|---|---|---|---|---|---|---|
| `accelerator_configure` | `runtime` | `accelerator_configure.engine` | `engine_cpu` | accelerator_configure.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `accelerator_read` | `runtime` | `accelerator_read.engine` | `engine_cpu` | accelerator_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `artifacts_current` | `artifacts` | `artifacts_current.engine` | `engine_cpu` | artifacts_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `artifacts_index` | `artifacts` | `artifacts_index.sqlite_sqlalchemy`, `artifacts_index.native`, `artifacts_index.calamine`, `artifacts_index.presentation`, `artifacts_index.arrow`, `artifacts_index.pdf_pymupdf`, `artifacts_index.pdf_pypdf`, `artifacts_index.pdf_pdfplumber` | `engine_cpu` | artifacts_index.sqlite_sqlalchemy: Python → SQLAlchemy<br>artifacts_index.native: Python<br>artifacts_index.calamine: Python → python_calamine<br>artifacts_index.presentation: Python → PPTX_OpenXML<br>artifacts_index.arrow: Python → pyarrow<br>artifacts_index.pdf_pymupdf: Python → pypdf → PyMuPDF<br>artifacts_index.pdf_pypdf: Python → pypdf<br>artifacts_index.pdf_pdfplumber: Python → pypdf → pdfplumber | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `artifacts_index_media` | `artifacts` | `artifacts_index_media.raster`, `artifacts_index_media.vector`, `artifacts_index_media.media` | `engine_cpu` | artifacts_index_media.raster: Python → Pillow<br>artifacts_index_media.vector: Python → defusedxml<br>artifacts_index_media.media: Python → FFmpeg | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `artifacts_query` | `artifacts` | `artifacts_query.engine` | `engine_cpu` | artifacts_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `artifacts_read` | `artifacts` | `artifacts_read.engine` | `engine_cpu` | artifacts_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `artifacts_refresh` | `artifacts` | `artifacts_refresh.sqlite_sqlalchemy`, `artifacts_refresh.native`, `artifacts_refresh.calamine`, `artifacts_refresh.presentation`, `artifacts_refresh.arrow`, `artifacts_refresh.pdf_pymupdf`, `artifacts_refresh.pdf_pypdf`, `artifacts_refresh.pdf_pdfplumber` | `engine_cpu` | artifacts_refresh.sqlite_sqlalchemy: Python → SQLAlchemy<br>artifacts_refresh.native: Python<br>artifacts_refresh.calamine: Python → python_calamine<br>artifacts_refresh.presentation: Python → PPTX_OpenXML<br>artifacts_refresh.arrow: Python → pyarrow<br>artifacts_refresh.pdf_pymupdf: Python → pypdf → PyMuPDF<br>artifacts_refresh.pdf_pypdf: Python → pypdf<br>artifacts_refresh.pdf_pdfplumber: Python → pypdf → pdfplumber | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `artifacts_refresh_media` | `artifacts` | `artifacts_refresh_media.raster`, `artifacts_refresh_media.vector`, `artifacts_refresh_media.media` | `engine_cpu` | artifacts_refresh_media.raster: Python → Pillow<br>artifacts_refresh_media.vector: Python → defusedxml<br>artifacts_refresh_media.media: Python → FFmpeg | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `capture_bind` | `chatlineage` | `capture_bind.engine` | `engine_cpu` | capture_bind.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `client_context` | `core` | `client_context.engine` | `engine_cpu` | client_context.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `code_apply` | `code` | `code_apply.structural`, `code_apply.structural_export`, `code_apply.syntax`, `code_apply.syntax_export` | `engine_cpu` | code_apply.structural: Python → SQLite_FTS5_BM25 → Python_structural_parser<br>code_apply.structural_export: Python → SQLite_FTS5_BM25 → Python_structural_parser → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>code_apply.syntax: Python → SQLite_FTS5_BM25 → Python_structural_parser → TreeSitter_LanguagePack<br>code_apply.syntax_export: Python → SQLite_FTS5_BM25 → Python_structural_parser → TreeSitter_LanguagePack → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows`, `Darwin`, `Linux` | — | `code_parse_content`, `render_lane_view` | no | no |
| `code_current` | `code` | `code_current.engine` | `engine_cpu` | code_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `code_impact` | `code` | `code_impact.engine` | `engine_cpu` | code_impact.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `code_index` | `code` | `code_index.registered_parser` | `engine_cpu` | code_index.registered_parser: Python → SQLite_FTS5_BM25 → Python_structural_parser | `Windows`, `Darwin`, `Linux` | — | `code_parse_file` | no | no |
| `code_index_git` | `code` | `code_index_git.git_checkpoint` | `engine_cpu` | code_index_git.git_checkpoint: Python → SQLite_FTS5_BM25 → Python_structural_parser → Git | `Windows`, `Darwin`, `Linux` | — | `code_parse_git_blob`, `code_git_checkpoint` | no | no |
| `code_index_syntax` | `code` | `code_index_syntax.registered_parser` | `engine_cpu` | code_index_syntax.registered_parser: Python → SQLite_FTS5_BM25 → TreeSitter_LanguagePack | `Windows` | — | `code_parse_file` | no | no |
| `code_query` | `code` | `code_query.engine` | `engine_cpu` | code_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `code_read` | `code` | `code_read.engine` | `engine_cpu` | code_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `code_refresh` | `code` | `code_refresh.registered_parser` | `engine_cpu` | code_refresh.registered_parser: Python → SQLite_FTS5_BM25 → Python_structural_parser | `Windows`, `Darwin`, `Linux` | — | `code_parse_file` | no | no |
| `code_refresh_git` | `code` | `code_refresh_git.git_checkpoint` | `engine_cpu` | code_refresh_git.git_checkpoint: Python → SQLite_FTS5_BM25 → Python_structural_parser → Git | `Windows`, `Darwin`, `Linux` | — | `code_parse_git_blob`, `code_git_checkpoint` | no | no |
| `code_semantic_index` | `code` | `code_semantic_index.local_model` | `engine_cpu` | code_semantic_index.local_model: Python → SentenceTransformers | `Windows` | — | `code_embed_text` | no | no |
| `code_semantic_query` | `code` | `code_semantic_query.local_model` | `engine_cpu` | code_semantic_query.local_model: Python → SentenceTransformers | `Windows` | — | `code_embed_text` | no | no |
| `code_semantic_query_faiss` | `code` | `code_semantic_query_faiss.local_model` | `engine_cpu` | code_semantic_query_faiss.local_model: Python → SentenceTransformers → FAISS_CPU | `Windows` | — | `code_embed_text` | no | no |
| `code_semantic_query_vec` | `code` | `code_semantic_query_vec.local_model` | `engine_cpu` | code_semantic_query_vec.local_model: Python → SentenceTransformers → sqlite_vec | `Windows` | — | `code_embed_text` | no | no |
| `code_snapshot_summary` | `code` | `code_snapshot_summary.engine` | `engine_cpu` | code_snapshot_summary.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `connector_configure` | `extensions` | `connector_configure.engine` | `engine_cpu` | connector_configure.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `connector_read` | `extensions` | `connector_read.engine` | `engine_cpu` | connector_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `connector_revoke` | `extensions` | `connector_revoke.engine` | `engine_cpu` | connector_revoke.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `context_index_query` | `memory` | `context_index.pinecone`, `context_index.weaviate`, `context_index.milvus`, `context_index.opensearch` | `engine_cpu` | context_index.pinecone: Python → HTTPX → Pinecone<br>context_index.weaviate: Python → HTTPX → Weaviate<br>context_index.milvus: Python → HTTPX → Milvus<br>context_index.opensearch: Python → HTTPX → OpenSearch | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `context_index_sync` | `memory` | `context_index.pinecone`, `context_index.weaviate`, `context_index.milvus`, `context_index.opensearch` | `engine_cpu` | context_index.pinecone: Python → HTTPX → Pinecone<br>context_index.weaviate: Python → HTTPX → Weaviate<br>context_index.milvus: Python → HTTPX → Milvus<br>context_index.opensearch: Python → HTTPX → OpenSearch | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `continuation_accept` | `continuity` | `continuation_accept.engine` | `engine_cpu` | continuation_accept.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `continuation_cancel` | `continuity` | `continuation_cancel.engine` | `engine_cpu` | continuation_cancel.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `continuation_context` | `continuity` | `continuation_context.engine` | `engine_cpu` | continuation_context.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `continuation_offer` | `continuity` | `continuation_offer.engine` | `engine_cpu` | continuation_offer.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `continuation_read` | `continuity` | `continuation_read.engine` | `engine_cpu` | continuation_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `continuation_recovery_cancel` | `recovery` | `continuation_recovery_cancel.engine` | `engine_cpu` | continuation_recovery_cancel.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `continuation_recovery_offer` | `recovery` | `continuation_recovery_offer.engine` | `engine_cpu` | continuation_recovery_offer.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `custom_current` | `custom` | `custom_current.engine` | `engine_cpu` | custom_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `custom_index` | `custom` | `custom_index.sqlite_sqlalchemy`, `custom_index.native`, `custom_index.calamine`, `custom_index.presentation`, `custom_index.arrow`, `custom_index.pdf_pymupdf`, `custom_index.pdf_pypdf`, `custom_index.pdf_pdfplumber` | `engine_cpu` | custom_index.sqlite_sqlalchemy: Python → SQLAlchemy<br>custom_index.native: Python<br>custom_index.calamine: Python → python_calamine<br>custom_index.presentation: Python → PPTX_OpenXML<br>custom_index.arrow: Python → pyarrow<br>custom_index.pdf_pymupdf: Python → pypdf → PyMuPDF<br>custom_index.pdf_pypdf: Python → pypdf<br>custom_index.pdf_pdfplumber: Python → pypdf → pdfplumber | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `custom_index_media` | `custom` | `custom_index_media.raster`, `custom_index_media.vector`, `custom_index_media.media` | `engine_cpu` | custom_index_media.raster: Python → Pillow<br>custom_index_media.vector: Python → defusedxml<br>custom_index_media.media: Python → FFmpeg | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `custom_lane_configure` | `sources` | `custom_lane_configure.engine` | `engine_cpu` | custom_lane_configure.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `custom_lanes_read` | `sources` | `custom_lanes_read.engine` | `engine_cpu` | custom_lanes_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `custom_query` | `custom` | `custom_query.engine` | `engine_cpu` | custom_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `custom_read` | `custom` | `custom_read.engine` | `engine_cpu` | custom_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `custom_refresh` | `custom` | `custom_refresh.sqlite_sqlalchemy`, `custom_refresh.native`, `custom_refresh.calamine`, `custom_refresh.presentation`, `custom_refresh.arrow`, `custom_refresh.pdf_pymupdf`, `custom_refresh.pdf_pypdf`, `custom_refresh.pdf_pdfplumber` | `engine_cpu` | custom_refresh.sqlite_sqlalchemy: Python → SQLAlchemy<br>custom_refresh.native: Python<br>custom_refresh.calamine: Python → python_calamine<br>custom_refresh.presentation: Python → PPTX_OpenXML<br>custom_refresh.arrow: Python → pyarrow<br>custom_refresh.pdf_pymupdf: Python → pypdf → PyMuPDF<br>custom_refresh.pdf_pypdf: Python → pypdf<br>custom_refresh.pdf_pdfplumber: Python → pypdf → pdfplumber | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `custom_refresh_media` | `custom` | `custom_refresh_media.raster`, `custom_refresh_media.vector`, `custom_refresh_media.media` | `engine_cpu` | custom_refresh_media.raster: Python → Pillow<br>custom_refresh_media.vector: Python → defusedxml<br>custom_refresh_media.media: Python → FFmpeg | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `data_current` | `data` | `data_current.engine` | `engine_cpu` | data_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `data_export` | `data` | `data_export.native`, `data_export.native_view`, `data_export.native_arrow`, `data_export.native_arrow_view` | `engine_cpu` | data_export.native: Python<br>data_export.native_view: Python → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>data_export.native_arrow: Python → pyarrow<br>data_export.native_arrow_view: Python → pyarrow → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_content`, `render_lane_view` | no | no |
| `data_generate` | `data` | `data_generate.native` | `engine_cpu` | data_generate.native: Python | `Windows`, `Darwin`, `Linux` | — | `data_generate` | no | no |
| `data_index` | `data` | `data_index.native` | `engine_cpu` | data_index.native: Python | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_file` | no | no |
| `data_index_arrow` | `data` | `data_index_arrow.native` | `engine_cpu` | data_index_arrow.native: Python → pyarrow | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_file` | no | no |
| `data_inspect_duckdb` | `data` | `data_inspect_duckdb.library` | `engine_cpu` | data_inspect_duckdb.library: Python → DuckDB → pyarrow | `Windows`, `Darwin`, `Linux` | — | `tabular_inspect` | no | no |
| `data_inspect_pandas` | `data` | `data_inspect_pandas.library` | `engine_cpu` | data_inspect_pandas.library: Python → pandas | `Windows`, `Darwin`, `Linux` | — | `tabular_inspect` | no | no |
| `data_inspect_polars` | `data` | `data_inspect_polars.library` | `engine_cpu` | data_inspect_polars.library: Python → Polars | `Windows`, `Darwin`, `Linux` | — | `tabular_inspect` | no | no |
| `data_inspection_read` | `data` | `data_inspection_read.engine` | `engine_cpu` | data_inspection_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `data_query` | `data` | `data_query.engine` | `engine_cpu` | data_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `data_read` | `data` | `data_read.engine` | `engine_cpu` | data_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `data_refresh` | `data` | `data_refresh.native` | `engine_cpu` | data_refresh.native: Python | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_file` | no | no |
| `data_refresh_arrow` | `data` | `data_refresh_arrow.native` | `engine_cpu` | data_refresh_arrow.native: Python → pyarrow | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_file` | no | no |
| `data_transform` | `data` | `data_transform.engine` | `engine_cpu` | data_transform.engine: Python | `Windows`, `Darwin`, `Linux` | — | `data_transform` | no | no |
| `delta_enter` | `delta` | `delta_enter.engine` | `engine_cpu` | delta_enter.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `delta_enter_planned` | `delta` | `delta_enter_planned.engine` | `engine_cpu` | delta_enter_planned.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `delta_query` | `delta` | `delta_query.engine` | `engine_cpu` | delta_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `delta_status` | `delta` | `delta_status.engine` | `engine_cpu` | delta_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `document_convert` | `document` | `document_convert.shared_office` | `engine_cpu` | document_convert.shared_office: Python → LibreOffice → DOCX_OpenXML | `Windows` | — | `document_convert` | no | no |
| `document_current` | `document` | `document_current.engine` | `engine_cpu` | document_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `document_edit` | `document` | `document_edit.native_document` | `engine_cpu` | document_edit.native_document: Python → lxml | `Windows`, `Darwin`, `Linux` | — | `document_edit` | no | no |
| `document_enrich` | `document` | `document_enrich.docling` | `engine_cpu` | document_enrich.docling: Python → Docling | `Windows` | — | `document_enrich` | no | no |
| `document_enrichment_read` | `document` | `document_enrichment_read.engine` | `engine_cpu` | document_enrichment_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `document_export` | `document` | `document_export.native`, `document_export.native_view` | `engine_cpu` | document_export.native: Python<br>document_export.native_view: Python → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows`, `Darwin`, `Linux` | — | `document_parse_content`, `render_lane_view` | no | no |
| `document_generate` | `document` | `document_generate.native_document` | `engine_cpu` | document_generate.native_document: Python → DOCX_OpenXML | `Windows`, `Darwin`, `Linux` | — | `document_generate` | no | no |
| `document_index` | `document` | `document_index.engine` | `engine_cpu` | document_index.engine: Python | `Windows`, `Darwin`, `Linux` | — | `document_parse_file` | no | no |
| `document_query` | `document` | `document_query.engine` | `engine_cpu` | document_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `document_read` | `document` | `document_read.engine` | `engine_cpu` | document_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `document_refresh` | `document` | `document_refresh.engine` | `engine_cpu` | document_refresh.engine: Python | `Windows`, `Darwin`, `Linux` | — | `document_parse_file` | no | no |
| `document_render` | `document` | `document_render.shared_office` | `engine_cpu` | document_render.shared_office: Python → LibreOffice → pypdfium2 | `Windows` | — | `document_render` | no | no |
| `document_render_read` | `document` | `document_render_read.engine` | `engine_cpu` | document_render_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `engine_health` | `core` | `engine_health.engine` | `engine_cpu` | engine_health.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `enroll_project` | `code` | `enroll_project.exact_git` | `engine_cpu` | enroll_project.exact_git: Python → SQLite_FTS5_BM25 → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `env_uop_inspect` | `core` | `env_uop_inspect.engine` | `engine_cpu` | env_uop_inspect.engine: Python → SQLite_FTS5_BM25 → rank_bm25 | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `evaluation_feedback_export` | `core` | `evaluation_feedback_export.langsmith` | `engine_cpu` | evaluation_feedback_export.langsmith: Python → HTTPX → LangSmith | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `fetch` | `code` | `fetch.engine` | `engine_cpu` | fetch.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `git_branch_authority` | `core` | `git_branch_authority.engine` | `engine_cpu` | git_branch_authority.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `git_restore` | `recovery` | `git_restore.engine` | `engine_cpu` | git_restore.engine: Python → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `git_restore_abandon` | `recovery` | `git_restore_abandon.engine` | `engine_cpu` | git_restore_abandon.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `git_restore_preview` | `recovery` | `git_restore_preview.engine` | `engine_cpu` | git_restore_preview.engine: Python → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `git_restore_reconcile` | `recovery` | `git_restore_reconcile.engine` | `engine_cpu` | git_restore_reconcile.engine: Python → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `git_sync_selected` | `code` | `git_sync_selected.exact_git` | `engine_cpu` | git_sync_selected.exact_git: Python → SQLite_FTS5_BM25 → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `github_repository_inspect` | `code` | `github_repository_inspect.pygithub` | `engine_cpu` | github_repository_inspect.pygithub: Python → PyGithub | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `instructions_inspect` | `core` | `instructions_inspect.engine` | `engine_cpu` | instructions_inspect.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `job_reconcile_effect` | `plan` | `job_reconcile_effect.local_or_recorded`, `job_reconcile_effect.git_readback` | `engine_cpu` | job_reconcile_effect.local_or_recorded: Python<br>job_reconcile_effect.git_readback: Python → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `job_recovery_inspect` | `plan` | `job_recovery_inspect.engine` | `engine_cpu` | job_recovery_inspect.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_catalog` | `projects` | `lane_catalog.engine` | `engine_cpu` | lane_catalog.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_configure_routes` | `sources` | `lane_configure_routes.engine` | `engine_cpu` | lane_configure_routes.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_fetch` | `projects` | `lane_fetch.engine` | `engine_cpu` | lane_fetch.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_search` | `projects` | `lane_search.sqlite_lexical`, `lane_search.rank_bm25` | `engine_cpu` | lane_search.sqlite_lexical: Python → SQLite_FTS5_BM25<br>lane_search.rank_bm25: Python → SQLite_FTS5_BM25 → rank_bm25 | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_status` | `projects` | `lane_status.engine` | `engine_cpu` | lane_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_view_catalog` | `core` | `lane_view_catalog.engine` | `engine_cpu` | lane_view_catalog.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_view_preview` | `artifacts` | `lane_view_preview.engine` | `engine_cpu` | lane_view_preview.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_view_read` | `artifacts` | `lane_view_read.engine` | `engine_cpu` | lane_view_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lane_view_refresh` | `artifacts` | `lane_view_refresh.pointer`, `lane_view_refresh.source`, `lane_view_refresh.native_dot` | `engine_cpu` | lane_view_refresh.pointer: Python<br>lane_view_refresh.source: Python → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>lane_view_refresh.native_dot: Python → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx → Graphviz_dot | `Windows`, `Darwin`, `Linux` | `codex_desktop_stable`, `codex_desktop_beta`, `codex_desktop`, `codex_cli`, `codex_vm_persistent`, `codex_vm_ephemeral` | `render_lane_view` | no | no |
| `learning_read` | `learning` | `learning_read.engine` | `engine_cpu` | learning_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `learning_record_host_memory_import` | `learning` | `learning_record_host_memory_import.engine` | `engine_cpu` | learning_record_host_memory_import.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `learning_revoke` | `learning` | `learning_revoke.engine` | `engine_cpu` | learning_revoke.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lifecycle_transition_law` | `core` | `lifecycle_transition_law.engine` | `engine_cpu` | lifecycle_transition_law.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lineage_read` | `chatlineage` | `lineage_read.engine` | `engine_cpu` | lineage_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `lineage_record` | `chatlineage` | `lineage_record.engine` | `engine_cpu` | lineage_record.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `linked_project_evidence_query` | `projects` | `linked_project_evidence_query.engine` | `engine_cpu` | linked_project_evidence_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `media_current` | `images_ocr` | `media_current.engine` | `engine_cpu` | media_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `media_export` | `images_ocr` | `media_export.raster`, `media_export.vector`, `media_export.container`, `media_export.raster_view`, `media_export.vector_view`, `media_export.container_view` | `engine_cpu` | media_export.raster: Python → Pillow<br>media_export.vector: Python → defusedxml<br>media_export.container: Python → FFmpeg<br>media_export.raster_view: Python → Pillow → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>media_export.vector_view: Python → defusedxml → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>media_export.container_view: Python → FFmpeg → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows`, `Darwin`, `Linux` | — | `media_parse_bytes`, `render_lane_view` | no | no |
| `media_extract` | `images_ocr` | `media_extract.ffmpeg_frame`, `media_extract.ffmpeg_audio` | `engine_cpu` | media_extract.ffmpeg_frame: Python → Pillow → FFmpeg<br>media_extract.ffmpeg_audio: Python → FFmpeg | `Windows` | — | `media_extract` | no | no |
| `media_extraction_read` | `images_ocr` | `media_extraction_read.engine` | `engine_cpu` | media_extraction_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `media_index` | `images_ocr` | `media_index.raster`, `media_index.vector`, `media_index.container` | `engine_cpu` | media_index.raster: Python → Pillow<br>media_index.vector: Python → defusedxml<br>media_index.container: Python → FFmpeg | `Windows`, `Darwin`, `Linux` | — | `media_parse_file` | no | no |
| `media_ocr` | `images_ocr` | `media_ocr.rapidocr`, `media_ocr.tesseract` | `engine_cpu` | media_ocr.rapidocr: Python → Pillow → OpenCV → RapidOCR_ONNX_Runtime<br>media_ocr.tesseract: Python → Pillow → pytesseract_Tesseract | `Windows`, `Darwin`, `Linux` | — | `media_ocr` | no | no |
| `media_ocr_read` | `images_ocr` | `media_ocr_read.engine` | `engine_cpu` | media_ocr_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `media_query` | `images_ocr` | `media_query.engine` | `engine_cpu` | media_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `media_read` | `images_ocr` | `media_read.engine` | `engine_cpu` | media_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `media_refresh` | `images_ocr` | `media_refresh.raster`, `media_refresh.vector`, `media_refresh.container` | `engine_cpu` | media_refresh.raster: Python → Pillow<br>media_refresh.vector: Python → defusedxml<br>media_refresh.container: Python → FFmpeg | `Windows`, `Darwin`, `Linux` | — | `media_parse_file` | no | no |
| `media_transform` | `images_ocr` | `media_transform.pillow` | `engine_cpu` | media_transform.pillow: Python → Pillow | `Windows`, `Darwin`, `Linux` | — | `media_transform` | no | no |
| `memory_checkpoint` | `memory` | `memory_checkpoint.engine` | `engine_cpu` | memory_checkpoint.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `memory_ingest` | `memory` | `memory_ingest.engine` | `engine_cpu` | memory_ingest.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `memory_read` | `memory` | `memory_read.engine` | `engine_cpu` | memory_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `memory_rehydrate` | `memory` | `memory_rehydrate.engine` | `engine_cpu` | memory_rehydrate.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `observability_export` | `core` | `observability_export.opentelemetry`, `observability_export.langfuse`, `observability_export.grafana` | `engine_cpu` | observability_export.opentelemetry: Python → HTTPX → OpenTelemetry<br>observability_export.langfuse: Python → HTTPX → Langfuse<br>observability_export.grafana: Python → HTTPX → Grafana | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `pdf_current` | `pdf_ocr` | `pdf_current.engine` | `engine_cpu` | pdf_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `pdf_edit` | `pdf_ocr` | `pdf_edit.native_pdf` | `engine_cpu` | pdf_edit.native_pdf: Python → pypdf → PyMuPDF → pdfplumber | `Windows`, `Darwin`, `Linux` | — | `pdf_edit` | no | no |
| `pdf_enrich` | `pdf_ocr` | `pdf_enrich.docling` | `engine_cpu` | pdf_enrich.docling: Python → Docling → pypdf | `Windows` | — | `pdf_enrich` | no | no |
| `pdf_enrichment_read` | `pdf_ocr` | `pdf_enrichment_read.engine` | `engine_cpu` | pdf_enrichment_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `pdf_export` | `pdf_ocr` | `pdf_export.pymupdf`, `pdf_export.pdfplumber`, `pdf_export.pypdf`, `pdf_export.poppler`, `pdf_export.pymupdf_view`, `pdf_export.pdfplumber_view`, `pdf_export.pypdf_view`, `pdf_export.poppler_view` | `engine_cpu` | pdf_export.pymupdf: Python → pypdf → PyMuPDF → pdfplumber<br>pdf_export.pdfplumber: Python → pypdf → pdfplumber<br>pdf_export.pypdf: Python → pypdf → pdfplumber<br>pdf_export.poppler: Python → pypdf → pdfplumber → Poppler_pdftotext_pdfinfo<br>pdf_export.pymupdf_view: Python → pypdf → PyMuPDF → pdfplumber → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>pdf_export.pdfplumber_view: Python → pypdf → pdfplumber → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>pdf_export.pypdf_view: Python → pypdf → pdfplumber → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>pdf_export.poppler_view: Python → pypdf → pdfplumber → Poppler_pdftotext_pdfinfo → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows`, `Darwin`, `Linux` | — | `pdf_parse_bytes`, `render_lane_view` | no | no |
| `pdf_generate` | `pdf_ocr` | `pdf_generate.native_pdf` | `engine_cpu` | pdf_generate.native_pdf: Python → ReportLab → pypdf → PyMuPDF → pdfplumber → Pillow | `Windows`, `Darwin`, `Linux` | — | `pdf_generate` | no | no |
| `pdf_index` | `pdf_ocr` | `pdf_index.native_pdf`, `pdf_index.pdfplumber`, `pdf_index.pypdf`, `pdf_index.poppler` | `engine_cpu` | pdf_index.native_pdf: Python → pypdf → PyMuPDF → pdfplumber<br>pdf_index.pdfplumber: Python → pypdf → pdfplumber<br>pdf_index.pypdf: Python → pypdf → pdfplumber<br>pdf_index.poppler: Python → pypdf → pdfplumber → Poppler_pdftotext_pdfinfo | `Windows`, `Darwin`, `Linux` | — | `pdf_parse_file` | no | no |
| `pdf_ocr` | `pdf_ocr` | `pdf_ocr.rapidocr`, `pdf_ocr.tesseract` | `engine_cpu` | pdf_ocr.rapidocr: Python → pypdf → pypdfium2 → Pillow → OpenCV → RapidOCR_ONNX_Runtime<br>pdf_ocr.tesseract: Python → pypdf → pypdfium2 → Pillow → pytesseract_Tesseract | `Windows`, `Darwin`, `Linux` | — | `pdf_ocr` | no | no |
| `pdf_ocr_read` | `pdf_ocr` | `pdf_ocr_read.engine` | `engine_cpu` | pdf_ocr_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `pdf_query` | `pdf_ocr` | `pdf_query.engine` | `engine_cpu` | pdf_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `pdf_read` | `pdf_ocr` | `pdf_read.engine` | `engine_cpu` | pdf_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `pdf_refresh` | `pdf_ocr` | `pdf_refresh.native_pdf`, `pdf_refresh.pdfplumber`, `pdf_refresh.pypdf`, `pdf_refresh.poppler` | `engine_cpu` | pdf_refresh.native_pdf: Python → pypdf → PyMuPDF → pdfplumber<br>pdf_refresh.pdfplumber: Python → pypdf → pdfplumber<br>pdf_refresh.pypdf: Python → pypdf → pdfplumber<br>pdf_refresh.poppler: Python → pypdf → pdfplumber → Poppler_pdftotext_pdfinfo | `Windows`, `Darwin`, `Linux` | — | `pdf_parse_file` | no | no |
| `pdf_render` | `pdf_ocr` | `pdf_render.pdfium` | `engine_cpu` | pdf_render.pdfium: Python → pypdfium2 → pypdf → Pillow | `Windows`, `Darwin`, `Linux` | — | `pdf_render` | no | no |
| `pdf_render_read` | `pdf_ocr` | `pdf_render_read.engine` | `engine_cpu` | pdf_render_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `plan_create` | `plan` | `plan_create.engine` | `engine_cpu` | plan_create.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `plan_host_bind` | `plan` | `plan_host_bind.engine` | `engine_cpu` | plan_host_bind.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `plan_host_status` | `plan` | `plan_host_status.engine` | `engine_cpu` | plan_host_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `plan_host_sync` | `plan` | `plan_host_sync.engine` | `engine_cpu` | plan_host_sync.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `plan_read` | `plan` | `plan_read.engine` | `engine_cpu` | plan_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `plan_refresh` | `plan` | `plan_refresh.engine` | `engine_cpu` | plan_refresh.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `powerbi_current` | `power_bi` | `powerbi_current.engine` | `engine_cpu` | powerbi_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `powerbi_edit` | `power_bi` | `powerbi_edit.native` | `engine_cpu` | powerbi_edit.native: Python → PowerBI_TOM → JSONSchema | `Windows` | — | `powerbi_edit` | no | no |
| `powerbi_export` | `power_bi` | `powerbi_export.native`, `powerbi_export.view` | `engine_cpu` | powerbi_export.native: Python → PowerBI_TOM → PBIXRay → JSONSchema<br>powerbi_export.view: Python → PowerBI_TOM → PBIXRay → JSONSchema → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows` | — | `powerbi_parse_content`, `render_lane_view` | no | no |
| `powerbi_generate` | `power_bi` | `powerbi_generate.native` | `engine_cpu` | powerbi_generate.native: Python → PowerBI_TOM → JSONSchema | `Windows` | — | `powerbi_generate` | no | no |
| `powerbi_index` | `power_bi` | `powerbi_index.native` | `engine_cpu` | powerbi_index.native: Python → PowerBI_TOM → PBIXRay → JSONSchema | `Windows` | — | `powerbi_parse_file` | no | no |
| `powerbi_query` | `power_bi` | `powerbi_query.engine` | `engine_cpu` | powerbi_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `powerbi_read` | `power_bi` | `powerbi_read.engine` | `engine_cpu` | powerbi_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `powerbi_refresh` | `power_bi` | `powerbi_refresh.native` | `engine_cpu` | powerbi_refresh.native: Python → PowerBI_TOM → PBIXRay → JSONSchema | `Windows` | — | `powerbi_parse_file` | no | no |
| `powerbi_schema` | `power_bi` | `powerbi_schema.engine` | `engine_cpu` | powerbi_schema.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `presentation_convert` | `presentation` | `presentation_convert.shared_office` | `engine_cpu` | presentation_convert.shared_office: Python → LibreOffice → PPTX_OpenXML | `Windows` | — | `presentation_convert` | no | no |
| `presentation_current` | `presentation` | `presentation_current.engine` | `engine_cpu` | presentation_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `presentation_edit` | `presentation` | `presentation_edit.native_presentation` | `engine_cpu` | presentation_edit.native_presentation: Python → lxml | `Windows`, `Darwin`, `Linux` | — | `presentation_edit` | no | no |
| `presentation_enrich` | `presentation` | `presentation_enrich.docling` | `engine_cpu` | presentation_enrich.docling: Python → Docling | `Windows` | — | `presentation_enrich` | no | no |
| `presentation_enrichment_read` | `presentation` | `presentation_enrichment_read.engine` | `engine_cpu` | presentation_enrichment_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `presentation_export` | `presentation` | `presentation_export.native`, `presentation_export.native_view` | `engine_cpu` | presentation_export.native: Python<br>presentation_export.native_view: Python → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows`, `Darwin`, `Linux` | — | `presentation_parse_content`, `render_lane_view` | no | no |
| `presentation_generate` | `presentation` | `presentation_generate.native_presentation` | `engine_cpu` | presentation_generate.native_presentation: Python → PPTX_OpenXML → Pillow → lxml | `Windows`, `Darwin`, `Linux` | — | `presentation_generate` | no | no |
| `presentation_index` | `presentation` | `presentation_index.engine` | `engine_cpu` | presentation_index.engine: Python | `Windows`, `Darwin`, `Linux` | — | `presentation_parse_file` | no | no |
| `presentation_query` | `presentation` | `presentation_query.engine` | `engine_cpu` | presentation_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `presentation_read` | `presentation` | `presentation_read.engine` | `engine_cpu` | presentation_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `presentation_refresh` | `presentation` | `presentation_refresh.engine` | `engine_cpu` | presentation_refresh.engine: Python | `Windows`, `Darwin`, `Linux` | — | `presentation_parse_file` | no | no |
| `presentation_render` | `presentation` | `presentation_render.shared_office` | `engine_cpu` | presentation_render.shared_office: Python → LibreOffice → pypdfium2 | `Windows` | — | `presentation_render` | no | no |
| `presentation_render_read` | `presentation` | `presentation_render_read.engine` | `engine_cpu` | presentation_render_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_backup` | `recovery` | `project_backup.engine` | `engine_cpu` | project_backup.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_backup_verify` | `recovery` | `project_backup_verify.engine` | `engine_cpu` | project_backup_verify.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_catalog` | `core` | `project_catalog.engine` | `engine_cpu` | project_catalog.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_deselect` | `core` | `project_deselect.engine` | `engine_cpu` | project_deselect.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_heads_compare` | `projects` | `project_evidence_heads_compare.engine` | `engine_cpu` | project_evidence_heads_compare.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_history` | `projects` | `project_evidence_history.engine` | `engine_cpu` | project_evidence_history.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_link` | `universe` | `project_evidence_link.engine` | `engine_cpu` | project_evidence_link.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_links_read` | `universe` | `project_evidence_links_read.engine` | `engine_cpu` | project_evidence_links_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_links_verify` | `universe` | `project_evidence_links_verify.engine` | `engine_cpu` | project_evidence_links_verify.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_map_inspect` | `universe` | `project_evidence_map_inspect.engine` | `engine_cpu` | project_evidence_map_inspect.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_map_query` | `universe` | `project_evidence_map_query.engine` | `engine_cpu` | project_evidence_map_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_network_create` | `universe` | `project_evidence_network_create.engine` | `engine_cpu` | project_evidence_network_create.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_network_grant` | `universe` | `project_evidence_network_grant.engine` | `engine_cpu` | project_evidence_network_grant.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_network_link` | `universe` | `project_evidence_network_link.engine` | `engine_cpu` | project_evidence_network_link.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_network_read` | `universe` | `project_evidence_network_read.engine` | `engine_cpu` | project_evidence_network_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_network_register` | `universe` | `project_evidence_network_register.engine` | `engine_cpu` | project_evidence_network_register.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_network_revoke` | `universe` | `project_evidence_network_revoke.engine` | `engine_cpu` | project_evidence_network_revoke.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_network_verify` | `universe` | `project_evidence_network_verify.engine` | `engine_cpu` | project_evidence_network_verify.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_select` | `core` | `project_evidence_select.engine` | `engine_cpu` | project_evidence_select.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_evidence_unlink` | `universe` | `project_evidence_unlink.engine` | `engine_cpu` | project_evidence_unlink.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_memory_record_link` | `memory` | `project_memory_record_link.engine` | `engine_cpu` | project_memory_record_link.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_recovery_inspect` | `recovery` | `project_recovery_inspect.engine` | `engine_cpu` | project_recovery_inspect.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_register` | `core` | `project_register.engine` | `engine_cpu` | project_register.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_select` | `core` | `project_select.engine` | `engine_cpu` | project_select.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_status` | `core` | `project_status.engine` | `engine_cpu` | project_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_work_classify` | `core` | `project_work_classify.engine` | `engine_cpu` | project_work_classify.engine: Python → ENV_UOP_classifier | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `project_workflow_configure` | `core` | `project_workflow_configure.git`, `project_workflow_configure.content` | `engine_cpu` | project_workflow_configure.git: Python → Git<br>project_workflow_configure.content: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `prompt_index_status` | `chat_lineage` | `prompt_index_status.engine` | `engine_cpu` | prompt_index_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `remote_git_action_read` | `core` | `remote_git_action_read.engine` | `engine_cpu` | remote_git_action_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `remote_git_execute_push` | `code` | `remote_git_execute_push.exact_git` | `engine_cpu` | remote_git_execute_push.exact_git: Python → SQLite_FTS5_BM25 → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `remote_git_prepare_push` | `code` | `remote_git_prepare_push.exact_git` | `engine_cpu` | remote_git_prepare_push.exact_git: Python → SQLite_FTS5_BM25 → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `render_project_panel` | `core` | `render_project_panel.engine` | `engine_cpu` | render_project_panel.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `render_runtime_panel` | `core` | `render_runtime_panel.engine` | `engine_cpu` | render_runtime_panel.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `research_current` | `research` | `research_current.engine` | `engine_cpu` | research_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `research_index` | `research` | `research_index.sqlite_sqlalchemy`, `research_index.native`, `research_index.calamine`, `research_index.presentation`, `research_index.arrow`, `research_index.pdf_pymupdf`, `research_index.pdf_pypdf`, `research_index.pdf_pdfplumber` | `engine_cpu` | research_index.sqlite_sqlalchemy: Python → SQLAlchemy<br>research_index.native: Python<br>research_index.calamine: Python → python_calamine<br>research_index.presentation: Python → PPTX_OpenXML<br>research_index.arrow: Python → pyarrow<br>research_index.pdf_pymupdf: Python → pypdf → PyMuPDF<br>research_index.pdf_pypdf: Python → pypdf<br>research_index.pdf_pdfplumber: Python → pypdf → pdfplumber | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `research_index_media` | `research` | `research_index_media.raster`, `research_index_media.vector`, `research_index_media.media` | `engine_cpu` | research_index_media.raster: Python → Pillow<br>research_index_media.vector: Python → defusedxml<br>research_index_media.media: Python → FFmpeg | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `research_query` | `research` | `research_query.engine` | `engine_cpu` | research_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `research_read` | `research` | `research_read.engine` | `engine_cpu` | research_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `research_refresh` | `research` | `research_refresh.sqlite_sqlalchemy`, `research_refresh.native`, `research_refresh.calamine`, `research_refresh.presentation`, `research_refresh.arrow`, `research_refresh.pdf_pymupdf`, `research_refresh.pdf_pypdf`, `research_refresh.pdf_pdfplumber` | `engine_cpu` | research_refresh.sqlite_sqlalchemy: Python → SQLAlchemy<br>research_refresh.native: Python<br>research_refresh.calamine: Python → python_calamine<br>research_refresh.presentation: Python → PPTX_OpenXML<br>research_refresh.arrow: Python → pyarrow<br>research_refresh.pdf_pymupdf: Python → pypdf → PyMuPDF<br>research_refresh.pdf_pypdf: Python → pypdf<br>research_refresh.pdf_pdfplumber: Python → pypdf → pdfplumber | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `research_refresh_media` | `research` | `research_refresh_media.raster`, `research_refresh_media.vector`, `research_refresh_media.media` | `engine_cpu` | research_refresh_media.raster: Python → Pillow<br>research_refresh_media.vector: Python → defusedxml<br>research_refresh_media.media: Python → FFmpeg | `Windows`, `Darwin`, `Linux` | — | `sector_evidence_parse_file` | no | no |
| `research_web_capture` | `research` | `research_web_capture.httpx`, `research_web_capture.requests` | `engine_cpu` | research_web_capture.httpx: Python → HTTPX → validators → tldextract<br>research_web_capture.requests: Python → Requests → validators → tldextract | `Windows`, `Darwin`, `Linux` | — | `research_web_capture_source` | no | no |
| `research_web_discover` | `research` | `research_web_discover.ddgs` | `engine_cpu` | research_web_discover.ddgs: Python → DDGS → HTTPX → lxml | `Windows`, `Darwin`, `Linux` | — | `research_discover_sources` | no | no |
| `research_web_extract` | `research` | `research_web_extract.trafilatura`, `research_web_extract.readability`, `research_web_extract.beautifulsoup`, `research_web_extract.markdownify`, `research_web_extract.html2text`, `research_web_extract.stdlib` | `engine_cpu` | research_web_extract.trafilatura: Python → trafilatura → BeautifulSoup4 → lxml<br>research_web_extract.readability: Python → readability_lxml → BeautifulSoup4 → lxml<br>research_web_extract.beautifulsoup: Python → BeautifulSoup4 → lxml<br>research_web_extract.markdownify: Python → markdownify → BeautifulSoup4 → lxml<br>research_web_extract.html2text: Python → html2text → BeautifulSoup4 → lxml<br>research_web_extract.stdlib: Python | `Windows`, `Darwin`, `Linux` | — | `research_web_extract_source` | no | no |
| `restoration_read` | `recovery` | `restoration_read.engine` | `engine_cpu` | restoration_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `restoration_reindex` | `recovery` | `restoration_reindex.engine` | `engine_cpu` | restoration_reindex.engine: Python → Git | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `runtime_doctor` | `core` | `runtime_doctor.engine` | `engine_cpu` | runtime_doctor.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `runtime_status` | `core` | `runtime_status.engine` | `engine_cpu` | runtime_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `search` | `projects` | `search.sqlite_lexical`, `search.rank_bm25` | `engine_cpu` | search.sqlite_lexical: Python → SQLite_FTS5_BM25<br>search.rank_bm25: Python → SQLite_FTS5_BM25 → rank_bm25 | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `session_boot` | `sessions` | `session_boot.engine` | `engine_cpu` | session_boot.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `session_context` | `sessions` | `session_context.engine` | `engine_cpu` | session_context.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `session_exit` | `sessions` | `session_exit.engine` | `engine_cpu` | session_exit.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `session_exit_boundary` | `receipts` | `session_exit_boundary.engine` | `engine_cpu` | session_exit_boundary.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `session_flash_status` | `core` | `session_flash_status.engine` | `engine_cpu` | session_flash_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `session_resume` | `sessions` | `session_resume.engine` | `engine_cpu` | session_resume.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `session_status` | `sessions` | `session_status.engine` | `engine_cpu` | session_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_classify` | `sources` | `source_classify.engine` | `engine_cpu` | source_classify.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_crosswalk` | `sources` | `source_crosswalk.engine` | `engine_cpu` | source_crosswalk.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_git_history` | `sources` | `source_git_history.engine` | `engine_cpu` | source_git_history.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_git_impact` | `sources` | `source_git_impact.engine` | `engine_cpu` | source_git_impact.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_graph` | `sources` | `source_graph.engine` | `engine_cpu` | source_graph.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_graph_diff` | `sources` | `source_graph_diff.engine` | `engine_cpu` | source_graph_diff.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_graph_impact` | `sources` | `source_graph_impact.engine` | `engine_cpu` | source_graph_impact.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_identity` | `sources` | `source_identity.engine` | `engine_cpu` | source_identity.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_inspect_sqlite` | `sources` | `source_inspect_sqlite.engine` | `engine_cpu` | source_inspect_sqlite.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_materialization_read` | `sources` | `source_materialization_read.engine` | `engine_cpu` | source_materialization_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_materialization_reconcile` | `sources` | `source_materialization_reconcile.engine` | `engine_cpu` | source_materialization_reconcile.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_materialize` | `sources` | `source_materialize.engine` | `engine_cpu` | source_materialize.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_preparation_read` | `sources` | `source_preparation_read.engine` | `engine_cpu` | source_preparation_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_prepare_refresh` | `sources` | `source_prepare_refresh.engine` | `engine_cpu` | source_prepare_refresh.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_prepare_tasks` | `sources` | `source_prepare_tasks.engine` | `engine_cpu` | source_prepare_tasks.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_read` | `sources` | `source_read.engine` | `engine_cpu` | source_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_reconcile_archives` | `sources` | `source_reconcile_archives.engine` | `engine_cpu` | source_reconcile_archives.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_register` | `sources` | `source_register.engine` | `engine_cpu` | source_register.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_routes_read` | `sources` | `source_routes_read.engine` | `engine_cpu` | source_routes_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_schema_configure` | `sources` | `source_schema_configure.engine` | `engine_cpu` | source_schema_configure.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_schema_map` | `sources` | `source_schema_map.engine` | `engine_cpu` | source_schema_map.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_snapshot_retire` | `sources` | `source_selector.local_files`, `source_selector.git_checkpoint` | `engine_cpu` | source_selector.local_files: Python<br>source_selector.git_checkpoint: Python → Git | `Windows`, `Darwin`, `Linux` | — | `code_git_checkpoint` | no | no |
| `source_snapshot_state` | `sources` | `source_snapshot_state.engine` | `engine_cpu` | source_snapshot_state.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `source_verify` | `sources` | `source_verify.engine` | `engine_cpu` | source_verify.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `spreadsheet_current` | `spreadsheet` | `spreadsheet_current.engine` | `engine_cpu` | spreadsheet_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `spreadsheet_edit` | `spreadsheet` | `spreadsheet_edit.native` | `engine_cpu` | spreadsheet_edit.native: Python → lxml | `Windows`, `Darwin`, `Linux` | — | `spreadsheet_edit` | no | no |
| `spreadsheet_export` | `spreadsheet` | `spreadsheet_export.native`, `spreadsheet_export.native_view`, `spreadsheet_export.native_values`, `spreadsheet_export.native_values_view` | `engine_cpu` | spreadsheet_export.native: Python<br>spreadsheet_export.native_view: Python → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx<br>spreadsheet_export.native_values: Python → python_calamine<br>spreadsheet_export.native_values_view: Python → python_calamine → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_content`, `render_lane_view` | no | no |
| `spreadsheet_generate` | `spreadsheet` | `spreadsheet_generate.native` | `engine_cpu` | spreadsheet_generate.native: Python → openpyxl | `Windows`, `Darwin`, `Linux` | — | `spreadsheet_generate` | no | no |
| `spreadsheet_index` | `spreadsheet` | `spreadsheet_index.native` | `engine_cpu` | spreadsheet_index.native: Python | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_file` | no | no |
| `spreadsheet_index_values` | `spreadsheet` | `spreadsheet_index_values.native` | `engine_cpu` | spreadsheet_index_values.native: Python → python_calamine | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_file` | no | no |
| `spreadsheet_inspect_openpyxl` | `spreadsheet` | `spreadsheet_inspect_openpyxl.library` | `engine_cpu` | spreadsheet_inspect_openpyxl.library: Python → openpyxl | `Windows`, `Darwin`, `Linux` | — | `tabular_inspect` | no | no |
| `spreadsheet_inspect_pandas` | `spreadsheet` | `spreadsheet_inspect_pandas.library` | `engine_cpu` | spreadsheet_inspect_pandas.library: Python → pandas | `Windows`, `Darwin`, `Linux` | — | `tabular_inspect` | no | no |
| `spreadsheet_inspection_read` | `spreadsheet` | `spreadsheet_inspection_read.engine` | `engine_cpu` | spreadsheet_inspection_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `spreadsheet_query` | `spreadsheet` | `spreadsheet_query.engine` | `engine_cpu` | spreadsheet_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `spreadsheet_read` | `spreadsheet` | `spreadsheet_read.engine` | `engine_cpu` | spreadsheet_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `spreadsheet_recalculate` | `spreadsheet` | `spreadsheet_recalculate.calc` | `engine_cpu` | spreadsheet_recalculate.calc: Python → LibreOffice | `Windows` | — | `spreadsheet_recalculate` | no | no |
| `spreadsheet_refresh` | `spreadsheet` | `spreadsheet_refresh.native` | `engine_cpu` | spreadsheet_refresh.native: Python | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_file` | no | no |
| `spreadsheet_refresh_values` | `spreadsheet` | `spreadsheet_refresh_values.native` | `engine_cpu` | spreadsheet_refresh_values.native: Python → python_calamine | `Windows`, `Darwin`, `Linux` | — | `tabular_parse_file` | no | no |
| `spreadsheet_render` | `spreadsheet` | `spreadsheet_render.calc` | `engine_cpu` | spreadsheet_render.calc: Python → LibreOffice → pypdfium2 | `Windows` | — | `spreadsheet_render` | no | no |
| `spreadsheet_render_read` | `spreadsheet` | `spreadsheet_render_read.engine` | `engine_cpu` | spreadsheet_render_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `steer_preview` | `plan` | `steer_preview.engine` | `engine_cpu` | steer_preview.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `steer_submit` | `plan` | `steer_submit.engine` | `engine_cpu` | steer_submit.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `storage_connector_inspect` | `receipts` | `storage_connector_inspect.engine` | `engine_cpu` | storage_connector_inspect.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `storage_connector_select` | `receipts` | `storage_connector_select.engine` | `engine_cpu` | storage_connector_select.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `storage_status` | `core` | `storage_status.engine` | `engine_cpu` | storage_status.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `tableau_current` | `tableau` | `tableau_current.engine` | `engine_cpu` | tableau_current.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `tableau_edit` | `tableau` | `tableau_edit.native` | `engine_cpu` | tableau_edit.native: Python → lxml → Tableau_Hyper_API | `Windows` | — | `tableau_edit` | no | no |
| `tableau_export` | `tableau` | `tableau_export.native`, `tableau_export.view` | `engine_cpu` | tableau_export.native: Python → lxml → Tableau_Hyper_API<br>tableau_export.view: Python → lxml → Tableau_Hyper_API → LangGraph_Mermaid_engine → Python_Graphviz_DOT_engine → rustworkx | `Windows` | — | `tableau_parse_content`, `render_lane_view` | no | no |
| `tableau_generate` | `tableau` | `tableau_generate.native` | `engine_cpu` | tableau_generate.native: Python → lxml → Tableau_Hyper_API | `Windows` | — | `tableau_generate` | no | no |
| `tableau_index` | `tableau` | `tableau_index.native` | `engine_cpu` | tableau_index.native: Python → lxml → Tableau_Hyper_API | `Windows` | — | `tableau_parse_file` | no | no |
| `tableau_query` | `tableau` | `tableau_query.engine` | `engine_cpu` | tableau_query.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `tableau_read` | `tableau` | `tableau_read.engine` | `engine_cpu` | tableau_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `tableau_refresh` | `tableau` | `tableau_refresh.native` | `engine_cpu` | tableau_refresh.native: Python → lxml → Tableau_Hyper_API | `Windows` | — | `tableau_parse_file` | no | no |
| `task_classify` | `chat_lineage` | `task_classify.engine` | `engine_cpu` | task_classify.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_classify` | `canon` | `task_evidence_classify.engine` | `engine_cpu` | task_evidence_classify.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_decide` | `canon` | `task_evidence_decide.engine` | `engine_cpu` | task_evidence_decide.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_edge_bind` | `canon` | `task_evidence_edge_bind.engine` | `engine_cpu` | task_evidence_edge_bind.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_edge_register` | `canon` | `task_evidence_edge_register.engine` | `engine_cpu` | task_evidence_edge_register.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_expect` | `canon` | `task_evidence_expect.engine` | `engine_cpu` | task_evidence_expect.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_graph` | `canon` | `task_evidence_graph.engine` | `engine_cpu` | task_evidence_graph.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_inbox` | `canon` | `task_evidence_inbox.engine` | `engine_cpu` | task_evidence_inbox.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_input_request` | `canon` | `task_evidence_input_request.engine` | `engine_cpu` | task_evidence_input_request.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_inspect` | `canon` | `task_evidence_inspect.engine` | `engine_cpu` | task_evidence_inspect.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_packet_classify` | `canon` | `task_evidence_packet_classify.engine` | `engine_cpu` | task_evidence_packet_classify.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_participant_register` | `canon` | `task_evidence_participant_register.engine` | `engine_cpu` | task_evidence_participant_register.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_read` | `canon` | `task_evidence_read.engine` | `engine_cpu` | task_evidence_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_receive` | `canon` | `task_evidence_receive.engine` | `engine_cpu` | task_evidence_receive.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_result` | `canon` | `task_evidence_result.engine` | `engine_cpu` | task_evidence_result.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_send` | `canon` | `task_evidence_send.engine` | `engine_cpu` | task_evidence_send.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `task_evidence_supersede` | `canon` | `task_evidence_supersede.engine` | `engine_cpu` | task_evidence_supersede.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `toolchain_catalog` | `core` | `toolchain_catalog.engine` | `engine_cpu` | toolchain_catalog.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `toolchain_resolve` | `core` | `toolchain_resolve.engine` | `engine_cpu` | toolchain_resolve.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `validation_policy_read` | `plan` | `validation_policy_read.engine` | `engine_cpu` | validation_policy_read.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `validation_policy_set` | `plan` | `validation_policy_set.engine` | `engine_cpu` | validation_policy_set.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |
| `workflow_catalog` | `core` | `workflow_catalog.engine` | `engine_cpu` | workflow_catalog.engine: Python | `Windows`, `Darwin`, `Linux` | — | — | no | no |

## Interpretation boundary

- `installation_state: not_verified` and `adapter_execution_state: not_verified` are truthful preinstall states.
- CPU is always selected for its declared provider operation. CUDA, ROCm and DirectML require their exact compatible host probe and installed self-test.
- External services ship as adapters and require explicit configuration; their services and credentials are not redistributed.
- Ghostscript remains separately license-gated and is not selected by default.
- The managed plugin source, Studio application, persistent engine and shared toolchains have separate owners and install locations.
- Installed-native proof is recorded only after D088 installs and reads back the exact committed candidate.

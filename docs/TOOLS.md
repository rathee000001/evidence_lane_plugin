# Evidence Lane 2.2.0 tools

This is the repository-facing inventory of the declared Evidence Lane tool
surface. Each tool is listed once and pinned to the lanes or runtime surfaces
that use it. The executable lane authority remains
`plugins/evidence-lane-plugin/remote_adapter/app/_data/lane-contracts.ts` plus
the native lane catalog; this page does not create a tool, route, or authority.

Lane IDs: `github_code`, `local_code`, `chat_lineage`, `discussion`,
`analysis`, `plan`, `mode`, `docs`, `data_excel`, `ppt`, `pdf_ocr`,
`images_ocr`, `artifacts`, `custom`, `brain_loader`, `research`,
`project_engulf`, and `sqlite_brain`.

| Tool | Requirement | Lanes or runtime surfaces | Exact role |
| --- | --- | --- | --- |
| Git | required; optional for Project Engulf | `github_code`, `local_code`; optional `project_engulf`; runtime Git delivery | refs, commits, parents, blobs, changes, and reviewed delivery |
| Python | required | `github_code`, `local_code`; package runtime | polyglot projection and native server runtime |
| `hashlib` / `pathlib` | required | all 18 lanes; Plan, Canon, Learning, Memory, receipts | exact hashing and bounded path handling |
| SQLite / CAS | required | all 18 lanes; Plan, Chat Lineage, Canon, Learning, Memory | durable facts, immutable content identity, and receipts |
| SQLite FTS5 / BM25 | required | all 18 lanes and every queryable project sector | bounded indexed retrieval without loading a database or PV package into model context |
| deterministic TF-IDF | required | all 18 lanes | explicit term statistics and retrieval parity |
| Mermaid emitter | required | all 18 lanes; Plan/Canon topology | human-readable topology source |
| Graphviz DOT emitter | required | all 18 lanes; Plan/Canon topology | machine-comparable topology source |
| Mermaid CLI (`mmdc`) | optional | all 18 lanes | derived Mermaid rendering when an exact host tool is available |
| Graphviz `dot` | optional | all 18 lanes | derived DOT rendering when an exact host tool is available |
| Python structural parser | required | all structured lanes | bounded structural extraction |
| Package sealer | required | all structured lanes; candidate lifecycle | four-file lane packages, candidates, manifests, and receipts |
| Node.js / TypeScript | required | `github_code`, `local_code`; public adapter | JavaScript/TypeScript manifests, routes, and site build |
| pytest | required for code acceptance | `github_code`, `local_code`; plugin tests | executable Python contracts |
| Ruff | required for configured code gates | `github_code`, `local_code` | Python quality checks |
| MyPy | required for configured code gates | `github_code`, `local_code` | typed-source checks |
| Secret redactor | required internal component | `chat_lineage`; every source policy | credential-shaped value exclusion |
| Hash-chain writer | required internal component | `chat_lineage`; receipts | idempotent visible-turn commit and lineage continuity |
| ENV/UOP classifier | required internal component | `mode`; lifecycle entry | ordered law intersection and operator routing |
| PCM / MBA operators | required internal component | `mode`; Code execution | mode-specific execution governance |
| DOCX OpenXML | required | `docs` | hierarchy, text, relationships, and tables |
| `defusedxml` | required | `docs`, `ppt` | hardened XML parsing |
| OpenXML / CSV / JSON parser | required | `data_excel` | deterministic structural extraction |
| `openpyxl` | optional | `data_excel` | workbook fidelity |
| `pandas` | optional | `data_excel` | bounded tabular inspection |
| `python-calamine` | optional | `data_excel` | legacy Excel extraction |
| `pyarrow` | optional | `data_excel` | Parquet schema and bounded samples |
| PPTX OpenXML | required | `ppt` | slides, notes, shapes, tables, and relationships |
| `pypdf` | required | `pdf_ocr` | native PDF text and embedded-image extraction |
| `pypdfium2` | required | `pdf_ocr` | full-page PDF raster fallback |
| `pdfplumber` | required | `pdf_ocr` | structural PDF extraction |
| RapidOCR + ONNX Runtime | optional | `pdf_ocr`, `images_ocr` | local OCR |
| `pytesseract` + Tesseract | optional | `pdf_ocr`, `images_ocr` | secondary local OCR |
| Pillow | optional/required by lane | optional `pdf_ocr`; required `images_ocr` | page/image pixels and metadata |
| Poppler / Ghostscript | optional host tools | `pdf_ocr` | local PDF fallbacks; not bundled by this source tree |
| OpenCV | optional host tool | `images_ocr` | image-region preprocessing |
| Custom schema compiler | required internal component | `custom` | bounded user-defined lane contract |
| SQLite immutable URI reader | required internal component | `brain_loader`, `sqlite_brain` | read-only database/package inspection |
| Safe archive intake | required internal component | `brain_loader`, `project_engulf` | member, path, and manifest validation |
| Citation binder | required internal component | `research` | claim/source/date evidence binding |
| Project inventory | required internal component | `project_engulf` | files, components, relationships, and conflicts |
| Git detector | optional internal component | `project_engulf` | repository identity when a readable Git worktree exists |
| Compatibility mapper | required internal component | `sqlite_brain` | schema version and sector mapping |
| MCP Python SDK | required | native MCP and internal SDK surfaces | typed native tool transport and results |
| Pydantic | required | native MCP and internal SDK surfaces | request/result validation |
| HTTPX | required | explicitly configured network adapters only | bounded HTTP client transport |
| Cryptography + PyJWT | required by protected remote profiles | headless/API authentication | DPAPI-adjacent envelope and JWT verification primitives |
| PowerShell + Win32 APIs | required on Windows host profiles | installer, helper, tunnel, hook host | hidden background launch, task binding, and current-user recovery |
| ripgrep 15.2.0 | packaged pre-index helper | bounded source discovery before SQLite enrollment | exact hash-pinned file/content search; never the FTS authority |
| Next.js / React / Three.js / Framer Motion | public-adapter dependencies | documentation adapter only | static documentation, interaction, and visual rendering; no project authority |
| GitHub Actions | configured external service | reviewed branch CI | clean CI and release evidence |
| Vercel Git integration | configured external service | preview/production documentation deployment | deployment evidence only; no lifecycle or pointer authority |

## License boundary

Internal components remain governed by the Evidence Lane proprietary license.
License-bearing direct dependencies, packaged binaries, and host-only external
tools are separated in [Third-party tool licenses](THIRD_PARTY_LICENSES.md)
and the [direct dependency license audit](DEPENDENCY_LICENSE_AUDIT.md). A tool
name in this inventory is not evidence that its executable is bundled.

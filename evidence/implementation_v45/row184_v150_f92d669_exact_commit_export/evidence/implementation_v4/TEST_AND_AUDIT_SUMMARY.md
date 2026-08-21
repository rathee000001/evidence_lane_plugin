# Evidence Lane Plugin 0.3.0 completion audit

## Outcome

The current 67-member F-drive candidate is implemented, tested, packaged,
fresh-installed, and reinstalled into Codex Desktop as
`0.3.0+codex.20260726103131`. It remains a dirty, uncommitted HIL candidate.
The parent commit and remote `main` are unchanged at
`4dd109c53af461eed2a69e2f3a0d411c5cb2564e`.

The exact candidate file set totals 689,387 bytes. Its LF-terminated manifest
has SHA-256
`587C804FB5D0B80ADC7A1870E13C91D6E683BD7142FDFD51C7E0F80464DD1BE3`.
Both `evidence/implementation_v3/` and `evidence/implementation_v4/` are
excluded from the candidate seal to prevent circular evidence.

No HIL success is inferred. No real session was flashed or booted, no real
project or PV was created, no accepted pointer moved, and no commit or remote
push occurred.

## Completed local capability boundary

The one immutable registry contains exactly 18 lanes. Each lane emits its own
SQLite, MMD, DOT, pointer, tool, receipt, and manifest artifacts. Retrieval
includes FTS5/BM25 and materialized TF-IDF with deterministic repeated order.

The completion pass added and directly tested:

- DOCX hierarchy, paragraph, table, and image facts.
- XLSX workbook, sheet, range, cell, formula, cached-value, dependency, table,
  and chart facts; CSV, JSON, and JSONL structure facts.
- Real PyArrow Parquet extraction and real python-calamine legacy-Excel
  capability, with exact blockers when an engine is absent.
- PPT slide, notes, shape, text, table, image, and relationship facts.
- Native PDF extraction plus local scanned-PDF OCR.
- Local image OCR through RapidOCR and ONNX Runtime. The fixture returned
  `EVIDENCE LANE 123` at confidence `0.9996` with parser state
  `PARSED_OCR_LOCAL`.
- Read-only SQLite integrity, foreign-key, schema, FTS, table-statistics,
  compatibility, and receipt facts.
- Archive inspection without extraction or execution, including rejection of
  parent-path segments.
- Prompt/response commits and a deterministic chat state-hash chain.
- Exact source-byte preservation when parsing fails.
- Duplicate-ID, ambiguous-alias, duplicate-route, incremental-reuse,
  tombstone, and tamper checks.

The executable transition law still exposes exactly six HIL decisions:
`APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`, `ROLLBACK`, `REJECT`, and
`FAIL`. Only exact `APPROVE` promotes a candidate. Rollback remains pointer-only
among immutable accepted PV history, and bare rollback targets the session
entry PV.

## Validation

- Pytest: 42 passed, 0 failed in 254.22 seconds.
- Ruff formatting: 97 files pass.
- Ruff lint: pass.
- Mypy: 35 source files pass.
- Bandit: 0 findings; 20 validated dynamic-SQL skips are recorded.
- Locked dependency audit: 68 dependencies, 0 known vulnerabilities.
- Plugin schema: pass.
- Skill schema: pass.
- Wheel build: pass.
- Hash-required dependency installation: pass.
- Fresh wheel installation: pass.
- Fresh-wheel runtime doctor: pass.
- Installed-Codex runtime doctor: pass.
- Installed lifecycle transition law: pass, single authority true.
- Installed lane catalog: pass, exactly 18 lanes, single registry true.
- Installed STDIO MCP inventory: 31 tools.
- Source and installed command inventories: 28 each.
- Eight selected source-to-installed hashes: all match.
- Session-flash verification: pass with state `NOT_FLASHED`.

The wheel is 2,077,026 bytes with SHA-256
`34081A4EA43925918FB2C21EECF000F5596D6E9CD4F90DA9397F7C3167466774`.
The runtime engine package SHA-256 is
`3ACBCB452B85B36D86589E6D42BACBBB3FD78DB27EA9F4E2B3788A518C142D78`.

## Honest host boundary

The installed plugin cache is verified, but native command/menu and current MCP
pickup require a fresh Codex task. This task keeps its historical server
process and therefore cannot prove the new task surface.

Write-capable ChatGPT parity and live Google Drive persistence/readback also
remain unproven. No reduced substitute was represented as parity.

## Verdict

`FIX-THEN-PURSUE`, confidence `0.98`.

The local implementation and evidence gates pass. The verdict changes to
`PURSUE` when a fresh Codex task proves native command and 31-tool pickup; full
cross-host parity additionally requires a write-capable ChatGPT proof and live
Drive readback. A failed fresh-task pickup, nondeterministic repeated fixture,
or candidate-manifest mismatch would change the verdict toward `PARK` until
fixed.

Gate:
`V030_LOCAL_COMPLETION_AUDIT_PASS_SIX_OPTION_HIL_OPEN_NO_REAL_PV`.

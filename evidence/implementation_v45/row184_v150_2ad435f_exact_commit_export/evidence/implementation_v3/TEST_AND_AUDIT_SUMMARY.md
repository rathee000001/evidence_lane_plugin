# Evidence Lane Plugin 0.3.0 - test and audit summary

## Outcome

The universal eighteen-lane candidate is implemented and installed in Codex
Desktop from the F-drive working tree. It is a dirty, uncommitted HIL
candidate; the parent commit remains
`4dd109c53af461eed2a69e2f3a0d411c5cb2564e`, and the candidate was neither
committed nor pushed.

The 67-member candidate file set totals 535,036 bytes. Its exact LF-terminated
manifest has SHA-256
`6159C710767EF3C7262296A747051D521DBFF303B8833AA4743F6D65AAF29766`.
The implementation-evidence directory is deliberately excluded from that
candidate seal to avoid a circular receipt.

No HIL success is inferred. No real session was flashed or booted, no real
project or PV was created, and no accepted pointer was moved.

## Implemented boundary

One immutable registry governs 18 lanes: GitHub Code, Local Code, Chat
Lineage, Discussion, Analysis, Plan, Mode, Docs, Data / Excel / CSV, PPT,
PDF / OCR, Images / OCR, Artifacts, Custom, Brain Loader, Research, Project
Engulf, and SQLite Brain.

Every lane emits its own SQLite, MMD, DOT, pointer, tool, receipt, and manifest
artifacts. Retrieval includes FTS5/BM25 and materialized TF-IDF. The Excel
extractor preserves workbook, sheet, table, formula, cached-value, dependency,
and chart logic. PDF and image lanes record native text, image, OCR block,
line, region, and capability facts. Code lanes record symbols, imports, routes,
and dependencies.

PV1 is the normal full build. PV2 and later reuse unchanged accepted bytes,
rebuild changed and new sources, and record tombstones. Accepted PV state,
backlog state, and the session entry PV persist across prompts.

The single executable transition table exposes six HIL outcomes:
`APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`, `ROLLBACK`, `REJECT`, and
`FAIL`. Only exact `APPROVE` promotes a candidate. Rollback can move only among
immutable accepted PVs; bare rollback selects the session entry PV, explicit
rollback can travel backward or forward, and neither form rewrites source,
accepts a candidate, deletes history, or resets the monotonic next ordinal.

Acceptance checks execute only an explicit `cmd:` form through parsed fixed
arguments. Prose stays pending; shell operators, script wrappers, unresolved
executables, secrets, and uncontrolled output are rejected. Receipts preserve
redacted output and before/after source hashes.

## Validation

- Pytest: 40 passed, 0 failed.
- Ruff lint: pass.
- Ruff formatting: 52 files pass.
- Mypy: 35 source files pass.
- Bandit: 0 findings. It emitted 8 `nosec` annotation warnings for validated
  immutable registry table identifiers; these are warnings, not findings.
- Locked dependency audit: 0 known vulnerabilities.
- Plugin schema after the final cachebuster: pass.
- Skill schema after the final cachebuster: pass.
- Wheel build: pass.
- Hash-required dependency install: pass.
- Fresh wheel install with `--no-deps`: pass.
- Fresh-wheel runtime doctor: pass.
- Installed-Codex runtime doctor: pass.
- Installed single lifecycle transition law: pass.
- Installed 18-lane catalog: pass.
- Installed STDIO MCP tool inventory: 31 tools.
- Source and installed command inventories: 28 each.
- Six selected source-to-installed hashes: all match.
- Installed session hook: `FRESH`.

The release wheel is 2,064,788 bytes with SHA-256
`29BBB167A1B086999E320530498379CD66421EFFE91AB8CC2D59C8A13E1F8CB4`.
The runtime engine package SHA-256 is
`6E719237775F1DF74FE380CC63DFA4A2A922A5209E678B89EB50967CB1F55432`.

## Host and persistence boundary

Codex Desktop is installed and enabled as
`0.3.0+codex.20260726092244`. Native command-menu discovery still requires a
fresh Codex task; the installed files, hook, and MCP runtime are verified, but
this task does not claim UI pickup that it did not observe.

ChatGPT installation was not attempted. The observed account is Pro, no Secure
MCP Tunnel is available, and the accepted product requires bounded write tools;
a reduced read/fetch-only substitute was not created.

Google Drive persistence code now writes accepted PV bytes, decision receipts,
and generation-addressed immutable pointer snapshots, including rollback
state. No Drive credentials or destination are configured, so no live readback
is claimed.

ENV15/UOP15 remain outside every PV and `NOT_FLASHED`. The parent authority
packet remains `PARTIAL_INTEGRITY`; only its independently verified ENV15/UOP15
subset is accepted.

Gate:
`V030_UNIVERSAL_IMPLEMENTED_CODEX_INSTALLED_SIX_OPTION_HIL_READY_WITH_EXTERNAL_HOST_BLOCKERS_NO_REAL_PV`.

Current verdict: `FIX-THEN-PURSUE`. The local implementation and installation
checks pass, but fresh-task native command discovery, write-capable ChatGPT
parity, and live Drive readback remain unverified external boundaries.

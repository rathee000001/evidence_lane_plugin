# Evidence Lane Plugin

`evidence_lane_plugin` is the private, single-user implementation of the approved
Evidence Lane PV agentic-code HIL. The installable Codex plugin identifier is
`evidence-lane-plugin`; the GitHub repository keeps the user-selected underscore
name.

The product is a tool-only MCP plugin. It carries project continuity through
immutable SQLite project versions:

```text
accepted PVn
  -> one classified task
  -> one authorized sandbox/source state
  -> append-only visible ChatLineage
  -> deterministic engine rerun
  -> PV<n+1> candidate
  -> APPROVE | APPROVE_WITH_DELTA | MORE_RESEARCH | REJECT | FAIL
  -> pointer advances only on a valid APPROVE
```

## What is implemented

- exact Git commit/tree identity and clean-worktree checks;
- exact source bytes, hashes, encodings, file families, deterministic chunks,
  FTS, symbols, imports, routes, dependencies, and provenance in SQLite;
- the canonical ten-file PV package with authoritative Mermaid source;
- immutable candidate storage and byte-preserving accepted promotion;
- compare-and-swap accepted pointers;
- five exact HIL decisions and forbidden-transition guards;
- exact correction/research continuation and source-restored return from
  rejected/failed runs without pointer movement;
- local persistence plus a Google Drive REST adapter boundary;
- one-agent/one-task/session enforcement;
- hash-locked ENV15/UOP15 session authority with exact MMD, SQLite, render, and
  law artifacts outside every PV;
- idempotent installation-scoped flash receipts and visible flash status;
- secret-redacted, idempotent ChatLineage;
- progressive chunk/symbol/path `search`, bounded file/chunk/symbol `fetch`,
  and focused files/symbols/imports/dependencies/routes/receipt queries;
- explicit current-accepted, historical-accepted, and unaccepted-candidate
  authority labels with exact source commit/hash provenance;
- a separately gated remote Git action controller;
- optional advisory Codex hooks that do not own persistence.

## Explicit first-HIL boundaries

- private and single-user;
- Git/code lane only;
- no desktop UI, 3D telemetry, local reasoning model, mobile execution,
  production hosting, Vercel deployment, or public submission;
- no automatic remote push;
- no automatic PV promotion;
- no ENV/UOP data inside a PV, source repository patch, or remote Git write;
- no historical V1/V3 brain package in the runtime;
- no secret in source, PVs, Drive, lineage, screenshots, or normal logs.

## Bootstrap

Windows PowerShell:

```powershell
.\plugins\evidence-lane-plugin\scripts\bootstrap.ps1
```

Portable Python:

```text
python plugins/evidence-lane-plugin/scripts/bootstrap.py
```

The bootstrap installs only the exact allowlisted versions in
`requirements.lock.txt` into the plugin-local `.venv`.

## Validate

```text
plugins/evidence-lane-plugin/.venv/Scripts/python.exe -m pytest
plugins/evidence-lane-plugin/.venv/Scripts/python.exe -m evidence_lane_plugin.cli doctor
```

The architecture, state transitions, persistence boundary, Git-write gate, host
matrix, and first-HIL runbook are in [`docs/`](docs/ARCHITECTURE.md). The
ChatGPT connection boundary is documented in
[`docs/CHATGPT_CONNECTION.md`](docs/CHATGPT_CONNECTION.md).
The accepted-entry versus live-source law and the independently reconciled
Claude/Fable deltas are documented in
[`docs/RUNTIME_STALENESS_CONTRACT.md`](docs/RUNTIME_STALENESS_CONTRACT.md) and
[`docs/REFERENCE_RECONCILIATION.md`](docs/REFERENCE_RECONCILIATION.md).

## Run MCP

STDIO:

```text
plugins/evidence-lane-plugin/.venv/Scripts/python.exe \
  plugins/evidence-lane-plugin/scripts/run_mcp.py --transport stdio
```

Local Streamable HTTP:

```text
plugins/evidence-lane-plugin/.venv/Scripts/python.exe \
  plugins/evidence-lane-plugin/scripts/run_mcp.py --transport streamable-http \
  --host 127.0.0.1 --port 8765
```

The HTTP endpoint is `/mcp`. ChatGPT cannot connect directly to a local MCP
server. This private tool-only app therefore requires either OpenAI Secure MCP
Tunnel or a later stable HTTPS deployment plus Developer Mode registration.
Production hosting remains deferred. The first private HIL uses local STDIO; no
public endpoint is configured by this repository.

## Session flash authority

Plugin selection injects a concise universal session-behavior prompt. Before
governed work, the runtime verifies the exact 16-member ENV15/UOP15 subset:
member sizes and SHA-256 values, MMD locks, read-only immutable SQLite integrity,
foreign keys, and SQLite user version. The first valid `session_boot` creates one
installation-scoped flash receipt; later boots reuse the same digest.

The supplied parent packet is not intact: six declared `codex/` members are
missing and one undeclared research file is present. Evidence Lane therefore
accepts only the independently verified ENV/UOP subset and always returns the
`SOURCE_PACKET_PARTIAL_INTEGRITY` warning. See
[`docs/SESSION_FLASH_AUTHORITY.md`](docs/SESSION_FLASH_AUTHORITY.md).

## Persistence

The canonical local store defaults to the plugin data directory or
`EVIDENCE_LANE_DATA_ROOT`. Google Drive use requires an explicit user OAuth
token in `EVIDENCE_LANE_GOOGLE_DRIVE_ACCESS_TOKEN` and a selected visible folder.
The token is read only from the environment and is never returned or persisted.
Private, restricted, or confidential PVs also require an explicit 32-byte
URL-safe-base64 AES-GCM key in `EVIDENCE_LANE_DRIVE_ENCRYPTION_KEY`.

## Authority

The sealed parent planning package remains separate, immutable, and outside this
repository. This repository contains only the additive Evidence Lane plugin
implementation and its implementation evidence.

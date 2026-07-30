# Evidence Lane Plugin architecture

## Implemented product boundary

Evidence Lane Plugin is a private, tool-only universal Evidence Lane lifecycle
engine. It preserves project continuity as immutable SQLite project versions
(PVs) while the prompt, model, and supported host remain replaceable.

One executable transition law governs all eighteen canonical lanes. The
deterministic engine owns exact source identity, routing, extraction, indexing,
PV construction, validation, hashing, immutable promotion, accepted-pointer
compare-and-swap, incremental Refresh, and Delta calculation. The MCP layer
owns typed host-facing tools and canonical result envelopes; it does not own
human-in-the-loop authority. `/evi`, its numeric root commands, matching
native skills, and lifecycle hooks are visible control surfaces, not
independent state machines.

The repository contains no desktop UI, local model, browser renderer, public
deployment, or historical brain package. It contains one minimized,
hash-locked ENV15/UOP15 session-flash authority. That installation context is
outside every PV.

## Runtime components

| Component | Responsibility | Authority boundary |
| --- | --- | --- |
| `CodePVEngine` | Exact source identity, primary code database, universal lane bundle, and PV candidate construction | Cannot accept a candidate |
| `lane_engine` and `lanes` | One immutable 18-lane registry; per-lane SQLite/MMD/DOT/tools; exact routing; PV1 full build; PV2+ Refresh | Cannot move the project pointer |
| `SessionFlashAuthority` | Verify exact ENV/UOP members, locks, SQLite, and installation receipt | Cannot create a PV or infer HIL |
| `ProjectStore` | Immutable candidates and accepted PVs, task backlog, receipts, accepted history, and CAS pointer | Cannot infer a human decision |
| `SessionManager` | Fresh-window State Travel, entry-PV verification, one writer, one active task, six-way HIL, rollback, and exact recovery transitions | Only exact `APPROVE` can promote a candidate; it cannot claim a host window opened |
| `PromptIndex` and `UserPromptSubmit` hook | Bind host prompt/turn references to the current entry PV with SHA-256 evidence | Stores no raw prompt text and cannot move a pointer |
| `PVReader` and `LaneReader` | Progressive validated reads over primary and lane SQLite authorities | Read-only; candidates are labeled unaccepted |
| `PVSyncService` | Seal PV artifacts and persist bounded decisions plus generation-addressed pointer snapshots | Never uploads a raw clone or cache |
| Required Google Drive app | Normal host OAuth and connector-mediated verified-mirror capability | Connector token never enters the Python MCP |
| `RemoteGitController` | Prepare and execute one separately confirmed push | PV approval alone is insufficient |
| `FastMCP` server | Universal tool contract for supported hosts | Host permissions and connectivity remain separate |

## Canonical universal PV

Every PV has the root package contract plus a recursively checksummed lane
bundle:

```text
PVn/
|-- code.sqlite
|-- project_master_topology.mmd
|-- project_master_topology.dot
|-- active_pointer.json
|-- project_identity.json
|-- manifest.json
|-- SHA256SUMS.txt
|-- chat_lineage.jsonl
|-- entry_slip.json
|-- exit_slip.json
|-- pv_receipt.json
`-- lanes/
    |-- registry.json
    |-- routes.json
    |-- project_lane_topology.mmd
    |-- project_lane_topology.dot
    |-- manifest.json
    |-- SHA256SUMS.json
    `-- <canonical_lane_id>/
        |-- <canonical_lane_id>_sector_v001.sqlite
        |-- <canonical_lane_id>.mmd
        |-- <canonical_lane_id>.dot
        |-- tools.json
        |-- lane_pointer.json
        |-- refresh_receipt.json
        `-- lane_manifest.json
```

SVG and PNG are optional derived outputs. A rendering failure is recorded and
does not invalidate correct SQLite/MMD/DOT authority.

`exit_slip.json` includes the neutral `evidence-lane.next-action.v1` contract.
It records the exact candidate-bound HIL command and prompt text, declares the
composer host-owned, forbids auto-submit, and requires a stop. It remains useful
whether or not a particular host renders its own gray prompt suggestion.

## Eighteen canonical lanes

The registry contains `github_code`, `local_code`, `chat_lineage`,
`discussion`, `analysis`, `plan`, `mode`, `docs`, `data_excel`, `ppt`,
`pdf_ocr`, `images_ocr`, `artifacts`, `custom`, `brain_loader`, `research`,
`project_engulf`, and `sqlite_brain`.

Every routed source belongs to exactly one lane. `/evi-02-git` selects
`github_code`; `/evi-03-local` selects `local_code`; the old code alias remains
an internal compatibility read alias. The `brain_loader` ID remains stable but
is presented only as **SQLite PV Candidate Loader**. A named lane-route grant can
override one exact source path for one candidate; it is recorded, consumed,
and re-locked. Accepted route authority is inherited by later Refresh runs.

The Mode registry entry is retained for schema compatibility, but Mode is not
source intake. One `/evi-mode` control sidecar classifies ordered mode
intersections, maps them to canonical lanes including Chat Lineage, appends a
privacy-minimized receipt when a governed session is active, and returns to the
unchanged lifecycle position.

All lanes preserve exact bytes, source hashes, structured facts, FTS5/BM25,
materialized TF-IDF, parser capability state, pointer evidence, and validation
receipts. Format-specific extractors add code symbols/imports/routes,
workbook/sheet/range/cell/formula/dependency/table/chart facts, CSV and
JSON/JSONL structure, Parquet schema/rows, PDF native text and local OCR
evidence, image OCR evidence, document hierarchy, slide
shape/text/table/image/relationship structure, archive topology, chat
hash-chain lineage, or immutable SQLite/brain inspection as applicable.
Optional external binaries and deliberately unbundled parsers remain explicit
capability rows and exact blockers; their absence is never represented as a
successful parse.

## PV and pointer law

- PV1 is the only normal full-source build.
- `/evi-00-state-travel` is displayed first. A prepared handoff requires a new
  Codex task or ChatGPT chat, then runs the atomic Boot plus locked ENV/UOP
  Flash verification and verifies accepted pointer generation, manifest, and
  package seals before loading `entry_pv` and waiting.
- PV2+ materializes from that accepted package and classifies every source as
  `UNCHANGED_REUSE`, `CHANGED_REBUILD`, `NEW_REGISTER`,
  `REMOVED_TOMBSTONE`, or `BLOCKED_UNSUPPORTED`.
- Unchanged stable lane artifacts are reused byte-for-byte.
- Accepted ordinals are never reused. Multiple preserved unaccepted candidates
  may propose the same next ordinal until one is approved; this is required for
  a corrected initial PV1 that has no accepted parent.
- Only exact `APPROVE` through Fuse promotes candidate bytes and advances the
  pointer. It seals a State Travel handoff; the origin host window does not
  enter the next turn.
- `ROLLBACK` never accepts a candidate. It moves only the accepted pointer to
  immutable accepted history using compare-and-swap. It resolves `PVn`,
  `PROMPT <index>`, or `TURN <id>`; a bare rollback uses the current prompt
  entry and falls back to the governed session entry.
- The next candidate ordinal is always one above the highest accepted history,
  including after backward pointer travel.

## Authority order

1. `/evi-00-state-travel`, including fresh-window Boot/Flash/pointer/seal
   verification when an accepted handoff is pending.
2. `/evi-01-boot`, which atomically verifies installed source, exact locked
   ENV/UOP Flash outside PV, project/pointer identity, and session intent.
3. Seventeen ordered source-intake commands, beginning with Git, Local, and
   SQLite PV Candidate Loader and including Project Engulf; `/evi-mode` remains
   a separate anytime sidecar.
4. `/evi-30-build-pv-entry`, or direct load of validated accepted PV bytes.
5. One persistent session, one writer, one active task, and bound host prompt.
6. Visible source and acceptance evidence.
7. Automatic completion, deterministic Refresh candidate, and internal slips.
8. Exact human HIL decision and Fuse-sealed State Travel when approved.
9. Pointer-only Rollback.
10. Explicit `/evi-exit-boot` session deactivation, which preserves all
    installation, Flash, PV, candidate, lineage, backlog, and pointer state.
11. Separate host-connector, direct durable-server, and remote-Git authority.

No later layer silently replaces an earlier one.

## Related contracts

- [`STATE_MACHINE.mmd`](STATE_MACHINE.mmd)
- [`HOST_CAPABILITY_MATRIX.md`](HOST_CAPABILITY_MATRIX.md)
- [`SESSION_FLASH_AUTHORITY.md`](SESSION_FLASH_AUTHORITY.md)
- [`RUNTIME_STALENESS_CONTRACT.md`](RUNTIME_STALENESS_CONTRACT.md)
- [`CHATGPT_CONNECTION.md`](CHATGPT_CONNECTION.md)
- [`REFERENCE_RECONCILIATION.md`](REFERENCE_RECONCILIATION.md)
- [`GOOGLE_DRIVE_PERSISTENCE.md`](GOOGLE_DRIVE_PERSISTENCE.md)
- [`GIT_WRITE_CONTRACT.md`](GIT_WRITE_CONTRACT.md)
- [`FIRST_HIL_RUNBOOK.md`](FIRST_HIL_RUNBOOK.md)
- [`IMPLEMENTATION_TRACEABILITY.md`](IMPLEMENTATION_TRACEABILITY.md)
- [`PROMPT_SUGGESTION_COMPATIBILITY.md`](PROMPT_SUGGESTION_COMPATIBILITY.md)

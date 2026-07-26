# Evidence Lane Plugin architecture

## Implemented product boundary

Evidence Lane Plugin is a private, tool-only Git/code lifecycle plugin. It keeps
project continuity in immutable SQLite project versions (PVs), while the model
and host remain replaceable.

The deterministic engine owns source identity, whole-repository ingestion,
SQLite intelligence, package construction, validation, hashing, immutable
promotion, accepted-pointer compare-and-swap, and Delta calculation.

The MCP layer owns typed host-facing tools and canonical result envelopes. It
does not own HIL authority. Codex hooks are advisory session context only.

The current repository contains no desktop UI, local model, browser renderer,
public deployment, or historical brain package. It does contain one minimized,
hash-locked ENV15/UOP15 session-flash authority. That authority is installation
context only and is forbidden from every PV.

## Runtime components

| Component | Responsibility | Authority boundary |
| --- | --- | --- |
| `CodePVEngine` | Exact repository ingestion and PV candidate creation | Cannot accept a candidate |
| `SessionFlashAuthority` | Verify exact ENV/UOP members, locks, SQLite, and installation receipt | Cannot create a PV or infer HIL |
| `ProjectStore` | Immutable candidates/accepted PVs and CAS pointer | Cannot infer a human decision |
| `SessionManager` | One project, agent, task, five-way HIL, and exact recovery transitions | Only exact `APPROVE` can advance |
| `PVReader` | Progressive search, bounded fetch, focused query, and diff over validated PVs | Read-only; candidates are explicitly labeled unaccepted |
| `PVSyncService` | Seal and persist bounded PV/receipt artifacts | Never uploads a raw clone or cache |
| `RemoteGitController` | Prepare and execute one separately confirmed push | PV approval alone is insufficient |
| `FastMCP` server | Universal tool contract for supported hosts | Host permissions remain separate |

## Canonical PV contents

Every required PV package contains:

```text
PVn/
|-- code.sqlite
|-- project_master_topology.mmd
|-- active_pointer.json
|-- project_identity.json
|-- manifest.json
|-- SHA256SUMS.txt
|-- chat_lineage.jsonl
|-- entry_slip.json
|-- exit_slip.json
`-- pv_receipt.json
```

SVG and PNG are optional derived outputs. A render failure is recorded as a
warning and cannot invalidate a correct SQLite PV.

## Authority order

1. Exact locked session-flash authority outside PV.
2. Exact Git/source identity.
3. Validated accepted PV and pointer generation.
4. One explicit session and task contract.
5. Visible operational evidence.
6. Deterministic exit candidate.
7. Exact human HIL decision.
8. Separate persistence or remote-Git authority when requested.

No later layer silently replaces an earlier one.

## State diagram

See [`STATE_MACHINE.mmd`](STATE_MACHINE.mmd). The diagram is source authority;
rendering is optional.

## Related contracts

- [`HOST_CAPABILITY_MATRIX.md`](HOST_CAPABILITY_MATRIX.md)
- [`SESSION_FLASH_AUTHORITY.md`](SESSION_FLASH_AUTHORITY.md)
- [`RUNTIME_STALENESS_CONTRACT.md`](RUNTIME_STALENESS_CONTRACT.md)
- [`CHATGPT_CONNECTION.md`](CHATGPT_CONNECTION.md)
- [`REFERENCE_RECONCILIATION.md`](REFERENCE_RECONCILIATION.md)
- [`GOOGLE_DRIVE_PERSISTENCE.md`](GOOGLE_DRIVE_PERSISTENCE.md)
- [`GIT_WRITE_CONTRACT.md`](GIT_WRITE_CONTRACT.md)
- [`FIRST_HIL_RUNBOOK.md`](FIRST_HIL_RUNBOOK.md)
- [`IMPLEMENTATION_TRACEABILITY.md`](IMPLEMENTATION_TRACEABILITY.md)

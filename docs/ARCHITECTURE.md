# Architecture

Evidence Lane 0.8.2 separates public controls, lifecycle APIs, brain artifacts,
host storage, and human authority.

## Control plane

Root `/evi` presents exactly Boot, Rollback, Build, Refresh, Mode, and Source
Intake. State Travel is a separate user-timed continuity event: a sealed
handoff makes it eligible, while an explicit user request or genuine host
context exhaustion is still required to invoke it. Internal MCP tool names
remain stable but do not enlarge the public surface.

At the accepted boundary, an explicit unchanged-host continuation may call the
internal next-turn helper with the exact continuation reason. The original
sealed handoff is preserved byte-for-byte in history, a non-consumption
supersession receipt is appended, and the accepted pointer does not move. A
different host session must use verified State Travel.

Atomic Boot runs doctor, verifies the immutable ENV15/UOP15 Flash, detects
Codex desktop/CLI/ChatGPT and durable/ephemeral capability, chooses storage,
and boots or resumes one governed session. It fails closed when an ephemeral
host lacks a transactional durable connector.

`/evi-exit-boot` detaches Flash context and visible prompt/response capture
while preserving the installed plugin, immutable project store, pointer, and
installation Flash receipt. A later Boot explicitly re-verifies and reattaches.
The `/evi-storage` (compatibility: `/evi-change-storage-connector`) sidecar
inspects or changes project-scoped storage with an append-only exact-token
receipt; it is not a seventh control.

The append-only backlog permits one active bounded task. Completion records
host source confirmation and creates a fresh unaccepted candidate. HIL records
five non-promotion decisions; exact `APPROVE` is accepted only by Fuse.

## Data plane

One immutable registry defines eighteen lanes, parsers, aliases, schemas, FTS,
and mutation policies. Source Intake auto-detects ordered sources or applies
exact overrides. Its optional Git arm uses AUTO fallback, REQUIRED fail-closed,
or explicit DISABLED behavior without granting remote-write authority. Mode is
an independent sidecar for locked intersections and explicit custom schemas.
Both always include Chat Lineage. The locked ENV15/UOP15 sources remain
immutable while a digest/schema/host-ABI keyed installation projection provides
read-only FTS queries. Full source verification always precedes projection
reuse; mutable development bundles rebuild it.

Every lane emits:

- a strict SQLite brain with integrity/FK/FTS checks;
- authoritative Mermaid and DOT topology;
- tool and parser identity;
- pointer and refresh evidence;
- a content-sealed manifest.

Independent lane computation is bounded to at most eight in-process workers.
All workers read one pre-hashed source snapshot and write only their assigned
lane directory. The main writer waits at a fail-closed barrier, re-hashes the
repository, verifies that every routed source hash is present in exactly one
lane SQLite brain, and assembles manifests in canonical registry order. Refresh
uses the same path, so only changed lanes perform incremental work while
unchanged lanes byte-reuse accepted artifacts. Chat Lineage append, HIL, Fuse,
accepted-pointer movement, rollback, and State Travel never run concurrently.

Git code lanes enumerate all reachable commits and refs, preserve exact blob
bytes, record file changes, reuse blob/chunk CAS, store occurrences, and build
history FTS. Incremental Refresh copies accepted brains, reindexes only changed
sources/sections, retains chunk history, tombstones removals, and byte-reuses
unaffected lanes.

## Candidate project overlays

Each package includes a candidate-only project-sector overlay. Visible Chat
Lineage fans into Chat Lineage and deterministic relevant sectors. Operational
tool/command/file/test/build/Git events route to the active code sector and
Artifacts; Source Intake events route to their classified sectors; output links
route to Artifacts.

Initial prompts and detectable mid-turn steers append as distinct ordered,
idempotent events. Overlay events retain actors, available model/submodel and token metrics,
visible payload hashes, event-chain pointers, and candidate/pointer identity.
Secrets are redacted. Hidden chain-of-thought/private reasoning is rejected.
All overlay truth stays `CANDIDATE_ONLY` and `accepted_sector_truth=0` before
Fuse. In addition to each session JSONL hash chain and sibling SQLite/FTS
projection, a project-wide Chat Lineage SQLite authority maintains a canonical
global state-hash head. Boot and resume read that head before task execution.

Lane-bundle schema v2 requires a sealed parallel-execution receipt and frozen
source binding. The validator keeps accepted schema-v1 bundles readable only
when the receipt and every v2 parallel field are absent. It never converts or
rewrites accepted data, and a v2 bundle with a missing receipt remains invalid.

## Connector brain

The connector brain records append-only plugin registrations, events, routes,
FTS, and separate exact drop receipts. It stores configuration environment
variable names only. At most eight additional plugins may be active. Routing is
deterministic and falls back to built-ins or a visible fail-closed result. Every
new grant records purpose, allowed actions, canonical lanes, write scope,
expiry, and actor. `/evi-plugin` is the compact sidecar;
`/evi-additional-plugin` and `/evi-drop-additional-plugin` remain discoverable
compatibility names.

## Atomic Delta completion

A multi-Delta implementation closes through one locked batch receipt only when
the evidence list names every queued Delta exactly once and in ledger order.
Each task receives append-only QUEUED -> ACTIVE -> DONE events. No task is
silently dropped or accepted; the later six-way HIL disposition maps the batch
without bypassing exact-`APPROVE` Fuse law.

## Host and deployment boundaries

Codex remains local and Git-backed. Durable local SQLite is primary. Google
Drive is an optional mirror/fallback. ChatGPT may use a durable remote MCP; the
Vercel component is only a release-verifying HTTPS adapter to that origin. It
stores no authority and is never the general router.

Candidate build, Git push, plugin install, and preview deployment are evidence,
not acceptance. State Travel is valid only after exact-APPROVE Fuse, a sealed
handoff, and an explicit user or genuine context-exhaustion trigger in a
genuinely fresh destination host.

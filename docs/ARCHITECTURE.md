# Architecture

Evidence Lane 0.6.0 separates public controls, lifecycle APIs, brain artifacts,
host storage, and human authority.

## Control plane

Root `/evi` conditionally exposes State Travel and otherwise presents exactly
Boot, Rollback, Build, Refresh, Mode, and Source Intake. Internal MCP tool names
remain stable but do not enlarge the public surface.

Atomic Boot runs doctor, verifies the immutable ENV15/UOP15 Flash, detects
Codex desktop/CLI/ChatGPT and durable/ephemeral capability, chooses storage,
and boots or resumes one governed session. It fails closed when an ephemeral
host lacks a transactional durable connector.

The append-only backlog permits one active bounded task. Completion records
host source confirmation and creates a fresh unaccepted candidate. HIL records
five non-promotion decisions; exact `APPROVE` is accepted only by Fuse.

## Data plane

One immutable registry defines eighteen lanes, parsers, aliases, schemas, FTS,
and mutation policies. Source Intake auto-detects ordered sources or applies
exact overrides. Mode is an independent sidecar for locked intersections and
explicit custom schemas. Both always include Chat Lineage.

Every lane emits:

- a strict SQLite brain with integrity/FK/FTS checks;
- authoritative Mermaid and DOT topology;
- tool and parser identity;
- pointer and refresh evidence;
- a content-sealed manifest.

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

Overlay events retain actors, available model/submodel and token metrics,
visible payload hashes, event-chain pointers, and candidate/pointer identity.
Secrets are redacted. Hidden chain-of-thought/private reasoning is rejected.
All overlay truth stays `CANDIDATE_ONLY` and `accepted_sector_truth=0` before
Fuse.

## Connector brain

The connector brain records append-only plugin registrations, events, routes,
FTS, and separate exact drop receipts. It stores configuration environment
variable names only. At most eight additional plugins may be active. Routing is
deterministic and falls back to built-ins or a visible fail-closed result.

## Host and deployment boundaries

Codex remains local and Git-backed. Durable local SQLite is primary. Google
Drive is an optional mirror/fallback. ChatGPT may use a durable remote MCP; the
Vercel component is only a release-verifying HTTPS adapter to that origin. It
stores no authority and is never the general router.

Candidate build, Git push, plugin install, and preview deployment are evidence,
not acceptance. State Travel is valid only after exact-APPROVE Fuse and a sealed
handoff in a genuinely fresh destination host.

# Architecture

Evidence Lane 1.3.0 separates public controls, lifecycle APIs, brain artifacts,
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

For Git worktrees, the current-source inventory is derived from tracked index
entries rather than a filesystem crawl. Ignored and untracked operational files
are outside the inventory. A shared policy filters sensitive paths and content
before any SQLite, FTS, CAS, history, topology, or package write; Git history
applies the same rule to reachable blobs and prunes unsafe inherited rows during
incremental reuse. Non-Git sources apply the deterministic path/content policy
without claiming a tracked-file boundary.

Mermaid and DOT are derived deterministically from the completed SQLite lane,
not from a second unverified file walk. Each topology contains source intake,
lane-specific tables and facts, retrieval/CAS/FTS, lifecycle/pointer evidence,
and outputs. GitHub and Local Code add code snapshot and Git-lineage subgraphs.
The project master graph shows bounded parallel lane computation feeding one
deterministic join and one serial candidate/HIL/Fuse authority path.
Optional render validation uses the configured Puppeteer browser or a standard
installed Chrome/Edge executable. A missing renderer is reported separately
from the authoritative MMD/DOT and SQLite validation.
Bundle validation then reparses both graphs and reconciles them independently to
read-only SQLite. Structural floors, balanced blocks, endpoint resolution,
exact MMD/DOT subgraph/node/edge parity, root totals, table row counts, and fact
kind counts must all pass. Rendering success alone is never topology proof.
Previously sealed v1 packages and pre-v1.1 v2 packages remain readable only
through explicit compatibility reports. That path validates their original
seals and database contracts but does not claim source-policy enforcement or
topology reconciliation that did not exist when they were built. All newly
built v1.3 candidates must pass both gates.

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
role-schema fields, FTS, and separate exact drop receipts. It stores
configuration environment-variable names only. At most eight additional
plugins may be active. Structured settings expose independent CODEX and CHATGPT
profiles without claiming a host-native settings panel. Routing is deterministic
and evaluates active state, grant lifetime, exact capability/action, canonical
lane, and host profile in a fixed order. Zero matches fail closed. Multiple
eligible plugins are never resolved by ID ordering: the caller must name one
exact preferred plugin ID, or the route records `AMBIGUOUS_FAIL_CLOSED` with
the eligible IDs and first failed guard for every registration. Every new grant
records one immutable purpose/reason, role and typed role schema, host profiles,
allowed actions, canonical lanes, write scope, expiry, actor, and an optional
declared Python/Java/Kotlin/Go/Rust/C++/external-MCP backend. A declaration is
capability metadata, not execution authority. `/evi-plugin` is the compact sidecar;
`/evi-additional-plugin` and `/evi-drop-additional-plugin` remain discoverable
compatibility names.

The MCP process prewarms optional NumPy/OpenCV/ONNX OCR dependencies before
starting FastMCP's event loop. The cached OCR call boundary is serialized, but
independent lane computation remains inside the same bounded worker pool. This
fixes native-loader/event-loop ordering without making Source Intake, Build, or
Refresh globally linear.

## Atomic Delta completion

A multi-Delta implementation closes through one locked batch receipt only when
the evidence list names every queued Delta exactly once and in ledger order.
Each task receives append-only QUEUED -> ACTIVE -> DONE events. No task is
silently dropped or accepted; the later six-way HIL disposition maps the batch
without bypassing exact-`APPROVE` Fuse law.

## Website narrative shell

The public Next.js site uses exact approved visual assets and bounded interaction
ideas from the read-only EvidenceOS SQLite frontend authority. The full logo,
cube icon, static brain, and official tool icons retain explicit roles; the
active website does not import the desktop app's Glass Shell, workspace frame,
side rail, PC cluster, system-task view, Rust/Tauri transport, backend state,
scanner, telemetry-page orb, or 3D telemetry nodes. Exact asset hashes and the
active component contract are pinned by a conformance test and source receipt.

Delta 063 supersedes only the fixed-window presentation attempted in Delta
062. The active website is a document-scrolling narrative with no application
side rail. The exact full logo floats at the top-left of one frosted navigation
pill; the navigation floats independently at the top-right. The cube remains
the compact browser, ChatGPT, Codex, and packaged-app icon. Content routes
retain normal browser scrolling and open as full-width story pages.

The light white/cyan/gold field uses bounded native Three.js rings and points
plus reduced-motion-aware Framer transitions. Those layers are atmospheric
only. They never represent project topology and import no telemetry root,
scanner, PC-cluster, system-task, or native Tauri transport. Public-repository
source brains remain evidence panels: exact Git-tree matches, derived SQLite
counts, implementation use, refused transplants, and version/provenance limits
are displayed together.

Lane hover, click, focus, and keyboard selection drive one visible story: the
selected lane's official app tool icons travel into the pulsing glass brain,
then exactly four lane-bound outputs emit below it (SQLite, Mermaid, DOT, and
Refresh receipt).
Replay and optional auto-cycle alter presentation state only; they do not run a
build or claim new evidence. The full-width Prompt Studio has no persistent
rail, answers only from its published local knowledge map, and visibly refuses
unsupported questions. It makes no external-model or provider claim.

## Host and deployment boundaries

Codex remains local and Git-backed. Durable local SQLite is primary. Google
Drive is an optional mirror/fallback. ChatGPT may use a durable remote MCP; the
Vercel component is only a release-verifying HTTPS adapter to that origin. It
stores no authority and is never the general router.

Candidate build, Git push, plugin install, and preview deployment are evidence,
not acceptance. State Travel requires a verified sealed handoff plus an explicit
user or genuine context-exhaustion trigger in a genuinely fresh destination
host. Acceptance is not a prerequisite for unfinished-work travel: the handoff
preserves the live source, pointer/candidate context, Plan Lane, additive Deltas,
exact resume row, and host execution profile. Accepted-entry travel remains a
separate explicit mode when the user asks to enter accepted context.

## Release evidence plane

Accepted PVs are validated as immutable authority for bytes, hashes, pointer
identity, lane checksums, and SQLite integrity. They are not retroactively
requalified as candidates when a successor release adds stricter topology
rules; the compatibility state is reported, and the successor candidate must
pass the current rules.

Top-level v1.3 Delta receipts use a canonical self-seal: remove only the
top-level `receipt_sha256`, serialize canonical JSON, and hash those bytes with
SHA-256. The repository test scans every such receipt. Any tracked-source
change after a branch push action or preview deployment is prepared invalidates
that exact-source evidence and requires a new tested commit, action, token, and
preview.

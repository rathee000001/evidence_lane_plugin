<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# AI Learning

Evidence Lane AI Learning is a project-scoped authority for reusable lessons.
It is separate from Project Truth, Canon Input, Project Memory, ChatLineage,
host memory, Plan, and the accepted Project PV pointer. Brain scaling means bounded indexed
retrieval and composition; it does not mean autonomous training.

## What Learning can record

An evidence-backed Learning candidate may describe a procedural lesson,
failure-avoidance rule, relationship, tool route, host compatibility fact, or
other reusable project behavior. Every candidate binds:

- exact project, task, Delta, and PV context;
- lesson type, tier, scope, and temporal validity;
- evidence and counterevidence references;
- outcome, calibrated confidence, contradictions, and supersession;
- privacy class and exact ChatLineage head; and
- a stable identity and content hash.

Actor/model evidence remains in linked ChatLineage rather than being copied
into the lesson.

## Independent lifecycle

The Learning family has five public actions:

1. `learning_inspect`
2. `learning_retrieve`
3. `learning_seal_candidate`
4. `learning_decide_candidate`
5. `learning_revoke`

The provider-neutral internal SDK also exposes
`bootstrap_verified_history`. It is deliberately not another public MCP
action: it deterministically seals unaccepted candidates from approved
historical Plan outcomes and exact verified forward-Delta checkpoints. Rows
that are merely DONE, ambiguous, dropped, superseded, or unverified are
excluded. Replays reuse candidate identities and the immutable bootstrap
receipt; Project Truth, both HIL surfaces, and both pointers remain untouched.

Sealing creates `PENDING_LEARNING_HIL`. It creates no Project candidate and
moves no pointer. Learning owns its own six-way decision surface:
`APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`, pointer-only Learning
rollback, `REJECT`, and `FAIL`. A Learning decision may move only the Learning
pointer. It never authorizes Project Fuse, Git, installation, deployment,
Canon acceptance, or State Travel.

Accepted, rejected, revoked, expired, rolled-back, and superseded lessons stay
immutable in history. Retrieval excludes ineligible lessons with an explicit
reason rather than deleting them.

## SQLite and bounded retrieval

The project `learning/` authority stores immutable candidates, append-only
events, decision receipts, pointer generations, canonical JSON artifacts, and
an FTS5 projection. Retrieval uses BM25 and returns only the requested bounded
slice. The full Learning ledger is never loaded into model context or silently
concatenated with Project Truth.

No hit is a valid governed result. A Project-Truth conflict suppresses the
lesson rather than changing accepted project evidence.

## Host-memory boundary

Codex or ChatGPT host memory is optional generated recall, not Evidence Lane
authority. It is never scanned or promoted automatically. A host-memory fact
may enter Learning evidence only through an immutable
`host-memory-import://<receipt-sha256>` provenance receipt binding the exact
project/task/Delta/PV, source-record hash, context hash, purpose, actor, times,
and unchanged Project Truth pointer. Raw memory text, secrets, and private
reasoning are not persisted by that receipt.

Hooks may transport visible lifecycle events but cannot import, decide, or
promote Learning. The Formula Engine may compile and route ENV/UOP operators;
it is not the learner.

## Cross-sector role

ChatLineage supplies visible event provenance. Canon may provide bounded input.
Memory may retrieve linked project facts. AI Learning may then propose a lesson
whose evidence points back to those immutable records. The authorities remain
separate throughout that flow and keep separate schemas, pointers, receipts,
failure states, and HIL decisions.

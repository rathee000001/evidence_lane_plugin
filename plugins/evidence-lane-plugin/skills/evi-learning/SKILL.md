---
name: evi-learning
description: "Govern the project-isolated AI Agent Learning arm: inspect and retrieve accepted lessons, seal evidence-backed learning candidates, run the separate Learning HIL, and revoke accepted learning without changing Project Truth. Use when an AI workflow proposes a reusable procedural, failure-avoidance, relational, tool-routing, or host-compatibility lesson."
---

# Evidence Lane Agent Learning

First apply the shared installed lifecycle contract in
`../evidence-lane-code-lifecycle/references/shared-boundaries.md`; this skill narrows that contract to
the separate Agent Learning authority and never widens lifecycle permission.

Agent Learning is a separate project-scoped AI learning authority under the
locked ENV/UOP boundary. It is not Project Truth, Canon Input, ChatLineage, a
Formula Engine operator, autonomous training, or the Project PV pointer. Brain
scaling means bounded indexed retrieval and composition, never self-training.

## Host memory boundary

Codex host memories are an optional generated recall layer, not Evidence Lane
authority. The host documents local Codex memories as generated
state under Codex home and recommends checked-in documentation or `AGENTS.md`
for guidance that must always apply. Do not scan, import, trust, or promote host
memory automatically, and do not treat a memory filename, summary, recollection,
or host injection as Project Truth, accepted Learning, ChatLineage, Canon, or a
task receipt.

A host-memory record may enter Learning evidence only through one explicit,
immutable `host-memory-import://<receipt-sha256>` provenance receipt. That
receipt must bind the exact project/task/Delta/PV, source kind and role-based
locator, source-record SHA-256, source-context identity hash, observed/imported
times, actor, purpose, and unchanged Project Truth pointer hash. Persist no raw
memory text, secret, or private reasoning. A direct `codex-local-memory://`,
`chatgpt-memory://`, or `host-memory://` evidence reference fails closed.

Invoke `learning_record_host_memory_import` only after an explicit user request
or an explicit governed workflow step. Recording provenance creates no
Learning candidate and invokes no Learning or Project HIL. Candidate sealing
remains a later explicit action; its result is still `PENDING_LEARNING_HIL`.
Hooks never import memory and attach explicit
`host_memory_imported=false`, `learning_candidate_created=false`, and
`learning_hil_invoked=false` boundaries to their behavior handoff.

## Inspect and retrieve

Run `pv_status`, `pv_task_backlog`, one bounded live-root `pv_query`, and the
current-authority `search` before using a Learning result in governed work. Neither
read opens the accepted HIL ZIP. Use `learning_inspect` to read the independent
candidate/event/pointer authority. Use `learning_retrieve` with exact scope,
time, conflict, and result limits. Keep the returned Learning slice visibly
separate from accepted Project Truth; do not concatenate or silently rerank the
two authorities.

No hit is a valid result. Expired, rejected, failed, superseded, revoked,
out-of-scope, or Project-Truth-conflicting lessons must remain excluded with an
explicit reason.

The Learning ledger is schema-versioned and validates its exact SQLite tables,
indexes, and FTS5 projections before use. Retrieval queries FTS5 with BM25 and
returns only the bounded result slice; never scan or place the full ledger in
model context. Project Memory is a separate authority, SDK arm, and first-class
`evi-memory` skill. In the current registry snapshot, AI Learning owns these
public actions:
`learning_inspect`, `learning_retrieve`, `learning_record_host_memory_import`,
`learning_seal_candidate`, `learning_decide_candidate`, and `learning_revoke`.
The installed registry contains no Learning-as-Memory compatibility actions;
never route or recreate them.

## Historical and forward bootstrap

PV0 creates no Learning candidate or HIL. Beginning with the first verified
Delta, each Plan-only sub-PV acceptance auto-admits its corresponding Delta
Learning member. Those members are available to later Delta entry as bounded
accepted procedural evidence. The full-PV pointer still represents PV(n-1),
while the live sector projection advances at every Delta exit through the
immediately preceding sub-PV; only the current ACTIVE Delta is absent.

The internal Codex-owned SDK owns `bootstrap_verified_history`; it remains an
internal workflow and never becomes an MCP action. It may seal Learning Delta members only from two
canonical Plan event classes: an `ACCEPTED` row whose latest exact event is an
approved `HIL_OUTCOME`, or a `DONE` row whose latest exact event is
`VERIFIED_TASK_CHECKPOINT_COMPLETED`. Ordinary `TASK_DONE`, queued, dropped,
superseded, ambiguous, or unverified rows are excluded.

The bootstrap reads the canonical Plan SQLite projection in read-only mode,
checks integrity and foreign keys, binds the accepted Project pointer, and
emits one deterministic member per eligible task. Each verified intermediate
member is automatically admitted as `AUTO_ACCEPTED_DELTA_LEARNING`, inheriting
the row's sub-PV acceptance when present and never moving the Learning pointer.
The complete admitted member set is then woven into one deterministic
`PV(n)` Learning candidate for the next full-PV HIL. Repeating the same input
must reuse every member, auto-acceptance event, weave identity, and immutable
receipt. The bootstrap never invokes either HIL, creates a Project candidate,
moves either pointer, imports host memory, or loads the full Plan into model
context. Later verified Deltas become eligible through the same SDK operation;
hooks do not own or auto-run the bootstrap.

Project Memory query and record-link behavior is owned only by `evi-memory`
through `project_memory_query` and `project_memory_record_link`. Those routes
append or return bounded content-addressed locators and edges among the complete
current project-sector registry, ChatLineage, Plan, Project Truth, Canon, Agent Learning, Project
Universe, receipts, and explicit host-memory import receipts. Neither route
stores or returns raw lane databases, Markdown, chat scrollback, or private
reasoning. `SUPERSEDES`, `SUPPRESSES`, and `REVOKES` edges exclude stale targets
at the requested retrieval time while preserving immutable history. Legacy
Memory tables and action names in Learning are immutable migration evidence,
never an active execution route or owner for new Memory writes.

## Expiry ownership

Retrieval owns logical temporal exclusion at its caller-supplied `as_of` and
must exclude an expired lesson even when no `EXPIRED` event has yet been
materialized. Only `AGENT_LEARNING_AUTHORITY_MAINTENANCE` may append those
expiry events through the internal maintenance function. Expiry is not a sixth
MCP/SDK action, not hook-owned, and not an assumed background scheduler. Both
logical exclusion and event materialization leave the Learning and Project
Truth pointers unchanged.

## Seal a candidate

Use `learning_seal_candidate` only for an evidence-backed visible lesson with:

- tier and lesson type;
- bounded task/project scope selectors;
- evidence and counterevidence references;
- outcome and calibrated confidence;
- contradiction and supersession links;
- observed, valid-from, and optional expiry times;
- privacy class and exact ChatLineage head.

Sealing creates `PENDING_LEARNING_HIL`; it does not accept the lesson, create a
Project candidate, invoke Project HIL, or move the Project pointer.

## Learning HIL

Use `learning_decide_candidate` only for the separate exact Learning governed
decision surface: `APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`, pointer-only
Learning rollback, `REJECT`, or `FAIL`. The decision may move only the Learning
pointer. It cannot promote Project Truth or authorize Project Fuse, Git,
install, deployment, Canon acceptance, or State Travel.

Intermediate auto-accepted Delta members are weave inputs and cannot receive
individual human decisions. A full-PV Learning HIL decides exactly one woven
candidate and moves the Learning pointer at most once.

Every full-PV presentation is conjoined with the Project HIL for the same
derived PV number, but the decisions remain separate. Record the Learning
decision first. Project `pv_fuse` may run only after it verifies that the
accepted Learning head is the unique weave targeting that Project proposal.
Project Fuse never moves the Learning pointer, and Learning approval never
promotes Project Truth. After both exact approvals, append one dual HIL
acceptance stamp to the exact Plan row, including the bounded weave summary and
both receipt hashes. Accepted ZIP storage is only the resulting Project
snapshot; Learning inspection and later entry/State Travel never query it.

Use `learning_revoke` to append a revocation for accepted Learning. Preserve the
candidate, acceptance, pointer, retrieval, and revocation history. Never delete
or rewrite an older lesson.

## SDK and operator boundary

The internal Codex-owned SDK exposes the whole engine and its isolated
authority contracts; it is not a public skill and must not merge authority
arms. The Formula Engine compiles and routes bounded ENV/UOP operators; it does
not learn. Hooks transport visible lifecycle events only; they do not decide or
promote Learning.

## Exit receipt

Return the Learning candidate or retrieval identities, scope, evidence and
counterevidence references, lifecycle state, Learning pointer effect, conflict
suppression, and explicit proof that Project Truth and Project HIL were
untouched. When host-memory provenance is present, also return its exact import
receipt SHA-256 and label the source `NONAUTHORITATIVE_HELPFUL_RECALL_ONLY`.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-learning`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

---
name: evi-learning
description: "Govern the project-isolated AI Agent Learning arm: inspect and retrieve accepted lessons, seal evidence-backed learning candidates, run the separate Learning HIL, and revoke accepted learning without changing Project Truth. Use when an AI workflow proposes a reusable procedural, failure-avoidance, relational, tool-routing, or host-compatibility lesson."
---

# Evidence Lane Agent Learning

First apply the shared installed lifecycle contract in
`../evidence-lane-code-lifecycle/SKILL.md`; this skill narrows that contract to
the separate Agent Learning authority and never widens lifecycle permission.

Agent Learning is a separate project-scoped AI learning authority under the
locked ENV/UOP boundary. It is not Project Truth, Canon Input, ChatLineage, a
Formula Engine operator, autonomous training, or the Project PV pointer. Brain
scaling means bounded indexed retrieval and composition, never self-training.

## Host memory boundary

ChatGPT/Codex host memories are an optional generated recall layer, not
Evidence Lane authority. The host documents local Codex memories as generated
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

Recording provenance creates no Learning candidate and invokes no Learning or
Project HIL. Candidate sealing remains a later explicit action; its result is
still `PENDING_LEARNING_HIL`. Hooks never import memory and attach explicit
`host_memory_imported=false`, `learning_candidate_created=false`, and
`learning_hil_invoked=false` boundaries to their behavior handoff.

## Inspect and retrieve

Run `pv_status`, `pv_task_backlog`, and one bounded `pv_query` before using a
Learning result in governed work. Use `learning_inspect` to read the independent
candidate/event/pointer authority. Use `learning_retrieve` with exact scope,
time, conflict, and result limits. Keep the returned Learning slice visibly
separate from accepted Project Truth; do not concatenate or silently rerank the
two authorities.

No hit is a valid result. Expired, rejected, failed, superseded, revoked,
out-of-scope, or Project-Truth-conflicting lessons must remain excluded with an
explicit reason.

The Learning ledger is schema-versioned and validates its exact SQLite tables,
indexes, and FTS5 projection before use. Retrieval queries the FTS5 projection
with BM25 and returns only the bounded result slice; never scan or place the
full ledger in model context. The public family remains exactly five actions:
`learning_inspect`, `learning_retrieve`, `learning_seal_candidate`,
`learning_decide_candidate`, and `learning_revoke`.

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

Use `learning_decide_candidate` only for the separate exact Learning six-way
decision surface: `APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`, pointer-only
Learning rollback, `REJECT`, or `FAIL`. The decision may move only the Learning
pointer. It cannot promote Project Truth or authorize Project Fuse, Git,
install, deployment, Canon acceptance, or State Travel.

Use `learning_revoke` to append a revocation for accepted Learning. Preserve the
candidate, acceptance, pointer, retrieval, and revocation history. Never delete
or rewrite an older lesson.

## SDK and operator boundary

The internal provider-neutral SDK exposes the whole engine and its isolated
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

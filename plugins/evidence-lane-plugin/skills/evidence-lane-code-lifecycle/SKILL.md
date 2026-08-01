---
name: evidence-lane-code-lifecycle
description: Govern one universal Evidence Lane project across user-timed fresh-host State Travel, atomic Boot and locked ENV/UOP Flash, six public controls, eighteen lanes, bounded linear work, unaccepted candidates, exact-APPROVE Fuse, pointer-only Rollback, and explicit Exit Boot.
---

# Evidence Lane universal lifecycle

Use one linear state machine. Runtime context is never accepted evidence.

## Non-negotiable gates

- `/evi-state-travel` may run only after an explicit user request or genuine
  host-context exhaustion and from a sealed accepted-PV handoff in a genuinely
  fresh destination task or chat. A prepared handoff is eligibility evidence,
  not an automatic instruction. Verify host identity, pointer generation,
  manifest, package seals, and freshness, then stop in
  `WAITING_FOR_NEXT_USER_COMMAND`.
- Otherwise `/evi-boot` is first. It atomically runs runtime doctor, locked
  ENV15/UOP15 Flash verification, storage selection, and `session_boot` or
  `session_resume`. Reuse an existing governed session; never duplicate it.
- A booted session persists across host tasks until `/evi-exit-boot`.
- At an accepted boundary, an explicit same-host continuation may call
  `pv_begin_next_turn` with `continue_same_host=true` and exact reason
  `EXPLICIT_USER_CONTINUATION`. Preserve the handoff receipt in history, record
  non-consumption supersession, and leave the pointer unchanged. A changed host
  remains blocked until verified State Travel.
- Never call a candidate accepted. Exact case-sensitive `APPROVE` supplied to
  `pv_fuse` is the only promotion authority. The five non-promotion HIL choices
  may record correction, research, rollback, rejection, or failure state.
- `ROLLBACK` moves only the accepted pointer to immutable accepted history. It
  never promotes a candidate, rewrites source, deletes history, or resets the
  monotonic PV ordinal.
- Record visible operational evidence only. Redact secrets and never store
  hidden chain-of-thought or private model reasoning.
- Remote Git writes require a separately prepared, exact one-use confirmation.

## Six public controls

After root `/evi`, expose exactly this order:

1. `/evi-boot`
2. `/evi-rollback`
3. `/evi-build`
4. `/evi-refresh`
5. `/evi-mode`
6. `/evi-source-intake`

State Travel remains a separate recovery event and is shown only for its two
allowed triggers. Internal MCP tool names remain stable for compatibility and
are not additional public controls.

`/evi-source-intake` accepts ordered sources, auto-detects their canonical
lanes, and accepts exact per-source overrides. It supports all eighteen lanes
and Project Engulf and always adds Chat Lineage. Its source-intake Git arm is
explicitly `AUTO`, `REQUIRED`, or `DISABLED`: AUTO falls back to deterministic
content indexing, REQUIRED fails closed, and DISABLED skips history. This does
not authorize remote writes. Governed lifecycle enrollment remains bounded to
one exact repository and registered branch; replacing that branch requires an
exact clean-checkout receipt and never broadens the allowlist.

`/evi-mode` is an anytime sidecar. It accepts ordered intersections from the
locked mode namespace plus explicit custom mode schemas. It always includes
Mode and Chat Lineage, appends a visible receipt, and returns to the prior
lifecycle position without creating a candidate or moving a pointer.

## Brain and sector law

Use `lane_catalog` as the sole registry for canonical lane IDs, aliases,
parsers, schema contracts, FTS tables, and mutation policies. Each routed lane
owns SQLite, MMD, DOT, tool identity, refresh evidence, and a manifest.

Project-sector overlays and Chat Lineage remain candidate-only until Fuse.
Visible lineage appends initial user prompts and every detectable mid-turn
steer as distinct ordered, idempotent events, plus assistant output, actors,
model/submodel when available, token metrics when available, tools, commands,
files, tests, builds, links, hashes, and pointers. Missing metrics remain
explicitly unavailable; private reasoning is prohibited.

Git brains enumerate reachable history read-only. Content-addressed chunks are
reused by hash, refresh changes only affected sections, FTS remains
deterministic, and all fusion/fallback/rollback events are visible.

Local or durable-host Codex uses local Git-backed SQLite. An ephemeral host
must have a configured durable runtime connector or fail closed. Google Drive
is an optional mirror/fallback, never primary when durable local storage exists.
Persistent connector registrations are governed, history-preserving, and
limited to eight additional active plugins; drop requires its exact token.

## Task, Refresh, and HIL

1. Append requested Deltas with `pv_plan_tasks`; never delete, reorder, or
   silently complete backlog history.
2. Classify exactly one bounded task and record visible activities.
3. Use accepted evidence as entry truth and live repository evidence for
   source changed after entry.
4. Confirm final host source state with
   `HOST_SANDBOX_FINAL_STATE_CONFIRMED` for writable Codex or
   `USER_APPLIED_AND_PULL_CONFIRMED` for user-mediated ChatGPT.
5. `task_complete_and_refresh` seals the final unaccepted candidate. A changed
   schema/tool identity permits a declared full fallback; otherwise reuse
   unchanged lane and chunk artifacts.
6. Present exactly: `APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`,
   `ROLLBACK`, `REJECT`, or `FAIL`. Stop for the human decision.
7. Natural-language continuation or acceptance intent may be classified and
   appended to Chat Lineage, but classification never promotes. Only an exact
   first `/evi-build` argument of `APPROVE` may route to `pv_fuse`.
8. Only the resulting sealed handoff plus an explicit user or genuine
   context-exhaustion trigger can authorize `/evi-state-travel` in a fresh
   destination host.

Default reads use accepted truth and disclose live freshness. Explicit
candidate reads remain labeled `UNACCEPTED_CANDIDATE`. Use bounded fetches and
allowlisted queries; never execute arbitrary source SQL.

---
name: evidence-lane-code-lifecycle
description: Govern one universal Evidence Lane project across user-timed fresh-host State Travel, atomic Boot and locked ENV/UOP Flash, six public controls, eighteen lanes, bounded linear work, unaccepted candidates, exact-APPROVE Fuse, pointer-only Rollback, and explicit Exit Boot.
---

# Evidence Lane universal lifecycle

Use one linear state machine. Runtime context is never accepted evidence.

## Codex hook and skill ownership

- Hook command files own only host-signal parsing plus deterministic validation,
  secret redaction, bounds, deduplication identity, and transport-envelope
  sealing. They hand the exact envelope to the package's
  `hook_skill_runtime` consumer; they do not import or directly own Entry,
  PREPARE, prospective policy guards, tool/change receipts, compaction
  seal/rehydration, COMMIT, or boundary flush behavior.
- The installed lifecycle skill owns those actions through
  `hook_skill_runtime` after exact envelope validation. The consumer records
  `skill_action_owner=INSTALLED_EVIDENCE_LANE_CODE_LIFECYCLE_SKILL` and
  `hook_behavior_executed=false`. Neither the hook adapter nor its transport
  envelope may call native PV tools, call a host behavior tool, embed the
  complete Plan Lane, infer HIL, or call `update_plan`. The consumed lifecycle
  receipt may carry a content-addressed host-Plan rehydration request whose
  behavior owner is this active skill; transport is not execution.
- Every hook binds the same user-owned durable authority: an explicit
  `EVIDENCE_LANE_DATA_ROOT`, otherwise `~/EvidenceLanePV`. Codex-injected
  `PLUGIN_DATA` is selector-scoped installation storage and must never become
  project, session, PV, PromptIndex, ChatLineage, Plan, or Delta authority. Do
  not create or consult a shadow authority under a stable or fallback slot.
- For every visible user prompt, Goal continuation, correction, or mid-Goal
  steer, require the sealed `UserPromptSubmit` transport envelope and its
  skill-owned PREPARE receipt. Then, before substantive reasoning, source
  inspection, mutation, tests, Git, or a lifecycle write, call the installed
  native Evidence Lane route in this order: `pv_status`, `pv_task_backlog`, and
  one bounded `pv_query` against accepted authority. Select an allowlisted query
  that is relevant to the prompt; use a bounded `receipts` query for
  lifecycle-only prompts rather than inventing a semantic match. The
  `pv_query` must be a real native MCP call visible in Codex Sources. Internal
  hook SQLite lookup is not equivalent proof.
- When a canonical Plan Lane exists, validate
  `canonical_authority=PLAN_LANE`, contiguous rows, exactly one active row, and
  `persistent_until=NEXT_SIX_WAY_HIL_PRESENTED`; then the skill calls the host
  `update_plan` tool with every exact executable row. Each label is the native
  structured projection
  `Row <canonical row> / <task ID> — [CLASS=<classification>; GROUP=<plan group>; BATCH=<commit batch or UNASSIGNED>; DEP=<earlier task IDs or ROOT>; GIT=<stage>@<provenance>; VERSION=<marker>@<provenance>; BRANCH=<marker>@<provenance>; ROLE=<panel role>; STATE=<lifecycle status>] <exact description>`.
  Linked Delta JSON remains in native Evidence Lane authority and is never
  copied into a host label.
- After `pv_plan_steer_delta`, repeat `pv_status`, `pv_task_backlog`, the bounded
  native `pv_query`, and the complete `update_plan` projection before resuming
  work. If the installed native route or host plan tool is unavailable, fail
  closed and report the missing behavior route; a hook receipt never substitutes
  for it.
- Classify the visible request before any Plan mutation. Questions,
  explanations, small read-only asks, and bounded artifact readbacks are
  ordinary tasks: record their visible lineage and run the mandatory native
  read sequence, but do not call `pv_plan_steer_delta` or change the Step Task
  List. A request is a Plan steer only when it changes the active Goal's
  executable outcome, dependency, acceptance check, stop condition, release
  route, or HIL path. Link that steer to the exact existing logical row when
  possible; otherwise add exactly one independently testable row. Then refresh
  the complete panel and CURRENT CHANGE once. Never manufacture a Plan event or
  row merely to reproduce a historical Sources-sidebar call count.

### Persistent Step Task List re-entry

The complete host Step Task List is a durable Plan Lane projection, not Goal
state. An active Codex Goal is neither a prerequisite for restoration nor a
substitute for native Plan authority. Preserve this invariant even when no Goal
is attached, a Goal was deleted or recreated, or the host compacted context
automatically.

Treat each of these as a deterministic panel-reentry trigger:

- `SessionStart` with startup, resume, clear, or compact source;
- `PostCompact`, including host-automatic compaction;
- reopening the exact task after an app or host restart;
- every visible prompt, Goal continuation, correction, and mid-Goal steer; and
- any observation that the host panel is missing, partial, stale, or compacted.

After the applicable lifecycle hook receipt, panel restoration is the first
skill-owned behavior. For a visible prompt, PREPARE remains the lifecycle-first
hook receipt; for a resume or `PostCompact` without a new prompt, do not wait for
or fabricate a prompt, Goal, or PREPARE receipt. Before classifying the request,
reasoning about it, inspecting source, mutating anything, testing, using Git, or
calling another lifecycle write, the skill must:

1. call the installed native `pv_status`;
2. call the installed native `pv_task_backlog`;
3. call one bounded installed-native `pv_query`;
4. verify `canonical_authority=PLAN_LANE`, contiguous executable rows, exactly
   one active row, `persistent_until=NEXT_SIX_WAY_HIL_PRESENTED`, and one
   physically final `PHYSICALLY_FINAL_HIL` row in the final position; and
5. call host `update_plan` once with the complete executable projection.

When the lifecycle or task-classification receipt contains
`host_plan_rehydration`, validate its self-hash and exact project/session/task
binding. If its action is
`CALL_HOST_UPDATE_PLAN_EXACTLY_ONCE_FOR_THIS_TRIGGER`, pass
`receipt.projection.items` unchanged to host `update_plan`; an idempotent replay
of the same request is not a second issuance. A later independently observed
panel-loss event may create a new request identity for the same projection.
After the host action, use `task_record_activity` with activity type
`host.plan.observation` only for facts the host actually exposes. Visibility
requires the exact right-side Plan artifact ID, projection SHA, and item count;
explicit acceptance additionally requires the host Accept-control event. An
empty `update_plan` receipt, Sources presence, the Evidence Lane icon, or native
backlog readback proves neither visibility nor acceptance. Record
`HOST_CAPABILITY_UNAVAILABLE` and fail closed when the host cannot perform the
action; never fabricate the artifact or its acceptance.

Project every executable row, including all completed rows, the sole active row,
and all pending rows. Never replace the projection with a window, page, summary,
ellipsis, count-only placeholder, or only the unfinished suffix. Map native
  statuses to host statuses without changing task state. Hydrate classification,
  Plan group, commit batch, dependency, Git stage, current version, current
  branch, and panel role only from structured current non-superseded Plan authority. Preserve the
exact task ID and description. Use the immediately prior executable task as the
linear dependency only when no validated earlier dependency set was declared.
Render missing batch/version/branch/Git metadata as `UNASSIGNED` or
`NOT_DECLARED`; render conflicting current version or branch claims as
`CONFLICTING_DECLARATIONS@RECONCILIATION_REQUIRED` until an exact current marker
is linked. Never infer current release identity from historical, DROPPED, or
SUPERSEDED rows, and never reactivate a completed row because its immutable text
mentions an old commit or version. Never append acceptance checks, stop
conditions, hashes, or raw linked-Delta JSON to a host label; they remain native
authority. This projection law applies to every governed project and corpus.

If the installed native route, any required native read, the canonical Plan
invariants, or host `update_plan` is unavailable, fail closed before work. Do not
use a fallback slot, generated namespace, app connector, cached hook projection,
or direct stdio as replacement behavior. A lifecycle hook may signal re-entry,
show a privacy-safe current-change receipt, and transport a sealed exact
rehydration request. It must not perform native reads or call host
`update_plan`; the active skill validates and executes that request.

## Non-negotiable gates

- `/evi-state-travel` may run only after an explicit user request or genuine
  host-context exhaustion and in a genuinely fresh destination Codex task. A
  prepared handoff is eligibility evidence, not an automatic instruction. By
  default preserve the exact unfinished state, task/pending correction,
  candidate, pointer base, live source, Plan Lane, additive Deltas, resume row,
  and host execution profile. Resume that row after verification. Use accepted
  entry and `WAITING_FOR_NEXT_USER_COMMAND` only when the user explicitly asks
  for accepted context or the origin is already at an accepted boundary.
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
- Remote Git writes require a separately prepared exact action. Version 2 may
  execute the sole registered non-protected test branch without a per-push
  confirmation token; main, merge, PR acceptance, force, branch mismatch, and
  host-credential intake remain forbidden.
- Preserve one governed project, one live writer, linear execution, and
  evidence-first verification under the exact host execution profile. Read-only
  recovery agents are allowed only at a genuine State Travel entry. After
  entry, no subagent, alternate-checkout writer, background mutation, or second
  browser profile is allowed unless the user explicitly changes this boundary.

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

Skills, commands, receipts, and saved contracts use only the canonical bare MCP
tool names advertised by the exact active server. Host-generated connector
namespaces are display and transport metadata, not lifecycle identity. The
stdio compatibility boundary may normalize one only when the remaining name is
in that server's live registered-tool catalog; unknown names remain unchanged
and fail closed. Never route an Evidence Lane lifecycle call through a storage
connector or another plugin namespace.

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

`/evi-plan` is a Codex-only Planning sidecar outside the six controls. If native
Plan mode is not active, return the `/pl` reminder without persisting a plan.
After planning, persist the canonical Plan Lane and return the short prompt the
user copies into the host-owned Goal. Linked steers append to an existing row;
unrelated steers insert a new numbered row before the next HIL when present.
Mark the physically final HIL task with `panel_role=PHYSICALLY_FINAL_HIL`, and
never place a later correction behind it. The default steer boundary is
before the next HIL, and the full task panel persists until that HIL.

If the host task panel disappears after a token continuation, stalled Goal,
compaction, browser or Codex restart, session continuation, resume, or State
Travel entry, re-project the complete canonical Plan Lane first. Do this before
source inspection, mutation, testing, Git activity, or another lifecycle call.
Keep exactly one active row, preserve every completed and pending description
unabridged, and drop the panel only after the physically final HIL decision and
all decision-dependent work are complete.

The host Goal remains attached to the same canonical Plan Lane, active source
boundary, and single-writer session throughout that interval. A UI crash,
token wait, required user input, or HIL wait pauses dependent work only and may
not complete the Goal. Usage reporting is separate accounting and has no task
status effect. Every reconstruction includes completed-but-still-governing
rows, the one active row, and all pending rows.

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
   silently complete backlog history. Record each steer through
   `pv_plan_steer_delta`, linking it to an existing row or inserting a new row
   before the next governed HIL.
2. Classify exactly one bounded task and record visible activities.
   Close an ordinary executable row only from one current-run, exact-task,
   exact-acceptance checkpoint receipt. The successor must be the first queued
   canonical row, and the advance must preserve an absent candidate/HIL and an
   unchanged pointer. A PASS string from another task, run, or partial
   acceptance set cannot advance the Plan.
3. Use accepted evidence as entry truth and live repository evidence for
   source changed after entry.
4. Confirm final Codex source state with
   `HOST_SANDBOX_FINAL_STATE_CONFIRMED`.
5. `task_complete_and_refresh` seals the final unaccepted candidate. A changed
   schema/tool identity permits a declared full fallback; otherwise reuse
   unchanged lane and chunk artifacts.
   If a client or host process is interrupted in `EXIT_BUILDING`, the same
   call may recover only when no candidate was sealed. Recovery is
   receipt-backed, remains in `EXIT_BUILDING` until sealing succeeds, and
   cannot move the accepted pointer or infer HIL.
6. Present exactly: `APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`,
   `ROLLBACK`, `REJECT`, or `FAIL`. Stop for the human decision.
7. Natural-language continuation or acceptance intent may be classified and
   appended to Chat Lineage, but classification never promotes. Only an exact
   first `/evi-build` argument of `APPROVE` may route to `pv_fuse`.
8. Only an exact sealed handoff plus an explicit user or genuine
   context-exhaustion trigger can authorize `/evi-state-travel` in a fresh
   destination host. Acceptance is not a prerequisite for unfinished-work
   continuity.

Git delivery is batched by dependency-coherent integration checkpoints, not by
individual Delta row or file. Every row still receives its own acceptance
evidence and lifecycle transition, PREPARE/capture/retrieval receipt, native
PV status/backlog/query reads, task/row/current-change classification, visible
ChatLineage activity, and full persistent Plan/CURRENT CHANGE reprojection.
When a logical bundle of coupled rows is
implemented and locally verified, one behavior-bearing commit synchronizes
plugin source, root README, affected repository-level docs/manifests/workflows/
tests, and the public Plan/Delta projection; one governed push then runs the
complete configured clean-checkout Git/CodeQL/preview route, exact-SHA package
proof, same-selector stable update, and installed-host readback. Derive a small
number of bundles from shared source/schema/runtime/test boundaries; never use a
fixed quota, conceal a failed row, or mark an unimplemented row done. A local or
dirty-worktree package is rehearsal evidence only and must never update the
stable slot. Update that single selector once per bundle, solely from the exact
Git commit package after every configured check and exact-SHA preview gate has
passed. Before that update, emit a cross-Delta verification matrix binding each
included task ID to changed source/schema/runtime/docs surfaces, focused local
tests, clean-checkout remote checks, installed-host checks, outcome, and exact
failure owner. Any included-row failure fails the bundle closed.

Default reads use accepted truth and disclose live freshness. Explicit
candidate reads remain labeled `UNACCEPTED_CANDIDATE`. Use bounded fetches and
allowlisted queries; never execute arbitrary source SQL.

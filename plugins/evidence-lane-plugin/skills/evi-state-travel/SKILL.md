---
name: evi-state-travel
description: User-requested or context-exhaustion recovery from the exact verified governed work boundary.
---

# Evidence Lane State Travel

Run only when the user explicitly requests State Travel or the current host
context is genuinely exhausted. A prepared handoff is eligibility evidence,
not an instruction to travel.

After the destination hook seals its transport envelope and the installed
lifecycle skill runtime consumes it into a PREPARE/entry receipt, the skill
calls native `pv_status`, `pv_task_backlog`, and one bounded `pv_query`, then
preserves the active governed session. State Travel does
not require an accepted candidate. Its default is
`UNFINISHED_VERIFIED_WORK` whenever the session is not at an accepted lifecycle
state. Build the `resume_contract` from the canonical Plan Lane and visible task
panel, including completed rows, the one exact in-progress row, pending rows,
all additive Deltas, and the exact resume step. Never substitute historical
accepted Delta-ledger rows for the current execution plan. A steer Delta is
`BEFORE_NEXT_HIL` unless the user explicitly names another boundary.
The State Travel seal must derive and include every steer Delta persisted on the
active Plan Lane. Fail closed if an explicit task list or additive-Delta list
drops or changes one. A `PHYSICALLY_FINAL_HIL` row must remain physically final.

The sealed resume or direct-entry contract must carry the executable
persistent-panel reactivation law. The native Plan and the Step Task List are
different projections. Before host Plan acceptance, EVI Plan returns only the
small whole-authority reprojection prompt; it does not call `update_plan`,
serialize the fixed batch, or create a Goal. Only after the user pastes that
prompt in native Plan mode and clicks the visible **Implement this plan**
control may bounded Evidence Plan verification validate the exact complete
native ledger and relock the Step Task List.

The Step Task List uses one projector only. Its first visible element is the
compact PV/ACTIVE/BATCH/NEXT_HIL/FINAL_HIL header. Its remaining elements are
the exact canonical fixed-batch Delta rows, up to nine and exactly nine when
the canonical batch contains nine. The projector derives identity from the
persisted canonical batch, never a sliding active-row window. Every Delta uses
the established four-line contract: two compact metadata/classification lines
plus at most two human-readable brief lines. Delete or disable any fallback
that emits rows without the header, verbose expanded rows, duplicate/de-duped
rows, or a sliding window.

After Phase 5 passes, apply the same single-projector relock after every
token-driven continuation, stalled Goal, context compaction, supported
reconnect, renderer reload, Plan/Changes-surface loss, browser or Codex restart,
session continuation, or session resume. Keep the native right-side Plan
artifact and exact task/worktree-bound Changes surface present through every
pause and HIL. A missing, partial, stale, duplicate, or silently dropped
surface is a continuity failure: atomically relock it before work through the
supported native host action, or fail closed when that capability is
unavailable. Do not claim the plugin can prevent a host crash. Drop the
surfaces only after the human explicitly marks the Goal complete, or after an
exact task-completion-and-State-Travel handoff passes and the successor owns
the complete projection.

The Step Task List continuity header is visible element one and shows only the
accepted PV/pointer generation, absolute ACTIVE row, canonical fixed batch,
next HIL boundary, and physical-final HIL. It is not outside the list and never
becomes an eleventh item. Detailed queued HIL records, proposed PV identities,
six-way choices, and dependency/continuation connections belong exclusively to
the Evidence Lane project renderer/resource and must never be mixed into the
Step Task List.

Every host-visible Delta row uses the sealed compact four-line projection:
line 1 contains exact row number, stable task/Delta ID, lifecycle status and
classification; line 2 contains Plan group, declared logical commit batch,
dependencies, Git stage/provenance, version, branch and panel role; lines 3-4
contain at most two human-readable brief lines. Preserve
explicit metadata exactly. When an older Plan never declared a batch, Git
stage, current version, or current branch, render `UNASSIGNED` or
`NOT_DECLARED`; never infer one. When current non-superseded task text and
linked corrections conflict, keep the immutable wording and render
`CONFLICTING_DECLARATIONS@RECONCILIATION_REQUIRED` until an exact linked
`CURRENT_VERSION=...` or `CURRENT_BRANCH=...` marker resolves it. Exclude
DROPPED and SUPERSEDED history from executable numbering, dependency fallback,
and effective markers. Keep dependencies explicit, use the prior executable row
only as the deterministic linear fallback, and never embed raw Delta JSON in a
visible label. This law applies to every governed project and corpus, not only
the Evidence Lane plugin project.

SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact,
PostCompact, Stop, and best-effort SessionEnd hooks remain lifecycle-only. They
may transport a sealed content-addressed host-Plan rehydration request, but
must not execute `update_plan`; the active skill owns all native PV reads and
host behavior. PermissionRequest remains conditional on a proven host
capability, and subagent hook events are out of scope. After a steer is sealed
with `pv_plan_steer_delta`, repeat the three native reads, validate the complete
ledger, and inspect the receipt's linked task. Synchronize the current host
window once only when that linked task is inside it; otherwise retain the Delta
in native authority and defer host synchronization until its window becomes
active. This conditional host-window synchronization never invokes or aliases
`/evi-refresh`.

For a Codex handoff, also capture the exact non-secret model, submodel,
reasoning-effort, reasoning-speed, and optional service-tier selectors. The
plugin cannot change host-owned selectors; the destination must use the same
profile and `pv_state_travel_resume` must reject a mismatch before rebinding.
The sealed resume and next-action receipts must carry the exact execution and
writer boundary: one governed project, one live writer, linear execution,
evidence-first verification, and the verified host execution profile. At a
genuine State Travel entry only, read-only recovery agents may help reconstruct
visible state. After entry, do not start a subagent, alternate-checkout writer,
background mutation, or second browser profile unless the user explicitly
changes that boundary.

The separately named direct/forced same-worktree recovery route is available
only when the user explicitly authorizes exact dirty-work continuity and no
eligible fresh sealed handoff exists. The destination must be a genuinely new
native Codex local-project task, never a fork or `Continued from chat`. Call
`pv_state_travel_direct_force_same_worktree` exactly once with only the
authoritative-source task UUID, runtime-donor task UUID, destination task UUID,
and exact visible destination title. The server must mint the opaque replay
guard and atomically derive the task deep links, project/worktree, sole writer,
accepted-pointer baseline, live dirty path/content identities, canonical Plan
and fixed 1+9/HIL anchors, installed plugin/catalog, runtime attestation, Flash,
hooks state, and execution profile under the State Travel lock. The public MCP,
SDK, skill, and command surfaces must not accept a caller binding object,
nonce, PID/runtime ID, pointer/PV fields, source hashes, or Plan hashes. The
legacy full-binding normalizer is private compatibility code only and may not
be used to reconstruct a public call. It must fail closed on mismatch or replay
and must not call
`pv_state_travel_prepare`, consume `pv_state_travel_resume`, fabricate a sealed
transport receipt, infer HIL, create a candidate, or move the pointer.

`STATE_TRAVEL_DESTINATION_ENTRY_LAW` is permanent public plugin behavior, not a
task-local correction. Every fresh destination must execute this exact order:
bind the host-assigned task UUID/deep link plus governed project, session,
worktree, sole-writer and execution-profile identities; verify the accepted
pointer, exact live dirty-work identity, installed route/runtime/Flash and
canonical Plan SQLite; consume exactly one explicitly authorized entry route;
then return the small EVI Plan whole-authority reprojection prompt. The plugin
must stop for the user's native Plan paste and visible **Implement this plan**
click. Only after a distinct click receipt and bounded Plan verification may it
resume the carried Goal and relock the one canonical fixed header + up-to-nine
Step Task List. No phase may reconstruct or serialize Plan SQLite, substitute a
thread-history/fallback projector, infer host acceptance, or skip directly from
task creation to Goal resume. For direct same-worktree entry, hash oversized Git
diffs as bounded streams and retain only digest/byte-count evidence; never
materialize a binary diff merely to calculate its SHA-256.

If the user explicitly requests accepted context, set `entry_mode` to
`ACCEPTED_ENTRY`. Otherwise do not clear an active task, pending correction,
candidate, or resume row. Call `pv_state_travel_prepare` to seal the pointer
base, accepted package if one exists, candidate if one exists, live source,
Plan Lane, task state, additive Deltas, and execution profile.

An orphaned stale-host PREPARED handoff may be superseded only by one exact
same-session user correction before it is consumed and while the accepted
pointer remains unchanged. Require the old handoff ID, old handoff SHA-256,
exact old origin host-session ID, scope `ORPHANED_STALE_HOST_TASK`, confirmation
`SUPERSEDE_ORPHANED_PREPARED_HANDOFF`, and the new exact host-session binding.
Preserve the old receipt in immutable history, append the supersession event,
and prepare exactly one replacement. Never resume the stale handoff, fabricate
cancellation, retry a mismatched correction, infer HIL, or move a pointer.

Before entering a genuinely fresh Codex task, use the host-supported
`Continue in new chat` operation programmatically when available. Create
exactly one destination and apply the canonical Task X to Task X+1 naming law.
Fail closed when the host cannot prove that supported action; do not fabricate
a destination or ask the user to click it. Seal and verify the source and
destination task IDs and `codex://threads/<id>` deep links, exact worktree
including dirty/untracked identity, governed project/session, accepted
pointer, active row, host-session identity, plugin build, and execution
profile. A title or CWD alone is never binding authority. When the supported
host action first returns a queued `clientThreadId`, resolve it to exactly one
live destination task UUID and deep link before resume. Bind that resolution
receipt to the handoff and retain duplicate or archived task identities only
as immutable history. Zero or multiple live matches fail closed.

State Travel is a task transition, never an application-restart mechanism.
Before the one-shot native resume, require a host-continuity receipt that binds
the exact source and destination UUID/deep-link pair, the initial destination
shell's source/destination UUIDs, the host creation result, one unchanged host
process instance, and exactly one live canonical destination-title identity.
The receipt must prove zero app restarts, renderer reloads, UI freezes,
unexpected navigation/task activation, and background-agent activation during
destination creation. A wrong nested source task (including a stale Task4/Task5
shell), duplicate title identity, unexpected task opening, app restart/reload,
or missing host proof is `STATE_TRAVEL_HOST_CONTINUITY_FAILURE`. Stop before
handoff consumption, never retry that one-shot route, preserve all bytes, and
require an explicit fresh correction. A restart/reattach helper, tunnel helper,
scheduled recovery helper, or subagent may not satisfy or bypass this proof.

The same receipt must prove `BOUNDED_HANDOFF_ENVELOPE_ONLY` hydration, zero
unbounded thread-history reads, zero collaboration-overlay hydration, and
`CANONICAL_PLAN_LANE_NOT_THREAD_HISTORY` as the reconstruction source. Do not
open subagent panels, hydrate avatar/agent overlays, or read the source task's
full chat scrollback during destination creation or Plan recovery. A
thread-hydration overflow or host React-root rerender is a first-class host
continuity failure even when the root Codex process remains alive. Fail closed,
keep the handoff unconsumed, and reproject the exact native Plan before work;
never claim the plugin can prevent a host-owned renderer reset.

In that freshly bound Codex task, call `pv_state_travel_resume` exactly once as
the first State Travel lifecycle action. It atomically
verifies runtime doctor, locked ENV15/UOP15 Flash, new host ID, pointer base,
package/candidate seals, live-source identity, Plan Lane, and execution profile.
`UNFINISHED_VERIFIED_WORK` continues at `RESUME_EXACT_UNFINISHED_STEP` without
reclassification or HIL replay. `ACCEPTED_ENTRY` stops at
`WAITING_FOR_NEXT_USER_COMMAND`. The first matching handoff plus destination
host-session tuple consumes the resume globally under a durable cross-process
lock. An identical later call returns `ALREADY_CONSUMED_NO_REBIND` with the
original consumption receipt and an incident receipt; it must not repeat Boot,
Flash, host rebinding, source work, candidate/HIL work, or pointer activity. A
different destination tuple, queued-ID resolution, or execution profile fails
closed and can never rebind the consumed handoff.

For unfinished work, execute exactly five ordered destination phases:

1. Create exactly one fresh native destination and bind exact task UUID/deep
   link, project, governed session, worktree, sole writer, execution profile
   and bounded host identity. Source-task closure ends only its task-boundary
   Goal when explicitly authorized; it is not HIL or implementation completion.
2. Run installed atomic Boot/locked Flash and exactly one authorized entry
   verification: the sealed route consumes `pv_state_travel_resume` once, while
   the no-seal direct route consumes
   `pv_state_travel_direct_force_same_worktree` once through its server-derived
   high-level contract. Never run a caller-built preflight, retry, or substitute
   routes. Verify pointer/package, runtime, source, profile,
   plugin, worktree, canonical Plan, sole ACTIVE row and physical-final HIL.
   After that exact PASS, call `render_runtime_panel` and
   `render_project_panel` once per tool for the State Travel authority
   presentation. This is the single State Travel render allowance. Never call
   either renderer before entry PASS, again during Plan acceptance/Goal resume,
   or as verification, discovery, retry, fallback, restart, or rehydration.
3. After Phase 2 PASS, EVI Plan reads only bounded live Plan status from the
   existing canonical Plan SQLite and returns one small pasteable whole-
   authority reprojection prompt. It never serializes the fixed Step Task List,
   reconstructs SQLite, calls host `update_plan`, auto-pastes, creates a Goal,
   or mutates source. Stop for the user to paste the exact prompt with native
   Plan selected.
4. Only the user's native Plan paste may cause the host to display the visible
   **Implement this plan** control. Stop there. Never click or accept it for the
   user, infer acceptance from an empty host receipt, or treat it as Evidence
   Lane HIL.
5. Only after a distinct explicit user click receipt, automatically apply
   `evidence-lane-plugin:source-command-evi-plan` and run bounded Evidence Plan
   verification: re-read `pv_status`, exact/bounded
   `pv_task_backlog`, and one bounded `pv_query`; compare canonical and
   executable hashes, complete range, sole ACTIVE row, next HIL, physical-final
   HIL and unchanged pointer. On PASS only, hook or resume exactly one carried
   unfinished Goal, then hydrate or relock the single canonical fixed header +
   up-to-nine Delta Step Task List and persistent Changes panel. Continue source
   work at the exact active row. Never create a competing Goal.

Record ordered phase receipts. No Goal, source inspection/mutation, testing,
Git, install, candidate, HIL, or pointer work may occur before Phase 5 bounded
verification passes. Fail closed on a missing host capability, identity
mismatch, skipped phase, duplicate destination, duplicate resume, or fabricated
Plan acceptance. An already-consumed identical replay is the sole non-error
duplicate outcome and performs no rebind; every conflicting duplicate remains
fail-closed.

The two and only two user gates are (a) paste the returned small prompt with
native Plan selected and (b) click the host's visible **Implement this plan**
control. Do not ask the user to type `/pl`, `/evi-plan`, or a Goal prompt. If
the host cannot expose or attest the native Plan selector/control, record
`HOST_MODE_SELECTOR_UNAVAILABLE` and fail closed; do not fabricate activation.
The MCP cannot call host-owned Plan or Goal controls. An empty host action
receipt, Sources/icon presence, or native backlog readback is not visibility or
acceptance proof.

The Phase-3 payload is the direct receipt's
`destination_orchestration.whole_plan_reprojection` or the equivalent sealed
receipt field. Validate exact destination identity, canonical/executable
projection hashes, contiguous executable range, sole ACTIVE row, next HIL and
physical-final HIL. It contains zero serialized Plan rows. The Phase-5 payload
is the bounded verification receipt plus the fixed-batch Step Task List
projection. Native whole-Plan activation and the fixed 1+9 Step Task List must
never be substituted for one another.

Codex may project Plan Lane into its native Goal and task panel. Never invent
cross-host UI parity, build, Fuse, infer approval, or move a pointer merely
because a handoff exists.

State Travel never completes a Goal by itself. When the human uses the exact
visible `MARK GOAL COMPLETE` command with disposition
`COMPLETE_THIS_TASK_AND_STATE_TRAVEL`, close only the current task's Goal
boundary and carry the unfinished governed objective into the exact bound
successor. No HIL, Fuse, pointer, Git, install, merge, or deployment authority
is implied; specifically, Goal completion is not HIL approval. Without that
exact human disposition, preserve the Goal as active,
paused, or stalled according to host truth.
The other human-only disposition, `COMPLETE_FULLY`, closes the whole Goal and
does not invoke State Travel.

For either human completion disposition, render completion telemetry only
through `build_rich_goal_completion_metrics_receipt` after the native Goal
boundary reports completion. Reuse a validated persisted rich receipt when the
Goal was already complete, keep host-accounted Goal tokens separate from raw
model traffic, keep reasoning output inside output, and report unavailable
telemetry through structured missing fields. The older
`build_goal_usage_receipt` identifier is a non-executing `OBSOLETE_ROUTE`
tombstone and never a fallback.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-state-travel`.
`MCP_ROUTING_FAIL_CLOSED`: if the bundled `evidence-lane` dependency, an exact
tool, or a required result is missing or ambiguous, stop and report it; never
rewrite prefixes, substitute a tool, retry State Travel, reorder a write, or
infer success.

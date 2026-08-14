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

The sealed resume contract must carry the executable persistent-panel
reactivation law. At the destination, re-project the exact complete task list
with the host `update_plan` tool after those native reads and before source
inspection, mutation,
testing, Git activity, or another lifecycle call. Apply the same ordering after
every token-driven continuation, stalled Goal, context compaction, browser or
Codex restart, session continuation, or session resume. A non-empty panel must
have exactly one in-progress row; preserve its order and every completed and
pending description unabridged; keep it visible through every pause and HIL;
drop it only after the physically final six-way HIL is decided and every
decision-dependent action is complete.

Every host-visible task row uses the sealed universal label projection: exact
row number, stable task/Delta ID, exact description, lifecycle status, task
classification, Plan group, declared logical commit batch, dependencies, and
  Git commit stage plus provenance, current version, current branch, and panel
  role (`STANDARD`, an exact declared gate, or `PHYSICALLY_FINAL_HIL`). Preserve
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
capability, and subagent hook events are out of scope. After a steer is sealed with
`pv_plan_steer_delta`, repeat the three native reads and the complete panel
projection before continuing.

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

If the user explicitly requests accepted context, set `entry_mode` to
`ACCEPTED_ENTRY`. Otherwise do not clear an active task, pending correction,
candidate, or resume row. Call `pv_state_travel_prepare` to seal the pointer
base, accepted package if one exists, candidate if one exists, live source,
Plan Lane, task state, additive Deltas, and execution profile.

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

1. Programmatically create exactly one fresh destination and bind both task
   identities plus the complete governed authority described above.
2. Run atomic Boot/locked Flash and the exact-once native State Travel resume;
   verify the returned receipt, pointer/package, runtime, source, profile,
   plugin build, worktree, canonical Plan, sole active row, and physically final
   HIL. Never retry a failed one-shot resume; classify an identical post-PASS
   call only through `ALREADY_CONSUMED_NO_REBIND`.
3. Restore the complete unabridged host Plan with `update_plan`, including the
   universal row metadata labels. Surface the host's Plan acceptance control
   and stop at `WAITING_FOR_EXPLICIT_HOST_PLAN_ACCEPTANCE`. Never accept it
   automatically. Host Plan acceptance is not Evidence Lane HIL, creates no
   candidate, and cannot move a PV pointer.
4. Only after the explicit Plan acceptance is observed, automatically apply
   `evidence-lane-plugin:source-command-evi-plan`: read native `pv_status`,
   `pv_task_backlog`, and one bounded prompt-relevant `pv_query`; validate
   canonical `PLAN_LANE` authority, contiguous rows, exact metadata labels, the
   sole active row, and the physically final HIL row; then validate the same
   complete host projection. Do not write a replacement Plan Lane or duplicate
   rows when canonical authority already exists.
5. Only after phases 1–4 pass, create or resume the transferred plugin Goal
   from the returned `goal_start_prompt`, then continue source work at the
   exact active row.

Record ordered phase receipts. No Goal, source inspection/mutation, testing,
Git, candidate, HIL, or pointer work may occur before Phase 3 acceptance and
Phase 4 verification. Fail closed on a missing host capability, identity
mismatch, skipped phase, duplicate destination, duplicate resume, or fabricated
Plan acceptance. An already-consumed identical replay is the sole non-error
duplicate outcome and performs no rebind; every conflicting duplicate remains
fail-closed.

Do not ask the user to type `/pl`, `/evi-plan`, or the Goal prompt between
State Travel phases. The sole permitted user gate is the visible host Plan
Accept control in Phase 3. If the Codex API cannot mutate or attest the native
Plan-mode selector, record `HOST_MODE_SELECTOR_UNAVAILABLE`, preserve the
visible Plan projection, and do not fabricate selector activation. The MCP
cannot call host-owned `update_plan` or Goal controls itself; the active skill
must perform those host actions linearly around the explicit Plan-acceptance
gate after resume PASS.

The resume receipt's `host_plan_rehydration` object is the Phase-3 payload.
Validate the exact project/session/destination-task binding, canonical and
executable projection hashes, contiguous row count, sole active row, exact
metadata-rich labels, and physically final HIL. Call host `update_plan` only
when its action requires it, using `projection.items` byte-for-byte. Then record
only a genuinely observed `host.plan.observation`. An empty host action receipt,
Sources or icon presence, or native backlog readback cannot prove that the
right-side Plan artifact is visible, and only a distinct explicit Accept-control
event can clear the Phase-3 acceptance gate.

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

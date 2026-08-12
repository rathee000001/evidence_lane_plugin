---
name: evi-state-travel
description: User-requested or context-exhaustion recovery from the exact verified governed work boundary.
---

# Evidence Lane State Travel

Run only when the user explicitly requests State Travel or the current host
context is genuinely exhausted. A prepared handoff is eligibility evidence,
not an instruction to travel.

After the destination lifecycle PREPARE receipt, the skill—not a hook—calls
native `pv_status`, `pv_task_backlog`, and one bounded `pv_query`, then
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

SessionStart, UserPromptSubmit, and PostToolUse hooks remain lifecycle-only.
They must not embed the full Plan Lane or direct `update_plan`; the active skill
owns all native PV reads and host behavior. After a steer is sealed with
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

In a genuinely fresh Codex task, call `pv_state_travel_resume`. It atomically
verifies runtime doctor, locked ENV15/UOP15 Flash, new host ID, pointer base,
package/candidate seals, live-source identity, Plan Lane, and execution profile.
`UNFINISHED_VERIFIED_WORK` continues at `RESUME_EXACT_UNFINISHED_STEP` without
reclassification or HIL replay. `ACCEPTED_ENTRY` stops at
`WAITING_FOR_NEXT_USER_COMMAND`.

Codex may project Plan Lane into its native Goal and task panel. Never invent
cross-host UI parity, build, Fuse, infer approval, or move a pointer merely
because a handoff exists.

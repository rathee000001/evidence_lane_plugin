---
name: evi-state-travel
description: User-requested or context-exhaustion recovery from the exact verified governed work boundary.
---

# Evidence Lane State Travel

Run only when the user explicitly requests State Travel or the current host
context is genuinely exhausted. A prepared handoff is eligibility evidence,
not an instruction to travel.

Call `pv_status`, then preserve the active governed session. State Travel does
not require an accepted candidate. Its default is
`UNFINISHED_VERIFIED_WORK` whenever the session is not at an accepted lifecycle
state. Build the `resume_contract` from the canonical Plan Lane and visible task
panel, including completed rows, the one exact in-progress row, pending rows,
all additive Deltas, and the exact resume step. Never substitute historical
accepted Delta-ledger rows for the current execution plan. A steer Delta is
`BEFORE_NEXT_HIL` unless the user explicitly names another boundary.

For a Codex handoff, also capture the exact non-secret model, submodel,
reasoning-effort, reasoning-speed, and optional service-tier selectors. The
plugin cannot change host-owned selectors; the destination must use the same
profile and `pv_state_travel_resume` must reject a mismatch before rebinding.
At the handoff entry only, read-only recovery subagents may help reconstruct
visible state. After entry, preserve one sole writer and use subagents only on
an explicit user command.

If the user explicitly requests accepted context, set `entry_mode` to
`ACCEPTED_ENTRY`. Otherwise do not clear an active task, pending correction,
candidate, or resume row. Call `pv_state_travel_prepare` to seal the pointer
base, accepted package if one exists, candidate if one exists, live source,
Plan Lane, task state, additive Deltas, and execution profile.

In a genuinely fresh task or chat, call `pv_state_travel_resume`. It atomically
verifies runtime doctor, locked ENV15/UOP15 Flash, new host ID, pointer base,
package/candidate seals, live-source identity, Plan Lane, and execution profile.
`UNFINISHED_VERIFIED_WORK` continues at `RESUME_EXACT_UNFINISHED_STEP` without
reclassification or HIL replay. `ACCEPTED_ENTRY` stops at
`WAITING_FOR_NEXT_USER_COMMAND`.

Codex and ChatGPT are separate host universes. Codex may project Plan Lane into
its native Goal and task panel. ChatGPT has no Codex `/pl`, Goal, or task-panel
contract; it reads and appends through the same persistent plugin runtime in
its mounted host storage under the shared append-only lane and ENV/Exit-Slip
laws. Never invent cross-host UI parity, build, Fuse, infer approval, or move a
pointer merely because a handoff exists.

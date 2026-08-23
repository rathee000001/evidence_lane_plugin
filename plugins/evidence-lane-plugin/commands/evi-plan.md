---
description: Pair a finished Codex Plan-mode plan with the canonical Evidence Lane Plan Lane and Goal.
---

# Evidence Lane Plan Lane

Use this sidecar only for Codex native Plan mode. It is not a seventh primary
Evidence Lane lifecycle control.

## Preflight

1. The skill, not a hook, reads native `pv_status`, `pv_task_backlog`, and one
   bounded prompt-relevant `pv_query`, preserves the active lifecycle position,
   and verifies their paired `agent_configuration` authority/source-chain
   hashes. These explicit routes must work with hooks disabled.
2. Confirm the host is Codex.
3. For an ordinary planning turn, confirm native Plan mode is active. For a
   State Travel Phase-4 invocation, require the sealed destination contract,
   complete host Plan projection, and explicit host Plan acceptance instead;
   do not ask the user to type `/pl` or `/evi-plan`. If the API cannot attest
   its selector, record `HOST_MODE_SELECTOR_UNAVAILABLE` without fabricating
   activation. Perform no Plan Lane write when canonical authority already
   exists.
4. Require a final visible ordered plan with stable task IDs, exact outcomes,
   bounded paths/tools, acceptance checks, and stop conditions.

## Plan

State that the visible final plan will become the canonical Plan Lane. Its
Codex Goal and task panel are host projections of that same authority. No
candidate, HIL decision, pointer movement, deployment, or Fuse occurs.

## Commands

1. Call `mode_classify` for Planning mode without changing lifecycle state.
2. Call `pv_plan_tasks` with the final ordered contracts, exact actor,
   `host_kind=CODEX_DESKTOP` (or the exact Codex host), and `host_mode=PLAN`.
3. For later user steers, call `pv_plan_steer_delta`:
   - linked steer: pass `linked_task_id`; preserve the row and count;
   - unrelated steer: pass one complete `new_task_contract`; insert a new row
     before the next HIL when present, or pass `insert_before_task_id` in that
     contract for an exact gate;
   - mark the physically final HIL task with
     `panel_role=PHYSICALLY_FINAL_HIL`; never insert a steer behind it;
   - omit `boundary` to use `BEFORE_NEXT_HIL`.
4. After a Plan-row/status mutation or an in-window steer, repeat `pv_status`,
   `pv_task_backlog`, and the bounded native `pv_query`; validate the returned
   Plan Lane; then pass the native fixed `host_update_plan_contract` to the host
   `update_plan` tool unchanged. A read or outside-window linked correction does
   not trigger gratuitous hydration. In a State Travel destination, this step
   runs automatically only after explicit host Plan acceptance. Hooks must
   never perform or instruct this behavior.
   `NATIVE_HOST_PLAN_PROJECTION_ONLY_LAW` permanently forbids hand-written
   summaries, reconstructed labels, sliding windows, and generic fallback
   projections. Panel loss relocks the same persisted contract and exact
   source-window UI fingerprint.

## Verification

Require `goal_projection.canonical_authority=PLAN_LANE`, contiguous row numbers,
exactly one in-progress row for an active executable Goal, an exact projection
hash, and
`persistent_until=NEXT_SIX_WAY_HIL_PRESENTED`. Verify the returned host contract
states that MCP cannot change the native Goal or model/mode selectors.

The single native host projection is exactly one compact continuity header plus
at most nine persisted Delta rows. Each Delta uses the native four-line compact
contract. Preserve its declared metadata and dependencies without reconstructing
or expanding rows in model context. Exclude immutable DROPPED and SUPERSEDED
history from executable numbering and dependency fallback. Never place raw
linked Delta JSON in a host step label. If the native route or host
`update_plan` tool is unavailable, fail closed rather than treating a hook
receipt, sliding/fallback projector, verbose renderer, or internal SQLite lookup
as a replacement.

## Summary

Display the Plan Lane row count and active row. Keep the fixed native task panel
visible through every steer until the next six-way HIL.

## Next Steps

For an ordinary invocation, display the returned short `goal_start_prompt` as
the handoff supported by that host. For State Travel Phase 4, do not request a
manual paste: after verification passes, Phase 5 automatically calls the
supported Goal action in the same destination. Host Plan acceptance remains
separate from Evidence Lane HIL and never moves a PV pointer.

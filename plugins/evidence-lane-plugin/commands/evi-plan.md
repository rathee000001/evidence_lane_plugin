---
description: Pair a finished Codex Plan-mode plan with the canonical Evidence Lane Plan Lane and Goal.
---

# Evidence Lane Plan Lane

Use this sidecar only for Codex native Plan mode. It is not a seventh primary
Evidence Lane lifecycle control.

## Preflight

1. After the lifecycle PREPARE receipt, the skill—not a hook—reads native
   `pv_status`, `pv_task_backlog`, and one bounded prompt-relevant `pv_query`,
   and preserves the active lifecycle position.
2. Confirm the host is Codex.
3. Confirm native Plan mode is active. If it is not, perform no Plan Lane write
   and return: `Type /pl, finish the plan, then run /evi-plan again.`
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
4. After the Plan write or every steer, repeat `pv_status`, `pv_task_backlog`,
   and the bounded native `pv_query`; validate the returned Plan Lane; then call
   the host `update_plan` tool with the complete exact projection. Hooks must
   never perform or instruct this behavior.

## Verification

Require `goal_projection.canonical_authority=PLAN_LANE`, contiguous row numbers,
exactly one in-progress row for an active executable Goal, an exact projection
hash, and
`persistent_until=NEXT_SIX_WAY_HIL_PRESENTED`. Verify the returned host contract
states that MCP cannot change the native Goal or model/mode selectors.

Every host label is exactly
`Row <canonical row> / <task ID> — <exact description>`. Never place raw linked
Delta JSON in a host step label. If the native route or host `update_plan` tool
is unavailable, fail closed rather than treating a lifecycle hook receipt or
internal SQLite lookup as a replacement.

## Summary

Display the Plan Lane row count and active row. Keep the full native task panel
visible through every steer until the next six-way HIL.

## Next Steps

Display only the returned short `goal_start_prompt` as the copy/paste handoff.
The user pastes it into the Codex Goal to begin or continue execution.

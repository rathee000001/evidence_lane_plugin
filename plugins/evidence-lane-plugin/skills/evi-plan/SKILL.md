---
name: evi-plan
description: Pair a finished Codex Plan-mode plan with the canonical Evidence Lane Plan Lane and Goal.
---

# Evidence Lane Plan Lane

Use this native skill sidecar only for Codex Plan mode. It is not a seventh
primary Evidence Lane lifecycle control and it owns no workflow outside the
internal SDK.

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns host reasoning and projection behavior;
MCP is the native action transport and hooks provide lifecycle receipts only.

## Preflight

1. Read native `pv_status`, `pv_task_backlog`, one bounded prompt-relevant
   `pv_query`, and the prompt-relevant six-authority `search`. Preserve the
   active lifecycle position and verify their paired `agent_configuration`
   authority/source-chain hashes. These explicit routes must work with hooks
   disabled.
2. Confirm the host is Codex.
3. For an ordinary planning turn, confirm native Plan mode is active. For a
   State Travel Phase-4 invocation, require the sealed destination contract,
   complete host Plan projection, and explicit host Plan acceptance instead;
   do not ask the user to invoke a legacy command. If the API cannot attest its
   selector, record `HOST_MODE_SELECTOR_UNAVAILABLE` without fabricating
   activation. Perform no Plan Lane write when canonical authority exists.
4. Require a final visible ordered plan with stable task IDs, exact outcomes,
   bounded paths/tools, acceptance checks, and stop conditions.

## Plan

State that the visible final plan will become the canonical Plan Lane. Its
Codex Goal and task panel are host projections of that same authority. No
candidate, HIL decision, pointer movement, deployment, or Fuse occurs.

## SDK and MCP route

1. Call `mode_classify` for Planning mode without changing lifecycle state.
2. Call `pv_plan_tasks` with the final ordered contracts, exact actor,
   `host_kind=CODEX_DESKTOP` (or the exact Codex host), and `host_mode=PLAN`.
3. For later user steers, call `pv_plan_steer_delta`:
   - linked steer: pass `linked_task_id`; preserve the row and count;
   - unrelated steer: pass one complete `new_task_contract`; insert it before
     the next HIL when present, or use `insert_before_task_id` for an exact gate;
   - keep a `PHYSICALLY_FINAL_HIL` row physically final; and
   - omit `boundary` to use `BEFORE_NEXT_HIL`.
4. After a Plan-row/status mutation or an in-window steer, repeat the native
   reads, validate the Plan Lane, and pass the returned
   `host_update_plan_contract` to host `update_plan` unchanged. A read or
   outside-window linked correction does not trigger gratuitous hydration.

`NATIVE_HOST_PLAN_PROJECTION_ONLY_LAW` forbids hand-written summaries,
reconstructed labels, sliding windows, generic fallback projections, and any
command adapter. The native skill routes through the internal SDK registry and
the exact MCP actions only.

## Verification

Require `goal_projection.canonical_authority=PLAN_LANE`, contiguous row numbers,
exactly one in-progress row for an active executable Goal, an exact projection
hash, and `persistent_until=NEXT_SIX_WAY_HIL_PRESENTED`. Verify that MCP cannot
change the native Goal or model/mode selectors.

The host projection is one compact continuity header plus at most nine
persisted Delta rows. Preserve declared metadata and dependencies without
reconstructing rows in model context. Exclude immutable DROPPED and SUPERSEDED
history from executable numbering. If the native route or host `update_plan`
tool is unavailable, fail closed.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-plan`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

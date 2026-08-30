---
name: evi-rollback
description: Evidence Lane logical rollback across Plan-stamped full-PV and sub-PV states, with hard ZIP restore kept separate and explicit.
---

# Evidence Lane Rollback

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/references/shared-boundaries.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

Call `pv_rollback`. For a live-root project, resolve a bare rollback to the
session entry PV or accept an exact full-PV or auto-accepted sub-PV state ref.
Require pointer-generation CAS, then move only the logical rollback cursor.
Acceptance authority is the Plan dual-HIL stamp for full PVs and the Plan
SQLite sub-PV acceptance row for sub-PVs. The accepted pointer, candidate,
active task, Goal, source bytes, and monotonic next-PV ordinal remain unchanged.

Full-PV navigation links the target to the cumulative Project Overlay for
PV-to-PV blast-radius comparison. Sub-PV navigation never claims an Overlay;
its exact Delta checkpoint/acceptance receipt is the state boundary. Ordinary
rollback queries the live root, Plan, lanes, and Overlay only. It never opens
or queries the rotating `accepted/` folder.

Keep three rollback authorities separate:

Hard filesystem restore remains distinct from logical cursor movement and from
Git branch/commit restore.

1. `LOGICAL_LIVE_ROOT_STATE` is the current `pv_rollback` cursor route above.
   It may move backward or forward across Plan-stamped full-PV and sub-PV
   states without changing source, the accepted pointer, Plan, Goal, or bytes.
2. `HARD_ACCEPTED_ZIP_RESTORE` is a separate explicit full-PV restore. It
   requires a user-supplied, exact matching acceptance ZIP and a user-selected
   fresh restore root; it is unavailable for sub-PVs and never queries the
   rotating accepted folder as live authority. After restore, preserve Plan
   history through the stamped target row, make every later row non-executable,
   require a fresh user brief and EVI Plan, and bind a new Goal/Step projection
   only after explicit Plan acceptance.
3. `GIT_BRANCH_COMMIT_RESTORE` requires the user's exact repository, branch,
   commit, and a fresh user-selected workspace. Never hard-reset the dirty
   current workspace. Restore the exact commit into the fresh workspace,
   rebuild `local_code` from the governed `github_code`/Git authority, preserve
   Plan history through the commit-stamped row, make every later row
   non-executable, and require a fresh EVI Plan because Git rollback means the
   user rejected the later direction.

The Project/PV root and workspace remain separate in both hard modes. Neither
mode creates a candidate, infers HIL, or silently continues the old Plan. If
the installed `pv_rollback` schema/handler cannot execute the selected hard
mode exactly, fail closed and register the mismatch under the existing route
hardening owner; do not invent another rollback tool.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-rollback`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

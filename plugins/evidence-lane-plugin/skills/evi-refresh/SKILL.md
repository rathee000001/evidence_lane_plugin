---
name: evi-refresh
description: "Route the three current Evidence Lane refresh workflows without merging them: Source Intake steer/prompt refresh, adaptive Delta-exit append refresh, and full-PV-HIL Project Overlay refresh."
---

# Evidence Lane Refresh Router

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its hook/skill ownership
contract and Delta entry/exit laws.

This skill routes three separate current refresh operations. It never treats
them as aliases or lets one silently stand in for another:

1. For a steer or prompt that changes working evidence, use Source Intake with
   `REFRESH_WORKING_SECTORS`. Classify and append ChatLineage first, then refresh
   only the affected sector/source arms.
2. At every ordinary or HIL Delta exit, use `adaptive_delta_exit`. It refreshes
   changed sector lanes, AI Learning Delta state, Canon, Project Memory,
   Project Universe, connector brain, and required root pointers. It creates the
   Plan-only auto-accepted sub-PV record. A non-HIL Delta stops here and never
   runs Project Overlay.
3. Only on a full-PV HIL row, after the Delta-exit chain passes, call
   `task_complete_and_refresh` with the exact source-confirmation and active HIL
   row evidence. It appends the cumulative Project Overlay, seals or finalizes
   the same live-root proposal ID, and stops for the conjoined Project plus
   Learning HIL. It never constructs or copies a candidate directory.

If the same task already owns a pending proposal whose overlay predates bounded
exit-side writes, archive the prior seal append-only and finalize that exact
proposal ID. Never clear, rebuild, or rename it and never reuse an earlier
approval token.

Candidate-building refresh compatibility is not part of the installed surface.
Source Intake, `adaptive_delta_exit`, and HIL-only
`task_complete_and_refresh` are the only current refresh owners.

Project sub-PV acceptance is one Plan SQLite record only. It creates no folder,
ZIP, overlay, candidate, HIL, or pointer generation. After exact dual HIL
approval, accepted storage rotates to exactly one numbered full-root ZIP that
excludes the accepted directory itself.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and select exactly one ordered current `evi-refresh` route group for the
classified trigger. `MCP_ROUTING_FAIL_CLOSED`: never rewrite prefixes,
substitute a candidate directory, merge the three operations, or infer
approval.

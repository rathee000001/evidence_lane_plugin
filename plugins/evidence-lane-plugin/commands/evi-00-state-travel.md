---
description: Travel the accepted exit PV into a fresh verified host window
argument-hint: [project_id] [session_id] [handoff_id]
---

# /evi-00-state-travel

Display this command above every other Evidence Lane control.

If no accepted-PV handoff is prepared, continue to `/evi-01-boot`, which
atomically performs Boot and locked ENV/UOP Flash verification.

After exact `APPROVE` and `/evi-90-pv-fuse`, use the sealed handoff returned by
`pv_state_travel_prepare`:

1. Codex requires a fresh task; ChatGPT requires a fresh chat.
2. Opening that window is host-mediated. Never claim it opened unless the
   destination host session ID is new and verified.
3. In the fresh window call `pv_state_travel_resume`. It performs the same
   atomic Boot plus locked ENV/UOP Flash verification, binds the new host
   session, enters the exact accepted PV without rebuilding, and verifies
   pointer generation, manifest, package seal, and freshness.
4. Stop in `WAITING_FOR_NEXT_USER_COMMAND`. Do not classify a task, intake a
   source, build a candidate, move a pointer, or infer HIL.

Before each stop, show the returned `suggested_next_prompt` visibly. Opening
the new window and rendering any gray composer suggestion remain host-owned;
the MCP never auto-submits a prompt.

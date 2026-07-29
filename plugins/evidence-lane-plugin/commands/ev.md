---
description: Start or resume Evidence Lane, then reveal governed intake
argument-hint: [project_id]
---

# /EV — Evidence Lane orchestrator

`/EV` is the first user-facing command.

1. The host must already have installed this plugin; a command cannot install
   the package that defines itself. Call `runtime_doctor` and require installed
   manifest/runtime parity.
2. Call `session_flash_status`. Require the locked ENV15/UOP15 authority and
   report every warning exactly. Flash persists until plugin removal.
3. Verify the required Google Drive connector with one read-only connector
   search. Its normal host OAuth connection is distinct from the Python MCP's
   optional direct server-side Drive backend; never claim the connector token is
   exposed to the MCP.
4. If `$ARGUMENTS` names a registered project, call `pv_status`. Use
   `session_resume` with the `HOST_SESSION_ID` supplied by SessionStart when an
   active session exists; otherwise call `session_boot`.
   Pass the server-filesystem and client-source-edit capability axes explicitly
   for ChatGPT or any remote runner; never infer them from the UI brand.
5. If no project is named, show registered project IDs and reveal exactly two
   source-intake choices: `/git` and `/local`. Do not invent a registration.
6. After boot or resume, show the active source lane plus the eighteen-lane
   catalog, persistent entry PV, pointer generation, pending candidate/HIL,
   backlog, freshness, and next exact action.
7. If a booted project has no accepted PV and no preserved pending candidate,
   call `pv_build_initial` once and stop at HIL.

Static host command menus cannot be mutated dynamically. Enforce the intake
lock in the workflow: `/git`, `/local`, and lane reads require this `/EV`
verification in the current host task.

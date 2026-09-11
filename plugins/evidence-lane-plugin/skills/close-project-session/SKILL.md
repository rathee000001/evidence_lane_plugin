---
name: close-project-session
description: "Close the selected Evidence Lane project session at a safe work boundary and detach its Flash and capture. Use when explicitly ending that session."
---

# Close a project session

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read `session_status` and require the exact active session
ID, generation and event digest. Finish or safely checkpoint pending work and
uncertain effects, then use `session_exit` with the user's closure reason.
Read back closed state and detached capture. If also requested, use
`project_deselect` with its exact permissions to revoke this client selection.
Use `session_exit_boundary` when distinguishing closure from ordinary append,
native State Travel or Goal completion. It verifies exact supplied local
references and reports absent native evidence; it never emits a terminal receipt.
Closing a session does not remove project state, uninstall tools, stop the
shared Engine or change unrelated clients or native Goal status.

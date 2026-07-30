---
description: Explicitly close the persistent governed session without uninstalling or unflashing the plugin
argument-hint: <project_id> [session_id] [visible-reason]
---

# /evi-exit-boot

This explicit control is not an automatic Entry Slip or Exit Slip. Resolve the
one active governed session, then call `session_close` with the visible reason
`USER_REQUESTED_EVI_EXIT_BOOT` unless the user supplied a more specific
non-secret reason.

Report the closed session ID and its last lifecycle state. Confirm that the
active-session gate was released while the installed plugin, installation-
scoped ENV/UOP Flash receipt, accepted PVs, pending candidates, ChatLineage,
backlog, and accepted pointer generation were preserved.

Never delete evidence, uninstall the plugin, remove the Flash, promote a
candidate, move a pointer, or infer HIL approval.

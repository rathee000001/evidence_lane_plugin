---
description: Close only the active governed session
argument-hint: [project_id session_id reason]
---

# /evi-exit-boot

Call `session_close` with a visible reason. Close only the active governed
session; preserve installation, locked Flash, lineage, backlog, candidates,
accepted PVs, and pointer history.

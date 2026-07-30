---
description: Travel the accepted pointer to immutable PV or prompt-entry history
argument-hint: <project_id> [PVn | PROMPT <index> | TURN <id>]
---

# /evi-99-pv-rollback

Call `pv_rollback` once. A bare target resolves the current prompt entry and
falls back to the session entry. Show resolution, pointer/generation, seals,
accepted history, and preserved candidates. Never restore live source, delete
history, rebuild, or accept a candidate.

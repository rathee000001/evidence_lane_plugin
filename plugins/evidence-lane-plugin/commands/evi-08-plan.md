---
description: Read the Plan source lane
argument-hint: <project_id> [query-or-path]
---

# /evi-08-plan

Resolve canonical lane `plan` through `lane_catalog`, call `lane_status`, then
use bounded `lane_search` or `lane_fetch`. Return phases, milestones,
dependencies, acceptance criteria, seals, and freshness. This lane read does
not queue tasks; use `/evi-50-task-plan` for that.

When an accepted PV exists, also return the derived Plan runtime projection
reported by `lane_status`. Before an accepted PV exists, read that projection
through `pv_task_backlog`. Planning-mode detection may append a
privacy-minimized control-plane receipt and rebuild this projection
automatically. It never edits the canonical Plan source sector: exact Plan
files still require this named source-intake command and its one-turn route
grant.

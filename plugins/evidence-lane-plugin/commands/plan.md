---
description: Read the plan evidence lane
argument-hint: <project_id> [query-or-path]
---

# Evidence Lane plan

Resolve `plan` through `lane_catalog`, call `lane_status`, then use
`lane_search` or `lane_fetch`. Return phases, milestones, dependencies,
acceptance criteria, seals, and freshness. This read command does not queue
tasks; use `/pv-plan` for that.

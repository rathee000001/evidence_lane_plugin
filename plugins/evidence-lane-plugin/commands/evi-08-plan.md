---
description: Read the Plan source lane
argument-hint: <project_id> [query-or-path]
---

# /evi-08-plan

Resolve canonical lane `plan` through `lane_catalog`, call `lane_status`, then
use bounded `lane_search` or `lane_fetch`. Return phases, milestones,
dependencies, acceptance criteria, seals, and freshness. This lane read does
not queue tasks; use `/evi-50-task-plan` for that.

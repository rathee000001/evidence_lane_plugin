---
description: Read or explicitly transition the ordered Evidence Lane Delta backlog
argument-hint: <project_id> [DROP <task_id> <reason> | SUPERSEDE <task_id> <replacement_task_id> <reason>]
---

# /evi-51-backlog

Call `pv_task_backlog`. Show exact sequence, task IDs, contracts, statuses, and
the sole active task. Show the append-only event head, per-task lifecycle
events, universal status counts, Planning-mode receipt count, and derived Plan
runtime SQLite validation. Never reorder, merge, silently repair, or execute.

For a user-explicit `DROP` or `SUPERSEDE`, call `pv_task_transition`.
`SUPERSEDE` requires one different queued replacement task. Store only the
reason SHA-256 in the lifecycle ledger, preserve every prior event, and never
infer either transition from discussion or implementation results. `DONE`,
`ACCEPTED`, `REJECTED`, `FAILED`, and `ROLLED_BACK` remain governed automatic
Refresh or HIL outcomes rather than manual backlog shortcuts.

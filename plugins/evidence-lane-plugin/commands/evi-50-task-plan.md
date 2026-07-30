---
description: Queue bounded Deltas under one linear execution law
argument-hint: <project_id> <task 1>; <task 2>; ...
---

# /evi-50-task-plan

Call `pv_plan_tasks` with stable IDs and complete contracts. Planning must use
the same path, tool, and class validator as classification. One task becomes
one Delta and one HIL; later tasks remain queued.

Each new Delta enters the append-only universal lifecycle as `QUEUED`. The
complete status vocabulary is `QUEUED`, `ACTIVE`, `DONE`, `ACCEPTED`,
`REJECTED`, `DROPPED`, `SUPERSEDED`, `FAILED`, and `ROLLED_BACK`. To replace
an existing queued or DONE Delta, add the new task with
`supersedes_task_id`; the old task is marked `SUPERSEDED` without deletion or
reordering.

Planning-mode detection alone does not invent or queue a Delta. It appends
only a derived runtime receipt. A task enters this backlog only through an
explicit bounded contract.

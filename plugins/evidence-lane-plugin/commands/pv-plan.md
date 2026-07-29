---
description: Queue multiple exact tasks under one linear execution law
argument-hint: <project_id> <bounded task plan>
---

# Evidence Lane linear task plan

Translate `$ARGUMENTS` into exact task contracts and call `pv_plan_tasks` once.
Each task needs a stable ID, class, outcome, permitted paths/tools, acceptance
checks, and stop condition. Report the ordered queue. Do not classify or run
more than one task; planning never authorizes execution.

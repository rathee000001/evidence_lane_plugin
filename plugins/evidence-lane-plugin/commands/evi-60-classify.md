---
description: Activate one exact bounded Evidence Lane task
argument-hint: <project_id> <bounded outcome and scope>
---

# /evi-60-classify

Call `task_classify` once with exact outcome, class, paths, tools, executable
checks, stop condition, and HIL gate. A HIL follow-up must match its stored
class and outcome exactly.

After bounded work finishes, automatically call `task_complete_and_refresh`
with the exact host confirmation. Do not ask for a separate Exit or Refresh
command.

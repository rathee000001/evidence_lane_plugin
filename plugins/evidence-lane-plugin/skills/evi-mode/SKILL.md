---
name: evi-mode
description: Evidence Lane Mode sidecar for ordered known intersections and explicit custom-mode schemas.
---

# Evidence Lane Mode

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

Call `mode_classify` without changing the lifecycle position. Preserve the
user's mode order, always include Mode and Chat Lineage, and map known modes to
the locked ENV15 namespace. An unknown mode is never guessed: require a short
explicit name, concrete brief, and ordered canonical lanes, then persist only
that visible custom schema. Never store hidden reasoning.

Render every returned `mode_governance.visible_formula_response` line in the
visible response. Treat it as the selected lane's executable ENV/UOP contract:
show its formula, recursive loop, CI/CD requirement, PCM/MBA operator families,
and receipt hash. Persist the validated selection as the active mode binding;
when a task is classified, seal an immutable task-mode snapshot into both Entry
and Exit Slips and the candidate's next-action contract. Code mode always shows
`PCM + MBA`, uses the controlled CI/CD receipt returned by ENV, and reports an
open/failed approve gate when its executable evidence is incomplete. Never
infer autonomous build, Fuse, deployment, or HIL approval from mode selection.
At a HIL stop, preserve the universal six exact decision tokens but render each
selected lane's returned accepted object, gate, rollback target, and lane
effect. Do not reuse Code-mode HIL meanings for a non-Code lane.

For Planning mode in Codex, use `/evi-plan` as the Plan Lane sidecar. If the
host is not currently in native Plan mode, do not persist a plan: remind the
user to type `/pl`, finish planning, and run `/evi-plan` again. Once the plan is
final, call `pv_plan_tasks` with `host_kind=CODEX_DESKTOP` (or the exact Codex
host) and `host_mode=PLAN`. The returned Plan Lane is the canonical goal/task
list. Display its short `goal_start_prompt` for the user to copy and paste into
the host-owned Codex Goal; MCP does not mutate the Goal or mode selector.

Keep that Goal bound to the same canonical Plan Lane, active source boundary,
and single-writer session. A UI crash, token wait, required user input, or HIL
wait pauses only the dependent work; it never completes the Goal. Usage
reporting is separate accounting with no task-status effect. On every Goal or
panel reconstruction, include all completed-but-still-governing rows, exactly
one active row, and all pending rows without shortening or reordering them.

Keep the full task panel visible until the next six-way HIL. For every visible
steer, decide canonically whether it belongs to an existing task. Call
`pv_plan_steer_delta` with that `linked_task_id` when linked; append its exact
text without replacing the row or changing the count. If it is unrelated, pass
one complete `new_task_contract`; the Plan Lane adds a numbered step and the
count increases. When a next HIL row exists, Plan Lane inserts that new step
before the gate; a `PHYSICALLY_FINAL_HIL` row must remain physically final. The
default boundary is `BEFORE_NEXT_HIL` unless the user says otherwise.

Do not apply this Codex Plan-mode bridge to an unsupported non-Codex host.

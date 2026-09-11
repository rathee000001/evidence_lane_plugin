---
name: classify-project-work
description: "Classify the requested work, relevant source lanes, action classes and required gates. Use for known work patterns or an explicit custom classification; classification does not authorize execution."
---

# Classify project work

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Use `mode_classify` for the requested operating modes, preserving
the user's explicit order. Known modes use their locked ENV/UOP policies;
an inferred selection is labeled as inference and never grants permission.
Keep ChatLineage in the overall route. Each mode selects its actual owning
lanes; there is no Mode, Discussion or Analysis sector.

An unknown mode needs the user's explicit name, concrete brief, ordered
retained lanes and dependency policy. Do not invent a mode or silently switch
lanes when a dependency is missing. Use the exact custom schema accepted by
the action, with no hidden reasoning payload.

Use the returned scan order, unit of work, workflow, recursive loop,
validation gate, project-class defaults, action classes and required gates when
selecting the next task. Report the relevant work policy and receipt references
accurately; do not force one classification's actions or checks onto another.
Conditional toolchain bindings identify candidate actions and
tools. Select the exact owning action through `toolchain_resolve` when needed;
readiness, grants, device limits and execution budgets remain separate checks.
Classification does not run every listed tool or impose plugin maintainer CI.

To record an interpretation of a captured input, use `task_classify` with
its exact source event/cursor, intent, focus, owning lanes, workflow and next
action. Supply explicit modes there when recording that selection. A mode
query alone is read-only. For a work or semantic classification, carry the
returned `task_mode_binding` into each applicable Plan task's `mode_binding`.
This pins the exact project source, classification and locked policy through
Delta admission, tool execution and verified exit. Never substitute the latest
session mode or invent a binding from a read-only classification. Selecting a
new mode for pending work requires a semantic Plan refresh. Unbound historical
tasks retain their existing explicit action contracts without inferred modes.
For a semantic Plan change use the Plan workflow;
for an informational request keep the next action read-only. Recipe and mode
remain distinct and may be synchronized by the Project Recipe workflow.

For clean-exit intent, use `session_exit_boundary` to inspect the applicable
gate and exact references. An ordinary turn, verified Delta append, session
closure or engine-client continuation does not prove native Goal completion
or native State Travel. Missing independent host evidence remains unavailable.
Mode selection cannot execute Formula, promote a candidate, infer HIL,
complete a Goal, transfer a native task or authorize deployment.

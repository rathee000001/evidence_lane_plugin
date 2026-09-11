---
name: execute-project-plan
description: "Execute the next eligible project-plan task through bounded work, selected lane operations and verified completion. Use when carrying out project implementation."
---

# Execute project plan

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read the current Plan and `delta_status`. Select the next eligible
task with its exact revision, contract, dependencies, scope and acceptance
checks. When the task has an `operation` binding, use `delta_enter_planned` with
that exact task ID, revision and contract digest; the engine reads its stored
arguments and source route. Otherwise use `delta_enter` with the permitted
arguments. Both paths require the task's actual tools and permissions to be
ready. Changing a bound action, argument or source route requires a Plan steer.
An idle selected task receives an engine checkpoint before the new Plan revision;
an admitted job must reach its own safe checkpoint or recovery boundary.
Execute the lane operations admitted by that task through the Engine's
registered work path and owned OS workers. Keep the job ID and its receipts.
Treat queued work as queued. Advance only after verified exit; uncertain,
failed or incomplete effects require their actual checkpoint/recovery route.
Read `delta_status` for the recorded exit receipt, exact check identifiers,
Learning reference and verified automatic Memory refresh head. A historical
receipt without that refresh policy is reported explicitly; reading it does
not backfill Memory. Request `include_verification` for bounded computed check
observations and `include_result` for the addressed output. This read reconciles
recorded completion; it does not recheck today's source bytes. A group finishes
only after every selected task has its own verified exit in Plan order. Do not
submit caller PASS records or a host confirmation string to complete a batch.
Use the Plan workflow for a semantic steer and the owning read action for an
informational question. A recipe, tool listing or test count is not completion.

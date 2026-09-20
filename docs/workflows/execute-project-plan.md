# Carry out the next task

<p align="center">
  <a href="evidence-lane.md">Explore a project</a> · <a href="open-project-session.md">Open or resume a project</a> · <a href="configure-project-workflow.md">Shape the project workflow</a> · <a href="classify-project-work.md">Clarify the kind of work</a> · <a href="select-project-storage.md">Choose project storage</a> · <a href="manage-project-sources.md">Bring in project sources</a> · <a href="retrieve-project-evidence.md">Fit evidence into context</a> · <a href="refresh-project-evidence.md">Refresh changed evidence</a> · <a href="manage-project-plan.md">Create or update the Plan</a> · <a href="execute-project-plan.md">Carry out the next task</a> · <a href="exchange-task-evidence.md">Exchange evidence between tasks</a> · <a href="inspect-project-instructions.md">Understand applicable instructions</a> · <a href="manage-project-memory.md">Recall project context</a> · <a href="manage-project-lessons.md">Inspect project lessons</a> · <a href="inspect-project-connectors.md">Inspect connections</a> · <a href="configure-project-connector.md">Configure a connection</a> · <a href="revoke-project-connector.md">Withdraw a connection</a> · <a href="select-project-tools.md">Inspect and select tools</a> · <a href="inspect-project-evidence-map.md">Explore linked evidence</a> · <a href="link-project-evidence-network.md">Link separate projects</a> · <a href="handoff-project-work.md">Transfer work to another client</a> · <a href="recover-project-state.md">Back up or recover a project</a> · <a href="close-project-session.md">Close a project session</a> · <a href="run-project-lifecycle.md">Coordinate end-to-end work</a>
</p>


[← Workflow guide](../WORKFLOW_GUIDE.md) · [Documentation home](../README.md)

Execute the next eligible project-plan task through bounded work, selected lane operations and verified completion. Use when carrying out project implementation.

## What this helps you do

A bounded job, addressed output and recorded verification result.

## Use it when

Use **Carry out the next task** when your intention matches this example:

> Continue the next eligible task and show its output and checks.

Select the exact project first. If the workflow refers to sources, a Plan task, a connection, another client, or a recovery target, select that item explicitly rather than inferring it from a title or recently opened folder.

## What happens

1. **Read the current Plan.** Read a bounded page of the authoritative project Plan. This stage is read-only.
2. **Start the eligible task.** Run the operation already bound to one exact Plan task through its owning bounded task executor and verifier. This stage changes recorded project state.
3. **Inspect output and checks.** Read a recorded run and reconcile its completion evidence; optionally return result and verification bytes. This stage is read-only.

These stages are representative. The selected project and current state determine which choices, checks, or failure paths are needed.

## What you receive

A bounded job, addressed output and recorded verification result.

The result remains connected to the selected project and owning workflow. Read-only results do not silently refresh sources or change project state. State-changing results require their declared verification before the project moves forward.

## What Studio can show

Plan, Jobs, Workers, Evidence, Toolchains, Compute, and Diagnostics show admitted work and its result.

Studio is an observer. Open the corresponding Codex workflow when a change is required.

## Limits and failure behavior

A queued or failed job is not complete; changed task contracts require a Plan update.

Missing project identity, stale state, unavailable tools, insufficient access, invalid input, timeouts, or uncertain effects are reported as boundaries. Do not reinterpret a partial or queued result as completion.

## If something blocks the workflow

1. Preserve the exact project, task, source, connection, or operation identity.
2. Read the reported status and required choice.
3. Resolve the missing source, permission, provider, or safe checkpoint through its owning workflow.
4. Retry only when the prior operation's effects are known.

## Related workflows

- [Create or update the Plan](manage-project-plan.md)
- [Exchange evidence between tasks](exchange-task-evidence.md)
- [Refresh changed evidence](refresh-project-evidence.md)

<details>
<summary>SDK and MCP reference</summary>

- Public skill: `execute-project-plan`
- Representative actions: `plan_read`, `delta_enter_planned`, `delta_status`
- Some representative stages can change project records: `delta_enter_planned`.
- Current schemas and the connected engine remain authoritative for invocation.

</details>

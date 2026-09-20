# Refresh changed evidence

<p align="center">
  <a href="evidence-lane.md">Explore a project</a> · <a href="open-project-session.md">Open or resume a project</a> · <a href="configure-project-workflow.md">Shape the project workflow</a> · <a href="classify-project-work.md">Clarify the kind of work</a> · <a href="select-project-storage.md">Choose project storage</a> · <a href="manage-project-sources.md">Bring in project sources</a> · <a href="retrieve-project-evidence.md">Fit evidence into context</a> · <a href="refresh-project-evidence.md">Refresh changed evidence</a> · <a href="manage-project-plan.md">Create or update the Plan</a> · <a href="execute-project-plan.md">Carry out the next task</a> · <a href="exchange-task-evidence.md">Exchange evidence between tasks</a> · <a href="inspect-project-instructions.md">Understand applicable instructions</a> · <a href="manage-project-memory.md">Recall project context</a> · <a href="manage-project-lessons.md">Inspect project lessons</a> · <a href="inspect-project-connectors.md">Inspect connections</a> · <a href="configure-project-connector.md">Configure a connection</a> · <a href="revoke-project-connector.md">Withdraw a connection</a> · <a href="select-project-tools.md">Inspect and select tools</a> · <a href="inspect-project-evidence-map.md">Explore linked evidence</a> · <a href="link-project-evidence-network.md">Link separate projects</a> · <a href="handoff-project-work.md">Transfer work to another client</a> · <a href="recover-project-state.md">Back up or recover a project</a> · <a href="close-project-session.md">Close a project session</a> · <a href="run-project-lifecycle.md">Coordinate end-to-end work</a>
</p>


[← Workflow guide](../WORKFLOW_GUIDE.md) · [Documentation home](../README.md)

Refresh selected sources, lane views and changed authority references after a steer or verified work. Use for explicit evidence refresh and stale view repair.

## What this helps you do

Updated selected source snapshots and requested derived views.

## Use it when

Use **Refresh changed evidence** when your intention matches this example:

> Refresh the selected spreadsheet after these source changes.

Select the exact project first. If the workflow refers to sources, a Plan task, a connection, another client, or a recovery target, select that item explicitly rather than inferring it from a title or recently opened folder.

## What happens

1. **Prepare changed-source tasks.** Prepare changed, new and deleted selected sources as exact owning parser and retirement Plan tasks; preserve unselected scopes and history. This stage changes recorded project state.
2. **Apply the Plan revision.** Atomically replace remaining Plan contracts after a captured steer and safe checkpoint. This stage changes recorded project state.
3. **Process the selected sources.** Run one exact prepared source group through its current Plan tasks and owning bounded task verifiers, then verify coverage. This stage changes recorded project state.
4. **Refresh the selected views.** Export only selected lane artifacts from an exact verified source preview. This stage changes recorded project state.

These stages are representative. The selected project and current state determine which choices, checks, or failure paths are needed.

## What you receive

Updated selected source snapshots and requested derived views.

The result remains connected to the selected project and owning workflow. Read-only results do not silently refresh sources or change project state. State-changing results require their declared verification before the project moves forward.

## What Studio can show

Evidence and Plan views show the selected source group, preparation state, coverage, and refresh result.

Studio is an observer. Open the corresponding Codex workflow when a change is required.

## Limits and failure behavior

Refresh does not automatically parse every source or render every possible view.

Missing project identity, stale state, unavailable tools, insufficient access, invalid input, timeouts, or uncertain effects are reported as boundaries. Do not reinterpret a partial or queued result as completion.

## If something blocks the workflow

1. Preserve the exact project, task, source, connection, or operation identity.
2. Read the reported status and required choice.
3. Resolve the missing source, permission, provider, or safe checkpoint through its owning workflow.
4. Retry only when the prior operation's effects are known.

## Related workflows

- [Fit evidence into context](retrieve-project-evidence.md)
- [Create or update the Plan](manage-project-plan.md)
- [Bring in project sources](manage-project-sources.md)

<details>
<summary>SDK and MCP reference</summary>

- Public skill: `refresh-project-evidence`
- Representative actions: `source_prepare_refresh`, `plan_refresh`, `source_materialize`, `lane_view_refresh`
- Some representative stages can change project records: `source_prepare_refresh`, `plan_refresh`, `source_materialize`, `lane_view_refresh`.
- Current schemas and the connected engine remain authoritative for invocation.

</details>

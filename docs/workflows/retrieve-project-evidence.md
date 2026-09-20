# Fit evidence into context

<p align="center">
  <a href="evidence-lane.md">Explore a project</a> · <a href="open-project-session.md">Open or resume a project</a> · <a href="configure-project-workflow.md">Shape the project workflow</a> · <a href="classify-project-work.md">Clarify the kind of work</a> · <a href="select-project-storage.md">Choose project storage</a> · <a href="manage-project-sources.md">Bring in project sources</a> · <a href="retrieve-project-evidence.md">Fit evidence into context</a> · <a href="refresh-project-evidence.md">Refresh changed evidence</a> · <a href="manage-project-plan.md">Create or update the Plan</a> · <a href="execute-project-plan.md">Carry out the next task</a> · <a href="exchange-task-evidence.md">Exchange evidence between tasks</a> · <a href="inspect-project-instructions.md">Understand applicable instructions</a> · <a href="manage-project-memory.md">Recall project context</a> · <a href="manage-project-lessons.md">Inspect project lessons</a> · <a href="inspect-project-connectors.md">Inspect connections</a> · <a href="configure-project-connector.md">Configure a connection</a> · <a href="revoke-project-connector.md">Withdraw a connection</a> · <a href="select-project-tools.md">Inspect and select tools</a> · <a href="inspect-project-evidence-map.md">Explore linked evidence</a> · <a href="link-project-evidence-network.md">Link separate projects</a> · <a href="handoff-project-work.md">Transfer work to another client</a> · <a href="recover-project-state.md">Back up or recover a project</a> · <a href="close-project-session.md">Close a project session</a> · <a href="run-project-lifecycle.md">Coordinate end-to-end work</a>
</p>


[← Workflow guide](../WORKFLOW_GUIDE.md) · [Documentation home](../README.md)

Select attributed project evidence within explicit item and estimated-token budgets. Use to fit the relevant evidence into context without merging its owning stores.

## What this helps you do

A deterministic selection of attributed references within explicit budgets.

## Use it when

Use **Fit evidence into context** when your intention matches this example:

> Select the most relevant evidence within a 2000-token estimate.

Select the exact project first. If the workflow refers to sources, a Plan task, a connection, another client, or a recovery target, select that item explicitly rather than inferring it from a title or recently opened folder.

## What happens

1. **Select evidence within the budget.** Select deterministic metadata slices using explicit estimated-token and item budgets; verify content through its owning authority. This stage is read-only.
2. **Read the selected evidence.** Fetch bounded immutable source bytes through the owning lane using an exact snapshot and path. This stage is read-only.

These stages are representative. The selected project and current state determine which choices, checks, or failure paths are needed.

## What you receive

A deterministic selection of attributed references within explicit budgets.

The result remains connected to the selected project and owning workflow. Read-only results do not silently refresh sources or change project state. State-changing results require their declared verification before the project moves forward.

## What Studio can show

Evidence, Plan, Learning, and project context views show attributed references without changing them.

Studio is an observer. Open the corresponding Codex workflow when a change is required.

## Limits and failure behavior

Token counts are estimates. Selection does not fetch or reverify raw source content.

Missing project identity, stale state, unavailable tools, insufficient access, invalid input, timeouts, or uncertain effects are reported as boundaries. Do not reinterpret a partial or queued result as completion.

## If something blocks the workflow

1. Preserve the exact project, task, source, connection, or operation identity.
2. Read the reported status and required choice.
3. Resolve the missing source, permission, provider, or safe checkpoint through its owning workflow.
4. Retry only when the prior operation's effects are known.

## Related workflows

- [Bring in project sources](manage-project-sources.md)
- [Refresh changed evidence](refresh-project-evidence.md)
- [Choose project storage](select-project-storage.md)

<details>
<summary>SDK and MCP reference</summary>

- Public skill: `retrieve-project-evidence`
- Representative actions: `project_evidence_select`, `lane_fetch`
- The representative stages are read-only.
- Current schemas and the connected engine remain authoritative for invocation.

</details>

# Inspect connections

<p align="center">
  <a href="evidence-lane.md">Explore a project</a> · <a href="open-project-session.md">Open or resume a project</a> · <a href="configure-project-workflow.md">Shape the project workflow</a> · <a href="classify-project-work.md">Clarify the kind of work</a> · <a href="select-project-storage.md">Choose project storage</a> · <a href="manage-project-sources.md">Bring in project sources</a> · <a href="retrieve-project-evidence.md">Fit evidence into context</a> · <a href="refresh-project-evidence.md">Refresh changed evidence</a> · <a href="manage-project-plan.md">Create or update the Plan</a> · <a href="execute-project-plan.md">Carry out the next task</a> · <a href="exchange-task-evidence.md">Exchange evidence between tasks</a> · <a href="inspect-project-instructions.md">Understand applicable instructions</a> · <a href="manage-project-memory.md">Recall project context</a> · <a href="manage-project-lessons.md">Inspect project lessons</a> · <a href="inspect-project-connectors.md">Inspect connections</a> · <a href="configure-project-connector.md">Configure a connection</a> · <a href="revoke-project-connector.md">Withdraw a connection</a> · <a href="select-project-tools.md">Inspect and select tools</a> · <a href="inspect-project-evidence-map.md">Explore linked evidence</a> · <a href="link-project-evidence-network.md">Link separate projects</a> · <a href="handoff-project-work.md">Transfer work to another client</a> · <a href="recover-project-state.md">Back up or recover a project</a> · <a href="close-project-session.md">Close a project session</a> · <a href="run-project-lifecycle.md">Coordinate end-to-end work</a>
</p>


[← Workflow guide](../WORKFLOW_GUIDE.md) · [Documentation home](../README.md)

Inspect connector grants and their runtime readiness within an Evidence Lane project. Use for connector orientation; configuration and revocation have separate workflows.

## What this helps you do

Current connector registrations, scope, versions and configured backend references.

## Use it when

Use **Inspect connections** when your intention matches this example:

> Show which connectors this project can use and when their grants expire.

Select the exact project first. If the workflow refers to sources, a Plan task, a connection, another client, or a recovery target, select that item explicitly rather than inferring it from a title or recently opened folder.

## What happens

1. **Read the connection scope.** Inspect a bounded project registration page and current scope choices without activating a backend. This stage is read-only.
2. **Check eligible tools.** Inspect ordered compatible routes for an authorized operation without invoking or installing tools. This stage is read-only.

These stages are representative. The selected project and current state determine which choices, checks, or failure paths are needed.

## What you receive

Current connector registrations, scope, versions and configured backend references.

The result remains connected to the selected project and owning workflow. Read-only results do not silently refresh sources or change project state. State-changing results require their declared verification before the project moves forward.

## What Studio can show

Connections and Evidence views show the selected grant, exchange, or link and its current state.

Studio is an observer. Open the corresponding Codex workflow when a change is required.

## Limits and failure behavior

Registration is separate from measured execution readiness.

Missing project identity, stale state, unavailable tools, insufficient access, invalid input, timeouts, or uncertain effects are reported as boundaries. Do not reinterpret a partial or queued result as completion.

## If something blocks the workflow

1. Preserve the exact project, task, source, connection, or operation identity.
2. Read the reported status and required choice.
3. Resolve the missing source, permission, provider, or safe checkpoint through its owning workflow.
4. Retry only when the prior operation's effects are known.

## Related workflows

- [Inspect project lessons](manage-project-lessons.md)
- [Configure a connection](configure-project-connector.md)
- [Recall project context](manage-project-memory.md)

<details>
<summary>SDK and MCP reference</summary>

- Public skill: `inspect-project-connectors`
- Representative actions: `connector_read`, `toolchain_resolve`
- The representative stages are read-only.
- Current schemas and the connected engine remain authoritative for invocation.

</details>

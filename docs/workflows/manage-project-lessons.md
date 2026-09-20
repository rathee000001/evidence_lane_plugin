# Inspect project lessons

<p align="center">
  <a href="evidence-lane.md">Explore a project</a> · <a href="open-project-session.md">Open or resume a project</a> · <a href="configure-project-workflow.md">Shape the project workflow</a> · <a href="classify-project-work.md">Clarify the kind of work</a> · <a href="select-project-storage.md">Choose project storage</a> · <a href="manage-project-sources.md">Bring in project sources</a> · <a href="retrieve-project-evidence.md">Fit evidence into context</a> · <a href="refresh-project-evidence.md">Refresh changed evidence</a> · <a href="manage-project-plan.md">Create or update the Plan</a> · <a href="execute-project-plan.md">Carry out the next task</a> · <a href="exchange-task-evidence.md">Exchange evidence between tasks</a> · <a href="inspect-project-instructions.md">Understand applicable instructions</a> · <a href="manage-project-memory.md">Recall project context</a> · <a href="manage-project-lessons.md">Inspect project lessons</a> · <a href="inspect-project-connectors.md">Inspect connections</a> · <a href="configure-project-connector.md">Configure a connection</a> · <a href="revoke-project-connector.md">Withdraw a connection</a> · <a href="select-project-tools.md">Inspect and select tools</a> · <a href="inspect-project-evidence-map.md">Explore linked evidence</a> · <a href="link-project-evidence-network.md">Link separate projects</a> · <a href="handoff-project-work.md">Transfer work to another client</a> · <a href="recover-project-state.md">Back up or recover a project</a> · <a href="close-project-session.md">Close a project session</a> · <a href="run-project-lifecycle.md">Coordinate end-to-end work</a>
</p>


[← Workflow guide](../WORKFLOW_GUIDE.md) · [Documentation home](../README.md)

Inspect and revoke project-isolated procedural lessons recorded after verified work, or record an explicit host-memory provenance reference without creating a lesson. Use for lesson retrieval, correction or provenance.

## What this helps you do

Project-local lessons with verification provenance and retained revocation history.

## Use it when

Use **Inspect project lessons** when your intention matches this example:

> Show lessons from verified work that are relevant to this task.

Select the exact project first. If the workflow refers to sources, a Plan task, a connection, another client, or a recovery target, select that item explicitly rather than inferring it from a title or recently opened folder.

## What happens

1. **Read lessons from verified work.** Retrieve a bounded project-local slice of verified execution observations. This stage is read-only.
2. **Withdraw the selected lesson.** Revoke the exact current Learning observation and suppress automatic reactivation. This stage changes recorded project state.
3. **Read lessons from verified work.** Retrieve a bounded project-local slice of verified execution observations. This stage is read-only.

These stages are representative. The selected project and current state determine which choices, checks, or failure paths are needed.

## What you receive

Project-local lessons with verification provenance and retained revocation history.

The result remains connected to the selected project and owning workflow. Read-only results do not silently refresh sources or change project state. State-changing results require their declared verification before the project moves forward.

## What Studio can show

Evidence, Plan, Learning, and project context views show attributed references without changing them.

Studio is an observer. Open the corresponding Codex workflow when a change is required.

## Limits and failure behavior

Host recall is separate. Referencing host memory does not create a verified lesson.

Missing project identity, stale state, unavailable tools, insufficient access, invalid input, timeouts, or uncertain effects are reported as boundaries. Do not reinterpret a partial or queued result as completion.

## If something blocks the workflow

1. Preserve the exact project, task, source, connection, or operation identity.
2. Read the reported status and required choice.
3. Resolve the missing source, permission, provider, or safe checkpoint through its owning workflow.
4. Retry only when the prior operation's effects are known.

## Related workflows

- [Recall project context](manage-project-memory.md)
- [Inspect connections](inspect-project-connectors.md)
- [Understand applicable instructions](inspect-project-instructions.md)

<details>
<summary>SDK and MCP reference</summary>

- Public skill: `manage-project-lessons`
- Representative actions: `learning_read`, `learning_revoke`, `learning_read`
- Some representative stages can change project records: `learning_revoke`.
- Current schemas and the connected engine remain authoritative for invocation.

</details>

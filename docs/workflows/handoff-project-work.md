# Transfer work to another client

<p align="center">
  <a href="evidence-lane.md">Explore a project</a> · <a href="open-project-session.md">Open or resume a project</a> · <a href="configure-project-workflow.md">Shape the project workflow</a> · <a href="classify-project-work.md">Clarify the kind of work</a> · <a href="select-project-storage.md">Choose project storage</a> · <a href="manage-project-sources.md">Bring in project sources</a> · <a href="retrieve-project-evidence.md">Fit evidence into context</a> · <a href="refresh-project-evidence.md">Refresh changed evidence</a> · <a href="manage-project-plan.md">Create or update the Plan</a> · <a href="execute-project-plan.md">Carry out the next task</a> · <a href="exchange-task-evidence.md">Exchange evidence between tasks</a> · <a href="inspect-project-instructions.md">Understand applicable instructions</a> · <a href="manage-project-memory.md">Recall project context</a> · <a href="manage-project-lessons.md">Inspect project lessons</a> · <a href="inspect-project-connectors.md">Inspect connections</a> · <a href="configure-project-connector.md">Configure a connection</a> · <a href="revoke-project-connector.md">Withdraw a connection</a> · <a href="select-project-tools.md">Inspect and select tools</a> · <a href="inspect-project-evidence-map.md">Explore linked evidence</a> · <a href="link-project-evidence-network.md">Link separate projects</a> · <a href="handoff-project-work.md">Transfer work to another client</a> · <a href="recover-project-state.md">Back up or recover a project</a> · <a href="close-project-session.md">Close a project session</a> · <a href="run-project-lifecycle.md">Coordinate end-to-end work</a>
</p>


[← Workflow guide](../WORKFLOW_GUIDE.md) · [Documentation home](../README.md)

Transfer current project work between explicitly selected clients with exact ownership checks and attributed continuation context. Use for a requested task handoff.

## What this helps you do

An exact offer, destination acceptance and attributed continuation context.

## Use it when

Use **Transfer work to another client** when your intention matches this example:

> Transfer this project at a safe boundary to the selected client.

Select the exact project first. If the workflow refers to sources, a Plan task, a connection, another client, or a recovery target, select that item explicitly rather than inferring it from a title or recently opened folder.

## What happens

1. **Read the continuation context.** Read attributed Plan, task exchange authority, Memory and lineage locators for an accepted project continuation. This stage is read-only.
2. **Offer work at a safe boundary.** Offer exact project-participant continuity to another registered client at a safe work boundary. This stage changes recorded project state.
3. **Accept work on the destination.** Accept a destination-pinned project continuation and put the source client in read-only closeout. This stage changes recorded project state.
4. **Resume the exact session.** Resume the exact project session without duplicating or silently taking over a live owner. This stage changes recorded project state.

These stages are representative. The selected project and current state determine which choices, checks, or failure paths are needed.

## What you receive

An exact offer, destination acceptance and attributed continuation context.

The result remains connected to the selected project and owning workflow. Read-only results do not silently refresh sources or change project state. State-changing results require their declared verification before the project moves forward.

## What Studio can show

Plan, Jobs, Evidence, Connections, and Diagnostics show the continuation or recovery boundary.

Studio is an observer. Open the corresponding Codex workflow when a change is required.

## Limits and failure behavior

A title or client label cannot prove native task identity. Missing required attestation stops transfer.

Missing project identity, stale state, unavailable tools, insufficient access, invalid input, timeouts, or uncertain effects are reported as boundaries. Do not reinterpret a partial or queued result as completion.

## If something blocks the workflow

1. Preserve the exact project, task, source, connection, or operation identity.
2. Read the reported status and required choice.
3. Resolve the missing source, permission, provider, or safe checkpoint through its owning workflow.
4. Retry only when the prior operation's effects are known.

## Related workflows

- [Link separate projects](link-project-evidence-network.md)
- [Back up or recover a project](recover-project-state.md)
- [Explore linked evidence](inspect-project-evidence-map.md)

<details>
<summary>SDK and MCP reference</summary>

- Public skill: `handoff-project-work`
- Representative actions: `continuation_context`, `continuation_offer`, `continuation_accept`, `session_resume`
- Some representative stages can change project records: `continuation_offer`, `continuation_accept`, `session_resume`.
- Current schemas and the connected engine remain authoritative for invocation.

</details>

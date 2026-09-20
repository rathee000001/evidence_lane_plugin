# Exchange evidence between tasks

<p align="center">
  <a href="evidence-lane.md">Explore a project</a> · <a href="open-project-session.md">Open or resume a project</a> · <a href="configure-project-workflow.md">Shape the project workflow</a> · <a href="classify-project-work.md">Clarify the kind of work</a> · <a href="select-project-storage.md">Choose project storage</a> · <a href="manage-project-sources.md">Bring in project sources</a> · <a href="retrieve-project-evidence.md">Fit evidence into context</a> · <a href="refresh-project-evidence.md">Refresh changed evidence</a> · <a href="manage-project-plan.md">Create or update the Plan</a> · <a href="execute-project-plan.md">Carry out the next task</a> · <a href="exchange-task-evidence.md">Exchange evidence between tasks</a> · <a href="inspect-project-instructions.md">Understand applicable instructions</a> · <a href="manage-project-memory.md">Recall project context</a> · <a href="manage-project-lessons.md">Inspect project lessons</a> · <a href="inspect-project-connectors.md">Inspect connections</a> · <a href="configure-project-connector.md">Configure a connection</a> · <a href="revoke-project-connector.md">Withdraw a connection</a> · <a href="select-project-tools.md">Inspect and select tools</a> · <a href="inspect-project-evidence-map.md">Explore linked evidence</a> · <a href="link-project-evidence-network.md">Link separate projects</a> · <a href="handoff-project-work.md">Transfer work to another client</a> · <a href="recover-project-state.md">Back up or recover a project</a> · <a href="close-project-session.md">Close a project session</a> · <a href="run-project-lifecycle.md">Coordinate end-to-end work</a>
</p>


[← Workflow guide](../WORKFLOW_GUIDE.md) · [Documentation home](../README.md)

Exchange typed requirements, evidence and results between explicitly selected project tasks while preserving sender and receiver ownership. Use for a governed task exchange.

## What this helps you do

An attributed packet with sender, receiver and input or return contract.

## Use it when

Use **Exchange evidence between tasks** when your intention matches this example:

> Send this result to the selected task under its stated input requirements.

Select the exact project first. If the workflow refers to sources, a Plan task, a connection, another client, or a recovery target, select that item explicitly rather than inferring it from a title or recently opened folder.

## What happens

1. **Set the receiver requirements.** Version the receiver-owned typed expected-input contract. This stage changes recorded project state.
2. **Prepare the outgoing packet.** Seal a foreign-project outbox packet, or receive a local participant exchange against its exact contract. This stage changes recorded project state.
3. **Receive the exact packet.** Receive an exact source-sealed packet as its destination participant; never mutate the source project. This stage changes recorded project state.
4. **Return the attributed result.** Seal or reuse the content-derived typed result for the exact bound edge and pinned return contract. This stage changes recorded project state.

These stages are representative. The selected project and current state determine which choices, checks, or failure paths are needed.

## What you receive

An attributed packet with sender, receiver and input or return contract.

The result remains connected to the selected project and owning workflow. Read-only results do not silently refresh sources or change project state. State-changing results require their declared verification before the project moves forward.

## What Studio can show

Connections and Evidence views show the selected grant, exchange, or link and its current state.

Studio is an observer. Open the corresponding Codex workflow when a change is required.

## Limits and failure behavior

A sealed outgoing packet is not proof of receipt. Receiver acceptance remains separately owned.

Missing project identity, stale state, unavailable tools, insufficient access, invalid input, timeouts, or uncertain effects are reported as boundaries. Do not reinterpret a partial or queued result as completion.

## If something blocks the workflow

1. Preserve the exact project, task, source, connection, or operation identity.
2. Read the reported status and required choice.
3. Resolve the missing source, permission, provider, or safe checkpoint through its owning workflow.
4. Retry only when the prior operation's effects are known.

## Related workflows

- [Carry out the next task](execute-project-plan.md)
- [Understand applicable instructions](inspect-project-instructions.md)
- [Create or update the Plan](manage-project-plan.md)

<details>
<summary>SDK and MCP reference</summary>

- Public skill: `exchange-task-evidence`
- Representative actions: `task_evidence_expect`, `task_evidence_send`, `task_evidence_receive`, `task_evidence_result`
- Some representative stages can change project records: `task_evidence_expect`, `task_evidence_send`, `task_evidence_receive`, `task_evidence_result`.
- Current schemas and the connected engine remain authoritative for invocation.

</details>

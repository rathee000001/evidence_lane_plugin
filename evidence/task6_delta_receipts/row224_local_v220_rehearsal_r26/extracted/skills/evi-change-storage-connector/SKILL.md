---
name: evi-change-storage-connector
description: Compatibility sidecar for project-scoped Evidence Lane storage inspection and selection.
---

# Change Storage Connector

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

Follow the complete `evi-storage` contract. Call `storage_connector_inspect`
first. Only after the exact `SELECT_STORAGE:<MODE>[:connector-id]` token may
`storage_connector_select` run once. Store no credentials, fail closed when the
selected capability is unavailable, and keep Google Drive an optional sealed-
artifact mirror rather than primary runtime authority. Use those canonical bare
tool names only; never copy a connector display namespace into the contract.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-change-storage-connector`.
`MCP_ROUTING_FAIL_CLOSED`: if the bundled `evidence-lane` dependency, an exact
tool, or a required result is missing or ambiguous, stop and report it; never
rewrite prefixes, substitute a tool, reorder a write, or infer success.

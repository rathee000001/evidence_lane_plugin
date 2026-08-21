---
name: evi-drop-additional-plugin
description: Revoke one active Evidence Lane plugin grant while preserving its append-only history.
---

# Drop Additional Plugin

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

Inspect the catalog, identify exactly one active grant, and call
`connector_plugin_drop` only with case-sensitive `DROP:<plugin-id>`. Preserve
the registration and event history. Do not uninstall Evidence Lane, detach
Boot, accept a candidate, or move a pointer.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-drop-additional-plugin`.
`MCP_ROUTING_FAIL_CLOSED`: if the bundled `evidence-lane` dependency, an exact
tool, or a required result is missing or ambiguous, stop and report it; never
rewrite prefixes, substitute a tool, reorder a write, or infer success.

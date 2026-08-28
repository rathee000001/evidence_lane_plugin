---
name: evi-storage
description: Inspect or select Evidence Lane primary storage routing with an append-only, secret-free receipt.
---

# Evidence Lane Storage Sidecar

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

This is an administrative sidecar, not a seventh primary `/evi` control.

1. Call `storage_connector_inspect` before any `storage_connector_select`.
2. Route by the MCP server's actual durable-filesystem capability, then by host
   profile. Stable Codex desktop/CLI/VM and any explicitly durable mount use
   local SQLite.
3. A truly ephemeral Codex VM without a durable mount requires a transactional
   runtime connector. Google Drive may carry only sealed Entry/Exit artifacts
   for this Codex profile; it is never the live sessions, backlog, lineage,
   candidate, receipt, or pointer-CAS authority.
4. `LOCAL_SQLITE` fails closed unless the MCP server has durable local storage.
5. `CONFIGURED_DURABLE_CONNECTOR` requires a connector ID and a runtime-capable
   configured service; environment variable names may be governed, but secret
   values are never persisted.
6. Require exactly `SELECT_STORAGE:<MODE>` or, for a durable connector,
   `SELECT_STORAGE:CONFIGURED_DURABLE_CONNECTOR:<connector-id>`.
7. Re-inspect after selection and render the complete host route. MCP reads use
   the selected primary runtime; MCP writes remain under ENV/UOP and one-writer
   law. Never describe Google Drive as primary runtime storage.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-storage`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

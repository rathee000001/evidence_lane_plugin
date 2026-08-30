---
name: evi-additional-plugin
description: Add one bounded host-specific connector or AI toolchain grant with purpose, role schema, actions, scope, runtime, and expiry.
---

# Add Additional Plugin

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/references/shared-boundaries.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

1. Inspect `connector_plugin_catalog`.
2. Require a lowercase plugin ID, connector/toolchain kind, one visible
   purpose/reason, role, typed role-field schema, the CODEX host profile,
   allowed actions, canonical lanes, write scope, expiry, optional declared
   backend runtime, and configuration environment-variable names only.
3. Call `connector_plugin_register`. Never store secret values.
4. Verify the append-only registration receipt, role-schema hash/table, target
   host settings slots, current registry-derived active capacity, and one ordered route-guard
   receipt. If multiple plugins qualify, require an exact preferred plugin ID;
   never choose by registration or lexical order.
5. Return to the prior lifecycle position; registration is not acceptance.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-additional-plugin`.
`MCP_ROUTING_FAIL_CLOSED`: if the bundled `evidence-lane` dependency, an exact
tool, or a required result is missing or ambiguous, stop and report it; never
rewrite prefixes, substitute a tool, reorder a write, or infer success.

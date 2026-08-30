---
name: evi-exit-boot
description: Explicitly close one persistent Evidence Lane session and detach live Flash/capture without removing the installation or immutable evidence.
---

# Evidence Lane Exit Boot

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/references/shared-boundaries.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

Call `session_close` with a visible reason. Close only the active governed
session and require the returned runtime-activation receipt to show that exact
session detached. When no other governed sessions remain, the installation
runtime must be `DETACHED`: ENV/UOP Flash context and visible prompt/response
capture are off. Preserve the plugin installation, locked Flash verification
receipt, immutable store, lineage, backlog, candidates, accepted PVs, and
pointer history. A later `/evi-boot` re-verifies and reattaches them.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-exit-boot`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

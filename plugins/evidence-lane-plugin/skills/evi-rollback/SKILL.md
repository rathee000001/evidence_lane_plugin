---
name: evi-rollback
description: Evidence Lane pointer-only rollback across immutable accepted project versions.
---

# Evidence Lane Rollback

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

Call `pv_rollback`. Resolve a bare rollback to the session entry PV, require
pointer-generation CAS, preserve every source byte, candidate, receipt, and
accepted package, and record freshness. A pending unaccepted candidate is
preserved. Rollback never accepts or rebuilds anything.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-rollback`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

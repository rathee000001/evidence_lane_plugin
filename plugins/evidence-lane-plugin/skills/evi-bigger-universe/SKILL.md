---
name: evi-bigger-universe
description: Register or explicitly link hash-only project mini-brains in the separate Evidence Lane Bigger Universe federation without merging per-project Universe or Project Truth.
---

# Evidence Lane Bigger Universe

Use this skill for the federation above distinct per-project Universe
authorities.

- Use `bigger_universe_register` to register current lane mini-brain hashes and
  atomically advance only changed project lane heads.
- Use `bigger_universe_link` only with an exact explicit grant hash and
  hash-only evidence references from two different registered projects.

Reuse unchanged content without a write. Preserve historical mini-brain refs
for existing edges. Never copy raw cross-project payloads, merge project truth,
replace a per-project Universe, infer HIL, or move a pointer.
## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-bigger-universe`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing, stop without aliasing, prefix rewriting, or a compatibility fallback.
Shared lifecycle, Goal, Plan, HIL, install, and State Travel boundaries remain
owned by `../evidence-lane-code-lifecycle/SKILL.md`; this workflow cannot override them.

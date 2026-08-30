---
name: evi-brain-scaling
description: Select a deterministic bounded indexed Evidence Lane brain slice when a large authority must fit an explicit token and item budget without training or merging authorities.
---

# Evidence Lane Brain Scaling

Use this skill only for bounded indexed slicing of an existing authority.

1. Supply content IDs, exact SHA-256 identities, token counts, priorities, a
   token budget, and a maximum slice count.
2. Call `brain_scaling_select` and retain the deterministic receipt.
3. Use only the selected hash-addressed slice for the bounded task.

This workflow never trains a model, copies raw cross-project payloads, promotes
Learning, merges authority roles, creates a candidate, or changes a PV pointer.
## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-brain-scaling`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing, stop without aliasing, prefix rewriting, or a compatibility fallback.
Shared lifecycle, Goal, Plan, HIL, install, and State Travel boundaries remain
owned by `../evidence-lane-code-lifecycle/references/shared-boundaries.md`; this workflow cannot override them.

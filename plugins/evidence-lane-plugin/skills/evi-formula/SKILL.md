---
name: evi-formula
description: Compile or route the separate bounded Evidence Lane ENV/UOP Formula Engine when work needs explicit operator, formula, lane, tool, or budget governance.
---

# Evidence Lane Formula Engine

Use this skill for the first-class Formula Engine. It is not Mode, AI Learning,
Project Truth, a generic math label, or a HIL decision.

1. Resolve the exact current ENV/UOP authority and SDK binding.
2. Require a bounded execution budget for every selected lane and tool.
3. Compile with `formula_engine_run`; route an operator only when its declared
   effect, lane, tool, and budget match the compiled receipt exactly.
4. Return the compiled and routed receipt hashes. Fail visibly on authority,
   effect, binding, or budget drift.

Never create a candidate, infer HIL, move a pointer, store a secret value, or
let Formula override Project Truth. Mode may supply a selection, but it does not
own this workflow.
## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-formula`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing, stop without aliasing, prefix rewriting, or a compatibility fallback.
Shared lifecycle, Goal, Plan, HIL, install, and State Travel boundaries remain
owned by `../evidence-lane-code-lifecycle/SKILL.md`; this workflow cannot override them.

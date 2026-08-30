---
name: evi-project-recipe
description: Compile an Evidence Lane project-type recipe from exact source paths and the requested outcome during initial or explicitly reclassified work, without turning the recipe into Mode.
---

# Evidence Lane Project Recipe

Use this skill when initial Source Intake or an explicit reclassification needs
a project-type execution recipe. State Travel resumes the existing project and
does not create a new recipe merely because the host task changed.

1. Provide the project ID, exact source paths, requested outcome, and an
   explicit project type only when the human supplied one.
2. Call `project_recipe_compile`.
3. Preserve the returned lane set and Source Intake -> ENV/UOP -> lane work ->
   validation -> Delta append stages.

The recipe creates no stored lane, candidate, HIL, or pointer movement and does
not replace the separate Mode workflow.
## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-project-recipe`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing, stop without aliasing, prefix rewriting, or a compatibility fallback.
Shared lifecycle, Goal, Plan, HIL, install, and State Travel boundaries remain
owned by `../evidence-lane-code-lifecycle/references/shared-boundaries.md`; this workflow cannot override them.

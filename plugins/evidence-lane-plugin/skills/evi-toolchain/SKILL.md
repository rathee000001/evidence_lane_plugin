---
name: evi-toolchain
description: Resolve and run one conditional Evidence Lane AI toolchain for an exact lane and Codex host profile, with explicit primary/fallback order and fail-visible missing dependencies.
---

# Evidence Lane Full AI Toolchain

Use this skill when governed work must select the complete applicable toolchain
for one exact lane or action.

1. Resolve the current lane, action class, Codex host profile, and available
   dependency set.
2. Call `ai_toolchain_route`.
3. Run only the returned primary or ordered eligible fallback; never run every
   tool and never cross action classes silently.
4. Preserve the tool, license, runtime, and receipt identities.

This Codex plane excludes non-Codex acting-agent routes. Runtime dependency installation is allowed
only inside the explicit local-update route and never in a user workspace.
## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-toolchain`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing, stop without aliasing, prefix rewriting, or a compatibility fallback.
Shared lifecycle, Goal, Plan, HIL, install, and State Travel boundaries remain
owned by `../evidence-lane-code-lifecycle/references/shared-boundaries.md`; this workflow cannot override them.

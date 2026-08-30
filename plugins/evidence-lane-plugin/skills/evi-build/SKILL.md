---
name: evi-build
description: Evidence Lane PV0 bootstrap and unaccepted proposal construction through dual-HIL presentation.
---

# Evidence Lane Build and dual-HIL presentation

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/references/shared-boundaries.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

The first live-root Build is not a Project HIL. Before it runs, the initial
workflow has already registered the separate workspace and external Project/PV
root, persisted the canonical Plan through native Plan mode and EVI Plan, obtained explicit host
Plan acceptance, and bound the active Goal plus fixed Step Task List through
hooks. `pv_build_initial` then internally uses Source Intake to materialize
the complete current sector-lane registry, including Project Engulf, and binds that live
projection as the starting `PV0` authority at pointer generation `0`. It
creates no candidate, invokes no Project or Learning HIL, refreshes no Project
Overlay, and never reads or writes accepted storage. State Travel reuses the
carried project/Plan/Goal/PV identities and never invokes this initial path.
The historical initial-PV1
candidate workflow is an obsolete execution route and must never be selected
by the public skill, MCP action, SDK, hook, or UI route.

After PV0, continue the already-active Goal and Step Task List at the current
Plan row. PV0 never invents Plan rows, starts a Goal, or infers acceptance.

Later bounded rows enter through adaptive Delta entry and close through the
separate adaptive Delta exit. Ordinary verified rows auto-seal their Plan-only
sub-PV acceptance and auto-admit their Delta Learning member without any human
decision. A full-PV HIL row additionally refreshes Project Overlay exactly
once, seals the one live-root Project proposal, and weaves all auto-admitted
Delta Learning members into exactly one Learning candidate for the same target
PV.

Every full-PV HIL is dual. Build presents the Project and Learning decision
surfaces together and stops. It records no decision and owns no promotion.
The separate `evi-fuse` skill owns intent classification, both exact decision
routes, all non-promotion outcomes, Learning-then-Project approval ordering,
accepted ZIP rotation, and `pv_fuse`. Never alias Build to Fuse.

Project/runtime panels are not a generic HIL or candidate-build step. Under
`PROJECT_RUNTIME_RENDER_TWO_TRIGGER_LAW`, call `render_runtime_panel` and
`render_project_panel` once per tool only when presenting the physically final
PV HIL. Intermediate HILs, candidate creation, HIL classification, Fuse, and
return-to-accepted paths do not call them. The only other permitted trigger is
an explicit user render request. State Travel uses native receipts and never
calls a renderer. Never retry, substitute, or invoke a renderer for ordinary status
proof.

After presentation, route any user decision or natural-language HIL intent to
`evi-fuse`. `/evi-build APPROVE` is not a promotion route.

Every new candidate must validate the source-policy receipt and reconcile each
lane's SQLite authority independently to Mermaid and DOT, including structural
floors, exact MMD/DOT identity parity, and emitted count claims. Rendering alone
is not proof. Previously sealed pre-v1.1 lane bundles may remain readable only
through the explicitly reported compatibility path; do not describe their raw
topology as reconciled and do not use that path for a new candidate.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-build`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

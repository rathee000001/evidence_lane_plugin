---
name: evi-mode
description: Evidence Lane Mode sidecar for ordered known intersections and explicit custom-mode schemas.
---

# Evidence Lane Mode

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/references/shared-boundaries.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

Call `mode_classify` without changing the lifecycle position. Preserve the
user's mode order, always include Mode and Chat Lineage, and map known modes to
the locked ENV15 namespace. An unknown mode is never guessed: require a short
explicit name, concrete brief, and ordered canonical lanes, then persist only
that visible custom schema. Never store hidden reasoning.

Render every returned `mode_governance.visible_formula_response` line in the
visible response. Treat it as the selected lane's executable ENV/UOP contract:
show its formula, recursive loop, CI/CD requirement, PCM/MBA operator families,
and receipt hash. Persist the validated selection as the active mode binding;
when a task is classified, seal an immutable task-mode snapshot into both Entry
and Exit Slips and the candidate's next-action contract. Code mode always shows
`PCM + MBA`, uses the controlled CI/CD receipt returned by ENV, and reports an
open/failed approve gate when its executable evidence is incomplete. Never
infer autonomous build, Fuse, deployment, or HIL approval from mode selection.
At a HIL stop, resolve the current decision vocabulary from the owning
authority, then render each selected lane's returned accepted object, gate,
rollback target, and lane effect. Do not reuse Project or Code-mode meanings
for another authority or lane, and do not treat the current vocabulary size as
a workflow ceiling.

For Planning mode in Codex, route to the native `$evi-plan` skill after mode
classification. `evi-mode` does not own Plan persistence, Plan steering, host
projection, or a separate command surface.

Do not apply this Codex Plan-mode bridge to an unsupported non-Codex host.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-mode`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

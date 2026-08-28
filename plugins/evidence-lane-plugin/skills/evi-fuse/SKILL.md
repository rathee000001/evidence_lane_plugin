---
name: evi-fuse
description: Govern separate Project and Learning HIL decisions and exact approved promotion.
---

# Evidence Lane Fuse

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`. This skill owns dual-HIL decisions
and promotion only. Build owns PV0 and unaccepted proposal construction; it
must stop after presenting the Project and Learning decision surfaces.

Require one pending full-PV proposal and its unique consolidated Learning weave
for the same target PV. Present both six-choice surfaces together while keeping
their decisions separate: `APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`,
`ROLLBACK`, `REJECT`, and `FAIL`.

Classify non-exact natural language with `hil_intent_classify`; classification
never decides or promotes. Route the five non-promotion Project outcomes through
`hil_decide`, and use `hil_return_to_accepted` only for its exact bounded return
contract. Preserve the proposal, accepted history, Goal, Plan, source, and
pointer unless the selected outcome's native contract explicitly says
otherwise.

Promotion requires two independently visible, exact case-sensitive decisions;
this exact dual approval is the only promotion authority.
First use the Learning skill to record `APPROVE` for the unique consolidated
Learning weave. Then call `pv_fuse` with the separate exact Project `APPROVE`.
Fuse must verify the Learning head targets the same proposed PV. Never infer or
replay either decision.

Only `pv_fuse` may create the numbered accepted full-live-root ZIP, excluding
`accepted/` itself, rotate that snapshot, append the dual-HIL Plan stamps, and
move the Project pointer once. The ZIP is post-approval storage only and is
never queried by ordinary work or State Travel. Fuse never builds or rebuilds a
proposal, invokes Git/install/restart, or clears a candidate as preparation.

## MCP routing contract

Before the first MCP call, read
`../evi/references/mcp-tool-routing.v1.json` and use the ordered `evi-fuse`
route. `MCP_ROUTING_FAIL_CLOSED`: missing or ambiguous tools fail closed;
never alias Build to Fuse.

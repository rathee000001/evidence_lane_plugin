---
name: evi-canon
description: Govern bounded task-to-task Canon exchanges, linked top-level tasks or explicitly authorized subagents, receiver-owned Canon HIL decisions, backfire requests, result returns, and State Travel graph continuity. Use when one governed task must exchange typed evidence, requirements, corrections, plans, or results with another task without merging Project Truth, Agent Learning, task ownership, or HIL authority.
---

# Evidence Lane Canon

First apply the shared installed lifecycle contract in
`../evidence-lane-code-lifecycle/SKILL.md`; this skill narrows that contract to
the separate Canon authority and never widens lifecycle permission.

Canon is a project-isolated coordination authority. It carries immutable,
typed input and result envelopes between exact task UUID/deep-link nodes. It is
not Project Truth, Agent Learning, the internal SDK, ChatLineage, a Plan Delta,
State Travel, source-write authority, or a Project six-way HIL decision.

## Required intake

Run `pv_status`, `pv_task_backlog`, one bounded live-root `pv_query`, and the
six-authority `search` before classifying a new Canon exchange. Neither read
opens the accepted HIL ZIP. Match the receiving task by exact project,
task UUID, deep link, expected contract, schema, dependency, direction, and
accepted-pointer identity. Never bind by title or current directory alone.

Use `canon_inspect`, `canon_inbox`, and `canon_graph` for read-only inspection.
Use `canon_register_contract` before receiving input when the destination has a
known contract. `canon_seal_envelope` creates bounded input only;
`canon_receive` records receipt; `canon_classify` compares it to receiver
authority. None of these operations may promote Project Truth or Learning.

When a Delta explicitly requires the cross-sector consequence graph, use the
private SDK operation `canon_input:bootstrap_consequence_graph` with its exact
write grant and an empty payload. The SDK binding supplies project, accepted
pointer, active Plan task, host task/deep link, and ChatLineage identities; do
not repeat or override them in payload. The operation derives Plan, steer,
Learning, operational Canon, and all 18 sector inputs, then creates or reuses
one content-addressed SQLite/Mermaid/DOT bundle. Read the bounded summary back
through `canon_graph`. Never load or return the full graph or raw Plan/Learning
rows to model context, and never treat projection refresh as Canon admission,
Learning acceptance, ordinary approval, Project HIL, candidate creation, or
pointer movement.

## Linked execution

Canon supports exactly two execution classes:

- `TOP_LEVEL_TASK`: one independently governed task with its own task UUID,
  deep link, Plan/Goal, writer boundary, and any HIL it independently reaches.
- `SUBAGENT`: a bounded helper inside the current task, only when the current
  user policy authorizes subagents. It has no HIL, Fuse, pointer, Git, install,
  deploy, or State Travel authority.

Each class has either `READ_ONLY` or `GOVERNED_READ_WRITE` scope. Read-write
requires a separate host task contract, exact permitted paths, and a sealed
write-authorization identity; Canon itself grants no source write.

Use `canon_register_edge` for a proposed graph edge and `canon_bind_edge` for
an already created destination. `canon_dispatch_linked_task` supports one
idempotent two-phase native launch. When an injected `CodexHostDispatcher` is
available, it may execute the host operation directly. Otherwise the first call
returns `HOST_ACTION_REQUIRED` with the exact stable request; the caller invokes
only the native Codex task-create or authorized subagent route, then recalls the
same Canon action with the exact sealed host receipt. The host operation
receives one stable dispatch ID and must return a self-sealed
evidence-lane.codex-host-task-create-receipt.v1 binding the exact request,
destination UUID, deep link, and replay state. A plain callable, title match,
or unsealed `created_once` boolean is not sufficient. A persisted v2 Canon
dispatch receipt may be replayed without calling the host again. A missing
injected seam is not permission to use UI control: preserve the request and use
the caller-mediated native backend phase. If that native route is unavailable,
report `HOST_CAPABILITY_UNAVAILABLE`. Never fabricate creation, fall back to a
title, or perform host UI actions.

A Codex lifecycle hook firing is not itself a task-create receipt. A host may
implement the injected operation with Codex App Server, a Workspace Agent
trigger, or a hook-owned bridge, but it must normalize the supported host
result into the exact sealed UUID/deep-link receipt above. A thread ID without
the required deep link, or a conversation URL without the required task UUID,
remains insufficient and fails closed.

Directions are `UPSTREAM`, `DOWNSTREAM`, or `LATERAL`. Reject cycles, duplicate
destinations, ambiguous routing, cross-project leakage, unsealed contracts,
or any attempted approval/pointer propagation.

## Receiver-owned Canon HIL

When classification requires a decision, the receiving task owns a separate
three-way Canon HIL:

- `ACCEPT`
- `REJECT`
- `MORE_RESEARCH`

Record it only through `canon_decide`. Acceptance admits the bounded Canon
input; it does not prove implementation, accept Learning, authorize Project
HIL, Fuse a PV, move a pointer, or approve Git/install/deploy work. Use
`canon_supersede` to preserve an older immutable input while linking its
replacement.

## Backfire and return

Use `canon_backfire_hil` only when the linked work encounters one of these
classes: an execution failure requiring source-task action, missing source
information, a new source requirement, or input required from another linked
task. Backfire routes a bounded request to the named receiving task, which owns
its own three-way Canon HIL. It is not a generic error channel.

Use `canon_seal_envelope` for typed upstream, downstream, or lateral messages.
Use `canon_seal_result` to return bounded evidence and result payloads over the
existing edge. Preserve direction and provenance. A returned result is not
implementation proof until its owning task verifies it.

## State Travel continuity

Canon may accompany an independently authorized State Travel. Use
`canon_seal_continuity` to bind the current graph snapshot to the exact handoff,
then `canon_restore_continuity` only after the destination's exact State Travel
resume has passed. These actions never prepare or resume State Travel, create a
destination, replay HIL, or move a pointer.

## Schema authority

Treat `../../schemas/canon/canon-schema-manifest.v1.json` as the first-class
Canon schema authority. The ledger builder must execute the exact sealed
`canon-ledger.v1.sql` bytes and validate decision, dispatch, and State Travel
restore receipts against `canon-receipts.v1.schema.json` before persistence or
replay. The only implicit ledger migration is the additive, idempotent v0 to v1
transition recorded in the canon_schema_migration table; an unknown older version, a
newer version, an asset-hash mismatch, a destructive rewrite, or an unknown
receipt schema fails closed. Earlier receipt bytes are immutable, and a
breaking receipt change requires a new major schema ID.

Treat `../../schemas/canon/canon-consequence-graph.v1.sql` as the additive
first-class schema for the immutable consequence projection. It never replaces
or migrates the operational Canon ledger.

## Exit receipt

Return the Canon envelope/edge/result IDs and hashes, source and destination
task bindings, scope and direction, receiver decision state, any backfire state,
and explicit authority effects. State plainly that Project Truth, Agent
Learning, and pointer authority were unchanged.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-canon`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

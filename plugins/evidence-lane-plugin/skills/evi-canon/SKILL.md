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

Run `pv_status`, `pv_task_backlog`, and one bounded `pv_query` before
classifying a new Canon exchange. Match the receiving task by exact project,
task UUID, deep link, expected contract, schema, dependency, direction, and
accepted-pointer identity. Never bind by title or current directory alone.

Use `canon_inspect`, `canon_inbox`, and `canon_graph` for read-only inspection.
Use `canon_register_contract` before receiving input when the destination has a
known contract. `canon_seal_envelope` creates bounded input only;
`canon_receive` records receipt; `canon_classify` compares it to receiver
authority. None of these operations may promote Project Truth or Learning.

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
an already created destination. Use `canon_dispatch_linked_task` only when the
host exposes a supported programmatic task or subagent operation. The action
must create exactly one destination and return its exact UUID/deep link. If the
host seam is absent, preserve the request and report
`HOST_CAPABILITY_UNAVAILABLE`; never fabricate creation, fall back to a title,
or ask Canon to perform host UI actions it cannot perform.

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

Use `canon_seal_result` to return bounded evidence and result payloads over the
existing edge. Preserve upstream/downstream provenance and do not treat a
returned result as implementation proof until its owning task verifies it.

## State Travel continuity

Canon may accompany an independently authorized State Travel. Use
`canon_seal_continuity` to bind the current graph snapshot to the exact handoff,
then `canon_restore_continuity` only after the destination's exact State Travel
resume has passed. These actions never prepare or resume State Travel, create a
destination, replay HIL, or move a pointer.

## Exit receipt

Return the Canon envelope/edge/result IDs and hashes, source and destination
task bindings, scope and direction, receiver decision state, any backfire state,
and explicit authority effects. State plainly that Project Truth, Agent
Learning, and pointer authority were unchanged.

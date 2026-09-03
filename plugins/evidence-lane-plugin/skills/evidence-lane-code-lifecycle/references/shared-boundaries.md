# Shared Evidence Lane boundaries

Read this reference before a focused Evidence Lane skill calls a public action.
The selected skill narrows these rules; it never expands permission.

## Authority and ownership

- Codex is the sole acting agent. External models, agents, connectors, and SDKs
  may not own reasoning, Plan, Goal, HIL, Project/PV, memory, learning, or
  lifecycle state.
- The internal SDK owns typed action, authority, lane, formula, workflow, and
  receipt behavior. The outer SDK owns validated host-facing routing only.
- MCP exposes the current public action catalog. Skills select reusable
  workflows. Hooks carry bounded host events. Tunnel and toolchain components
  transport or execute a selected action. None may duplicate business logic.
- There is no separate command layer. A slash invocation is a skill-selection
  surface for the same canonical action route.

## Mutable registries

- Counts are current registry snapshots, never ceilings. Add or remove a skill,
  action, hook, lane, authority, tool, or fallback only by changing its canonical
  registry and regenerating every dependent surface to exact set parity.
- The current action catalog has 91 MCP actions: 30 reads and 61 writes. The
  current conditional tool/tunnel capability matrix has 120 entries. These are
  different inventories and must never be conflated.
- Project Engulf is one current sector-lane identity inside the registry-derived
  sector set, not an extra lane added after the set.

## ENV, UOP, and routing

- ENV selects the current Codex host profile, project recipe, context, locality,
  capability grants, and condition-true execution route.
- UOP independently governs operators, formulas, budgets, authorization, HIL,
  and promotion constraints. ENV never becomes governance and UOP never becomes
  environment discovery.
- FastMCP is preferred only when the selected action and host support it. Native
  or domain MCP, tunnel, and tool fallbacks remain ordered and condition-bound.
- A missing route, dependency, grant, server-derived identity, or durable store
  fails visibly. Never simulate an unavailable action or borrow another task's
  attachment.

## Project, task, and evidence

- Preserve exact project, workspace, task, session, Plan, Goal, PV, pointer,
  dirty-byte, host, and sole-writer identities. Runtime context and natural
  language are not acceptance evidence.
- Keep Project Truth, Project Memory, Agent Learning, Canon, Universe,
  ChatLineage, AGENTS.md instructions, host MEMORY.md recall, sectors, receipts,
  sessions, and connector state as separate authorities.
- Entry Slip classifies every prompt or steer and binds source intake before
  work. Adaptive Delta entry opens one Plan row. Mid-Delta queries are bounded.
  Adaptive Delta exit refreshes changed live authorities and closes real work.
  Exit Slip belongs only to Goal completion or a passed State Travel boundary.
- Delta-exit append, Entry Slip, Exit Slip, Project Overlay refresh, and full-PV
  HIL are distinct workflows. No one is an alias or acceptance substitute.

## HIL, refresh, and receipts

- Only the exact authority-owned HIL token can promote its candidate. Tests,
  commits, pushes, installs, restarts, discussion, or Plan acceptance never
  imply Project or Learning approval.
- Content-addressed source bytes, chunks, indexes, graphs, and unchanged members
  are reused by hash. A refresh emits a current commit-and-timestamp receipt even
  when bytes are unchanged.
- When a current route supersedes an executable, schema, generated, test, or
  source-derived documentation route, directly purge the old route and all of
  its references in the same Delta. Preserve only immutable non-executable
  evidence receipts; do not create executable tombstones or fallback history.

## Boot, State Travel, install, and Git

- Boot or resume uses the installed native server and server-derived runtime
  attestation. State Travel is user-timed or context-exhaustion-timed, exact
  once, and never a normal intake, install, or restart path.
- Plugin validation, cachebust, packaging, installation, user-manual restart,
  task reattachment, Git staging, commit, push, merge, and stable-slot upgrade
  are separate gates. Each requires its own exact receipt and never moves HIL or
  Project/PV authority.
- No restart helper exists. The sealed install receipt is the pre-restart
  boundary; after the response completes, the user closes and reopens the app.
  No executable may drain turns, control the app, replay State Travel, or mutate
  project state as part of restart.

## Focused-skill rule

After applying this reference, read only the selected skill's linked resources
that are necessary for the requested action. Preserve every narrower rule in the
selected skill and fail closed on ambiguity.

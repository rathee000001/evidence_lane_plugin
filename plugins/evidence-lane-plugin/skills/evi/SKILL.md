---
name: evi
description: Evidence Lane root router with user-timed State Travel and exactly six primary controls.
---

# Evidence Lane root

After the lifecycle PREPARE hook, the skill—not the hook—first calls the native
`pv_status`, `pv_task_backlog`, and one prompt-relevant bounded `pv_query`.
Internal hook retrieval is lifecycle evidence only and never satisfies this
native read sequence. A prepared exact-work handoff makes State Travel
eligible, but eligibility alone must not display, invoke, or consume it. Route
to State Travel only when the user explicitly requests it or the current host
context is genuinely exhausted and a continuity handoff is needed. Otherwise
run `/evi-boot` atomically and resume the existing governed session; State
Travel is not a normal intake step.

Whenever a canonical task panel exists, re-project its exact complete rows as
the skill-owned `update_plan` action after those native reads and after any
token-driven continuation, stalled Goal, context
compaction, browser or Codex restart, session continuation or resume, or State
Travel destination entry. This must precede source inspection, source mutation,
testing, Git activity, and every later lifecycle call. Require exactly one
in-progress row, preserve order and every completed or pending description
unabridged, keep the panel visible through every pause and HIL, and drop it only
after the physically final six-way HIL is decided and all decision-dependent
work is complete.

Hooks remain lifecycle-only: they may seal PREPARE/COMMIT and bounded event
receipts, but they never call `pv_status`, `pv_task_backlog`, `pv_query`, or
`update_plan`, and never carry the full Plan Lane. After every
`pv_plan_steer_delta`, the skill repeats the three native reads and redraws the
same complete panel. Fail closed when either the native MCP route or host plan
tool is absent.

Preserve one governed project, one live writer, linear execution, and
evidence-first verification under the exact host execution profile. Read-only
recovery agents are allowed only during a genuine State Travel entry. After
entry, do not start a subagent, alternate-checkout writer, background mutation,
or second browser profile unless the user explicitly changes that boundary.

Keep the host Goal attached to the same canonical Plan Lane, active source
boundary, and single-writer session. A UI crash, token wait, required user
input, or HIL wait pauses only dependent work and never marks the Goal
complete. Usage reporting is accounting only and has no task-status effect.
Every reconstruction must retain completed-but-still-governing rows, the one
active row, and all pending rows.

After root `/evi`, expose exactly these six primary controls in this order:

1. `/evi-boot`
2. `/evi-rollback`
3. `/evi-build`
4. `/evi-refresh`
5. `/evi-mode`
6. `/evi-source-intake`

Keep that exact control inventory on every supported Codex profile. The native
catalog contains twenty-one reads and forty-one writes under the complete
Git-backed lifecycle; a host capability restriction never becomes permission
to simulate an unavailable action.

Every skill and command must name MCP tools by the canonical bare name returned
by this exact Evidence Lane server. A connector-generated display namespace is
transport metadata: never copy it into a skill, receipt, command, or stored
contract. The stdio boundary may remove such a namespace only when its suffix
exactly matches a tool registered on the active server; every unknown prefix or
suffix must reach the MCP dispatcher unchanged and fail closed.

On Codex, accept lifecycle proof only from the installed native server identity
`evidence-lane` and its canonical `mcp__evidence_lane__*` catalog. `codex_apps`,
Google Drive, a network tunnel, an external connector, `plugin-runtime`, a legacy
version-labelled namespace, or any duplicate surface is never a fallback. A
collision-safe hexadecimal host display prefix may be normalized at transport
only; it is not proof. After install, enablement, upgrade, or server-code
replacement, require a Codex MCP catalog reload or a fresh Codex task before
claiming the new package is active.

`/evi-source-intake` is the single generalized intake surface. It auto-detects
all eighteen canonical lanes and Project Engulf, accepts exact overrides, and
always includes Chat Lineage. `/evi-mode` remains a separate one-command
sidecar for ordered intersections and explicit custom-mode briefs.

`/evi-plugin` is an administrative sidecar outside the six primary controls.
It lists, registers, routes, or separately drops at most eight additional
persistent connector/toolchain plugins. Its `SETTINGS:CODEX` view
exposes eight structured slots with one-time purpose, role/schema, host profile,
and optional governed backend runtime. It stores environment-variable names
only, preserves dropped history, and returns to the prior lifecycle position.

`/evi-build` presents the six HIL outcomes. Only the exact case-sensitive user
token `APPROVE` may call `pv_fuse`; continuation, discussion, install, tests,
or any other token never implies approval. `/evi-refresh` creates an unaccepted
candidate and stops at HIL. `/evi-rollback` moves only the accepted pointer.

The governed session and live Flash/capture attachment persist across host
tasks until `/evi-exit-boot`. Exit Boot fully detaches ENV/UOP Flash context
and visible prompt/response capture while preserving the installed plugin,
verified Flash receipt, immutable evidence store, candidates, backlog, and
pointer. A later `/evi-boot` verifies Flash again and reattaches the runtime.
Never run State Travel merely because a handoff exists and never store private
model reasoning. State Travel may preserve exact unfinished work, a pending
candidate, or explicit accepted context; it does not require acceptance. If the
user instead continues in the unchanged host after Fuse, call
`pv_begin_next_turn` with `continue_same_host=true` and exact reason
`EXPLICIT_USER_CONTINUATION`; preserve the sealed receipt, record its
supersession, and do not move the pointer.

`/evi-plan` is a Codex-only Planning sidecar outside the six primary controls.
If native Plan mode is not active, it returns the `/pl` reminder without
persisting tasks. After planning, it writes the canonical Plan Lane and returns
the short Goal prompt the user copies into the host-owned Goal.

---
name: evi
description: Evidence Lane root router with user-timed State Travel and exactly six primary controls.
---

# Evidence Lane root

After the `UserPromptSubmit` hook seals its transport envelope and the installed
lifecycle skill runtime consumes it into a PREPARE receipt, the skill first
calls native `pv_status`, `pv_task_backlog`, and one prompt-relevant bounded
`pv_query`.
Internal hook retrieval is lifecycle evidence only and never satisfies this
native read sequence. A prepared exact-work handoff makes State Travel
eligible, but eligibility alone must not display, invoke, or consume it. Route
to State Travel only when the user explicitly requests it or the current host
context is genuinely exhausted and a continuity handoff is needed. Otherwise
run `/evi-boot` atomically and resume the existing governed session; State
Travel is not a normal intake step.

Whenever a canonical task panel exists, validate its exact complete native
ledger and activate the aligned host window of at most ten rows containing the
sole ACTIVE row. Reuse that window on ordinary turns; call skill-owned
`update_plan` only for initial activation, observed panel loss, current-window
status changes, current-window Plan steers, or advancement to the next window.
Use the sealed compact continuity header as the host Plan `explanation` and the
current window as its only task items. The header carries accepted PV/pointer,
absolute ACTIVE row, window/total coordinates, next HIL boundary, and physical
final row. Detailed next/queued HIL records, proposed PVs, choices, and
dependency connections belong only to the Evidence Lane project renderer.
This must precede source inspection, source mutation, testing, Git activity,
and every later lifecycle call after such a trigger. Preserve complete order
and every description in native authority, keep the current host window visible
through every pause and HIL, and drop it only after the human Goal-completion
disposition or a passed exact task State Travel handoff.

Hook command files remain transport-only: they validate, redact, bound,
deduplicate, and seal event envelopes. The installed lifecycle skill runtime
owns PREPARE/COMMIT and bounded lifecycle receipts after envelope validation.
Hook adapters never call `pv_status`, `pv_task_backlog`, `pv_query`, or
`update_plan`, and never carry the full Plan Lane. After every
`pv_plan_steer_delta`, the skill repeats the three native reads and validates
the complete ledger. Synchronize the host window only when the receipt's linked
task is inside the current ten-row window; an outside-window Delta remains
ledger-only until that window becomes active. This is Plan synchronization,
never the Refresh lifecycle action. Fail closed when a required native MCP route or host plan
tool is absent.

Before Plan mutation, distinguish an ordinary question/readback from a Plan
steer. Ordinary requests keep their lineage plus native reads but append no
Delta and do not change the Step Task List. Only a request that changes the
active Goal contract, dependency, acceptance, stop, release, or HIL path calls
`pv_plan_steer_delta`; link it to the existing logical row when possible, then
synchronize CURRENT CHANGE exactly once only when its row is in the active host
window.

Preserve one governed project, one live writer, linear execution, and
evidence-first verification under the exact host execution profile. Read-only
recovery agents are allowed only during a genuine State Travel entry. After
entry, do not start a subagent, alternate-checkout writer, background mutation,
or second browser profile unless the user explicitly changes that boundary.

Keep the host Goal attached to the same canonical Plan Lane, active source
boundary, and single-writer session. A UI crash, token wait, required user
input, or HIL wait pauses only dependent work and never marks the Goal
complete. Usage reporting is accounting only and has no task-status effect.
Every reconstruction validates completed-but-still-governing rows, the one
active row, and all pending rows in native authority, then activates only the
exact current host window.

Goal completion is a separate human-owned boundary for every governed project.
Only the exact visible command `MARK GOAL COMPLETE` may authorize it, with one
of two exact dispositions: `COMPLETE_THIS_TASK_AND_STATE_TRAVEL` or
`COMPLETE_FULLY`. A HIL decision, candidate, Plan transition, passing test,
automation, task advance, pause, or stall cannot mark a Goal complete. Goal
completion never implies HIL approval, Fuse, pointer movement, Git, install,
merge, or deploy authority.

After root `/evi`, expose exactly these six primary controls in this order:

1. `/evi-boot`
2. `/evi-rollback`
3. `/evi-build`
4. `/evi-refresh`
5. `/evi-mode`
6. `/evi-source-intake`

Use one deterministic direct command map for these six controls. An exact slash
command and a conservative unambiguous ordinary-language request select the
same existing skill; do not invent another command or skill. Selection alone
executes no lifecycle action, and the selected skill still performs its native
reads and gates. Ambiguity fails closed. Plan-panel or Step Task List restore,
reactivation, or synchronization belongs to this lifecycle/host Plan path and
must never infer, invoke, or alias the Refresh lifecycle action.

Keep that exact control inventory on every supported Codex profile. The native
catalog contains twenty-six reads and fifty-seven writes under the complete
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

When a task needs evidence from a lane, route it through the single
`EVIDENCE_LANE_BOUNDED_LANE_QUERY_V1` workflow defined by
`../evi-source-intake/SKILL.md`. The native read order is `lane_catalog`,
`lane_status`, `lane_search`, then `lane_fetch` only for an exact returned
source path. The only diagnostic path templates are
`<EVIDENCE_LANE_DATA_ROOT>/projects/<project_id>/accepted/<PVn>/lanes/<canonical_lane_id>/<sqlite_filename>`
and
`<EVIDENCE_LANE_DATA_ROOT>/projects/<project_id>/candidates/<candidate_id>/lanes/<canonical_lane_id>/<sqlite_filename>`.
They are provenance validators, never permission to hunt for, open, copy, or
offload a whole lane SQLite database. Keep queries and results inside native
tool boundaries, preserve the workflow's authority/freshness provenance, and
never substitute transcript, scrollback, browser history, or live-source
inference.

`/evi-plugin` is an administrative sidecar outside the six primary controls.
It lists, registers, routes, or separately drops at most eight additional
persistent connector/toolchain plugins. Its `SETTINGS:CODEX` view
exposes eight structured slots with one-time purpose, role/schema, host profile,
and optional governed backend runtime. It stores environment-variable names
only, preserves dropped history, and returns to the prior lifecycle position.

`/evi-canon` is a separate linked-work sidecar outside the six primary
controls. It governs bounded task-to-task and explicitly authorized
task-to-subagent exchange through exact graph edges, immutable envelopes,
receiver-owned three-way Canon decisions, results, and bounded backfire.
Canon cannot promote Project Truth or Agent Learning, replay Project HIL, or
move a PV pointer. `/evi-learning` is a separate project-isolated AI Learning
sidecar for accepted-lesson retrieval, evidence-backed candidates, Learning
decisions, and revocation. Learning never becomes Project Truth, Canon, or the
Formula Engine.

Host-managed ChatGPT/Codex memories remain a nonauthoritative optional recall
layer. They are never imported by a hook or treated as required-rule, Project
Truth, Canon, ChatLineage, candidate, or accepted Learning authority. Any later
Learning use requires one explicit immutable provenance receipt; recording it
neither creates a candidate nor invokes Learning or Project HIL.

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
For ordinary planning it validates the host Plan context before a native Plan
write. At a State Travel destination, the active skill restores the complete
host Plan, stops for explicit host Plan acceptance, then invokes the Evidence
Plan verification automatically. If the API cannot attest its Plan-mode
selector, record `HOST_MODE_SELECTOR_UNAVAILABLE` without fabricating mode
activation. After Evidence Plan passes, the active skill starts the carried
Goal through the supported host action; do not ask the user to type `/pl`,
`/evi-plan`, or paste a Goal prompt between destination phases.

## MCP routing contract

Before the first MCP call, read `references/mcp-tool-routing.v1.json` and use
the ordered route for `evi`. `MCP_ROUTING_FAIL_CLOSED`: if the bundled
`evidence-lane` dependency, an exact tool, or a required result is missing or
ambiguous, stop and report it; never rewrite prefixes, substitute a tool,
reorder a write, or infer success.

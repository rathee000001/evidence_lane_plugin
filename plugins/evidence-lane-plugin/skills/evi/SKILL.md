---
name: evi
description: Evidence Lane root router with user-timed State Travel and exactly six primary controls.
---

# Evidence Lane root

First call `pv_status`. A prepared exact-work handoff makes State Travel
eligible, but eligibility alone must not display, invoke, or consume it. Route
to State Travel only when the user explicitly requests it or the current host
context is genuinely exhausted and a continuity handoff is needed. Otherwise
run `/evi-boot` atomically and resume the existing governed session; State
Travel is not a normal intake step.

After root `/evi`, expose exactly these six primary controls in this order:

1. `/evi-boot`
2. `/evi-rollback`
3. `/evi-build`
4. `/evi-refresh`
5. `/evi-mode`
6. `/evi-source-intake`

`/evi-source-intake` is the single generalized intake surface. It auto-detects
all eighteen canonical lanes and Project Engulf, accepts exact overrides, and
always includes Chat Lineage. `/evi-mode` remains a separate one-command
sidecar for ordered intersections and explicit custom-mode briefs.

`/evi-plugin` is an administrative sidecar outside the six primary controls.
It lists, registers, routes, or separately drops at most eight additional
persistent connector/toolchain plugins. Its `SETTINGS:CODEX|CHATGPT` view
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
the short Goal prompt the user copies into the host-owned Goal. ChatGPT uses the
same plugin code and append-only persistent store but never claims Codex Plan,
Goal, or task-panel UI.

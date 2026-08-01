---
description: Start or resume the six-control persistent Evidence Lane
argument-hint: [project_id]
---

# /evi - universal Evidence Lane

First call `pv_status`. A prepared accepted-PV handoff makes
`/evi-state-travel` eligible, but eligibility alone must not display, invoke,
or consume it. Route to State Travel only when the user explicitly requests it
or the current host context is genuinely exhausted and a continuity handoff is
needed. Otherwise run `/evi-boot` atomically and resume the existing governed
session; State Travel is not a normal intake step.

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
persistent connector/toolchain plugins. It stores environment-variable names
only, preserves dropped history, and returns to the prior lifecycle position.

`/evi-build` presents the six HIL outcomes. Only the exact case-sensitive user
token `APPROVE` may call `pv_fuse`; continuation, discussion, install, tests,
or any other token never implies approval. `/evi-refresh` creates an unaccepted
candidate and stops at HIL. `/evi-rollback` moves only the accepted pointer.

The governed session and live Flash/capture attachment persist across host
tasks until `/evi-exit-boot`. Exit Boot fully detaches ENV/UOP Flash context
and visible prompt/response capture while preserving the installed plugin,
verified Flash receipt, immutable evidence store, candidates, backlog, and
pointer. A later `/evi-boot` verifies Flash again and reattaches the runtime. Never
run State Travel merely because a handoff exists, never run it without an
accepted sealed handoff plus one of the two explicit triggers, and never store
private model reasoning. If the user instead continues in the unchanged host
after Fuse, call `pv_begin_next_turn` with `continue_same_host=true` and exact
reason `EXPLICIT_USER_CONTINUATION`; preserve the sealed receipt, record its
supersession, and do not move the pointer.

---
description: Start or resume the six-control persistent Evidence Lane
argument-hint: [project_id]
---

# /evi - universal Evidence Lane

First call `pv_status`. If and only if an accepted-PV handoff is prepared,
display `/evi-state-travel` as the top recovery event. Otherwise run
`/evi-boot` atomically; State Travel is not a normal intake step.

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

`/evi-build` presents the six HIL outcomes. Only the exact case-sensitive user
token `APPROVE` may call `pv_fuse`; continuation, discussion, install, tests,
or any other token never implies approval. `/evi-refresh` creates an unaccepted
candidate and stops at HIL. `/evi-rollback` moves only the accepted pointer.

The governed session persists across host tasks until `/evi-exit-boot`. Never
run State Travel without an accepted sealed handoff and never store private
model reasoning.

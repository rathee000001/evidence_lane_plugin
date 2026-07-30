---
description: Start or resume the ordered persistent Evidence Lane
argument-hint: [project_id]
---

# /evi - generalized Evidence Lane flow

Always show `/evi-mode` beside `/evi`. It may be called at any point to
classify one or more intersecting ENV15 modes and canonical lanes, including
Chat Lineage, then return to the prior lifecycle position.

The static catalog remains top-down in exactly this order:

1. `/evi-00-state-travel`
2. `/evi-01-boot` — one atomic Boot plus locked ENV/UOP Flash
3. `/evi-02-git`
4. `/evi-03-local`
5. `/evi-04-sqlite-pv-candidate-loader`
6. `/evi-05-chat-lineage`
7. `/evi-06-discussion`
8. `/evi-07-analysis`
9. `/evi-08-plan`
10. `/evi-09-docs`
11. `/evi-10-data-excel`
12. `/evi-11-ppt`
13. `/evi-12-pdf-ocr`
14. `/evi-13-images-ocr`
15. `/evi-14-artifacts`
16. `/evi-15-custom`
17. `/evi-16-research`
18. `/evi-17-project-engulf`
19. `/evi-18-sqlite-brain`
20. `/evi-30-build-pv-entry`
21. `/evi-40-status`
22. `/evi-50-task-plan`
23. `/evi-51-backlog`
24. `/evi-60-classify`
25. `/evi-70-lane-route`
26. automatic task completion, Refresh, and sealed exit PV
27. `/evi-80-hil`
28. `/evi-90-pv-fuse`
29. `/evi-99-pv-rollback`
30. `/evi-exit-boot` — explicit governed-session deactivation

State Travel is the conditional preflight at the top of the catalog. When a
prepared accepted-PV handoff exists, run it first in a fresh Codex task or
ChatGPT chat, atomically verify Boot/Flash plus the exact accepted pointer and
seals, and stop in `WAITING_FOR_NEXT_USER_COMMAND`. With no prepared handoff,
do not pause on State Travel: `/evi-01-boot` is the first normal action.

Seventeen source-intake commands are visible between Boot and Build PV Entry.
They route the eighteen canonical lanes because Mode remains an internal
compatibility lane exposed only through the separate `/evi-mode` control
sidecar, never as source intake. The stack is universal, not code-only. Entry
and exit slips are automatic sealed artifacts. Only exact `APPROVE` may Fuse;
rollback moves only accepted pointers. Boot remains active until the user
explicitly invokes `/evi-exit-boot`.

After atomic Boot/Flash succeeds, visibly render the returned
`ordered_source_intake_commands` and `suggested_next_prompt`. The user may
choose Git, Local, or any other displayed intake lane; never silently jump
past source intake into Build PV Entry.

At each HIL or State Travel stop, visibly show the returned
`suggested_next_prompt`. Never choose or submit it, and never claim the MCP
wrote gray text into the host composer.

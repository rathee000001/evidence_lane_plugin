---
description: Record one exact six-way human HIL decision
argument-hint: APPROVE | APPROVE_WITH_DELTA: <correction> | MORE_RESEARCH: <question> | ROLLBACK[: target] | REJECT: <reason> | FAIL: <gate>
---

# /evi-80-hil

Record the decision verbatim. Exact `APPROVE` calls `pv_fuse`; every other
supported choice calls `hil_decide` once. Only exact approval promotes.
Before waiting for the human, show the candidate's sealed
`suggested_next_prompt` from `exit_slip.json`. Never select, prefill, or submit
a HIL outcome on the user's behalf.

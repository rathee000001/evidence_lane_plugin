---
description: Build an unaccepted candidate or record the exact six-way HIL
argument-hint: [project_id session_id optional-HIL-token]
---

# /evi-build

From an initial entry call `pv_build_initial`. From a completed bounded task,
use `/evi-refresh`. At pending HIL, show exactly `APPROVE`,
`APPROVE_WITH_DELTA`, `MORE_RESEARCH`, `ROLLBACK`, `REJECT`, and `FAIL`.
Only an exact user-supplied case-sensitive `APPROVE` calls `pv_fuse`; all five
other outcomes call `hil_decide` with their required bounded payload. Never
replay a decision against another candidate and never infer approval from the
user continuing work. After a candidate build, render the complete Exit Slip
and stop.

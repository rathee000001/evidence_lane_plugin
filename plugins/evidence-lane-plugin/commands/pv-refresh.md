---
description: Refresh final source into the next unaccepted PV candidate
argument-hint: <project_id> <session_id>
---

# Evidence Lane PV Refresh

1. Require one active classified task.
2. Confirm the exact final host source state with
   `HOST_SANDBOX_FINAL_STATE_CONFIRMED` or, for a user-mediated host,
   `USER_APPLIED_AND_PULL_CONFIRMED`.
3. Call `pv_refresh`.
4. Report entry PV, proposed ordinal, candidate/seal, source Delta, reused and
   rebuilt lanes, tombstones, acceptance health, freshness, warnings, and
   lineage receipt.
5. State that `entry_slip.json` and `exit_slip.json` were generated as internal
   sealed artifacts; they are not user commands.
6. State that the candidate remains unaccepted and the pointer has not moved.
7. Stop at the six-way HIL. Use `/pv-fuse APPROVE` for approval; use `/pv-hil`
   for Delta, research, rollback, rejection, or failure.

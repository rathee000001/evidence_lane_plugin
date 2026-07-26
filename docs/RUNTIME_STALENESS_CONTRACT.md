# Runtime staleness contract

## Two simultaneous truths

At task entry, the accepted PV is exact entry-state authority. After the first
file creation, modification, or deletion, it does not automatically become a
current index of the changing worktree.

From that first mutation until a new candidate is approved:

- accepted-PV search/query results describe the accepted entry state only;
- exact repository reads, Git identity, and Git diff describe current source;
- ChatLineage records the mutation and marks `MUTATED_AFTER_ENTRY`;
- no tool or agent may silently describe an accepted-PV query as post-edit truth;
- Refresh re-ingests the complete final source and seals the exit candidate;
- only exact `APPROVE` makes that candidate the next accepted current entry.

This first HIL does not implement a continuously refreshed mid-task index.
That is deliberate: silently mixing stale entry intelligence with live edits is
worse than exposing the boundary.

## HIL recovery

`APPROVE_WITH_DELTA` and `MORE_RESEARCH` retain the prior pointer and preserve
the candidate source. Continuation is allowed only when the live repository
still matches that exact candidate and the next task class/outcome exactly
matches the human correction or research payload.

`REJECT` and `FAIL` retain the prior pointer. `hil_return_to_accepted` succeeds
only after the live source matches the prior accepted PV again. A receipt records
the prior run, pointer digest, reason, and `pointer_moved=false`.

If the failed/rejected run was the initial PV1 candidate, no accepted PV exists;
return-to-accepted is impossible. The session must be closed and the corrected
initial-entry flow restarted.

<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Project PV content-addressed storage

The registered external project root is the sole live working authority. It is
also the current working candidate overlay: Evidence Lane records a bounded
identity receipt over those bytes and does not copy them into a second
`candidates/` tree.

## Authority layout

- `active_pointer.json` remains compare-and-swap lifecycle metadata outside the
  accepted artifact.
- `accepted/` contains only the one governed root-PV snapshot ZIP selected by
  the current retention law; it is never queried as live authority.
- Transient sessions, locks, candidate-overlay receipts, capture-route receipts,
  and accepted-swap journals are not Project Truth and are excluded from the
  accepted archive identity.
- The live project root is never rewritten merely to query, validate, or stage a
  candidate.

## Future exact-HIL transition

Only the existing exact Project HIL promotion route may invoke the transition.
It must:

1. Validate the candidate receipt, parent accepted pointer, proposed PV, and
   post-seal approval receipt.
2. Build one deterministic ZIP outside `accepted/` while proving that the live
   working identity did not change during the build.
3. Store each unique SHA-256 object once and map every project-relative member
   through `PROJECT_PV_MANIFEST.json`.
4. Validate the exact ZIP member set, every object hash, the canonical manifest
   hash, and the candidate identity before any swap.
5. Swap `accepted/` under the project store lock, then compare-and-swap the
   pointer. If the pointer write fails, restore the prior accepted directory.
6. Verify that exactly one accepted artifact matches the new pointer before
   purging the prior artifact under the user's single-retention policy.

Plan acceptance, package installation, Git activity, tests, and storage status
queries never invoke this transition and never move the pointer.

## Retention and rollback semantics

The single retained archive carries the prior accepted PV and manifest identity
as lineage. It does not claim that the prior accepted bytes remain embedded or
that exact prior-byte rollback is available after the approved purge. Restoring
older bytes therefore requires separately retained external evidence or a
future, separately approved retention policy.

Temporary accepted views, when explicitly required by a hard-restore route, are
materialized outside the project authority and removed after use. Ordinary
status, query, Plan, and Delta work never opens the accepted ZIP.

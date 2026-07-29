# First private HIL runbook

This checklist is not an approval token. Run it only after the State Travel
candidate receives the exact human authorization required for a real,
non-sensitive project lifecycle.

## Preconditions

- cachebuster-installed plugin and hash-locked virtual environment;
- exact ENV15/UOP15 flash authority;
- one non-sensitive local or private test source;
- exact project ID, owner/name where applicable, code mode, and permitted
  branch;
- one user, one writer, and no parallel source mutation;
- user-owned durable data root outside the plugin cache;
- required Google Drive connector installed through normal OAuth; no governed
  upload, direct server token, deployment, commit, or remote-Git action unless
  separately authorized.

## Proof sequence

1. Run `/ev`. Call `runtime_doctor`, `session_flash_status`, and `pv_status`.
2. Verify source/runtime version parity and the exact flash manifest, locks,
   read-only SQLite checks, and disclosed warnings.
3. Verify the required host Google Drive connector with a read-only call and
   keep it distinct from the direct ephemeral-server backend.
4. Choose `/git` or `/local`. Register or enroll one exact source/branch without
   overwriting existing lineage. A selected Git update must be a clean,
   path-bounded fast-forward and must not perform a remote write.
5. If needed, set exact one-candidate lane-route overrides and verify they are
   named, bounded, and re-locking.
6. Boot or resume one governed session. Bind the current host session ID and
   record host, server-filesystem class, source-edit authority, `entry_pv`, pointer
   generation, highest accepted ordinal, freshness, backlog, and pending HIL.
7. If no accepted history exists, build PV1 and prove all eighteen lanes report
   `FULL_PV1`.
8. Validate the root package and recursive lane bundle: exact membership,
   SHA-256, SQLite integrity/foreign keys, registry IDs, route seal, MMD/DOT,
   tools, TF-IDF, and parser capability receipts.
9. Inspect the PV1 candidate as `UNACCEPTED_CANDIDATE`.
10. Present the six HIL choices. Only `/pv-fuse ... APPROVE` may promote PV1;
    it must directly hand off into the same accepted bytes.
11. Recreate the host task/chat and use `/ev` plus `session_resume` to prove
    accepted PV1 loads directly
    as `entry_pv` without a second full build.
12. Plan any waiting tasks, claim exactly one, and classify one bounded active
    task.
13. Use accepted-PV search/fetch only as entry-state evidence. After source
    mutation, use verified live source/diff as current truth.
14. Append visible activity and run exact `cmd:` acceptance checks. Leave prose
    checks `PENDING_HUMAN_REVIEW`.
15. Confirm the final source boundary and run `/pv-refresh`.
16. Prove PV2 is materialized from PV1: unchanged stable lane files are
    byte-identical, only affected lanes rebuild, deletions become tombstones,
    and no normal full rebuild occurs.
17. Validate and inspect PV2 as an unaccepted candidate, its Delta, ancestry,
    seals, warnings, and acceptance results.
18. Present exactly one of:
    `APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`,
    `ROLLBACK[: PVn]`, `REJECT`, or `FAIL`.
19. If approved, prove byte-preserving Fuse, one CAS pointer advance, automatic
    internal entry receipt, and direct handoff.
20. In a new host task, prove accepted PV2 becomes direct entry and the next
    candidate would be PV3.
21. Exercise rollback only with explicit HIL authority:
    backward, forward, same-target no-op, `PROMPT <index>`/`TURN <id>`, and bare
    rollback to the current prompt/session entry PV. Prove raw prompts were not
    stored, candidates and source remain unchanged, and the
    next ordinal remains above highest accepted history.
22. If the server is ephemeral, prove encrypted durable-store write and readback
    by hash before treating handoff as durable.
23. Seal the complete run evidence and stop at the next HIL.

Stop on any mismatch, stale pointer, unauthorized branch, dirty forbidden
initial state, route conflict, package validation failure, mutating acceptance
check, missing durable persistence, or exact-token failure.

Automated fixtures establish implementation behavior only. They do not accept
a real project PV or authorize any external write.

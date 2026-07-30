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
- Google Drive connector available through normal host OAuth when the selected
  persistence route needs it; no governed upload, direct server token,
  deployment, commit, or remote-Git action unless separately authorized.

## Candidate installation evidence

When the user explicitly defines the HIL as a cross-host installation test,
complete this bounded distribution sequence before presenting the decision:

1. commit the exact candidate only on its feature branch;
2. push only that branch without force, leaving local and remote `main`
   unchanged;
3. install Codex from the exact branch commit and verify it in a fresh task;
4. run the same local build through OpenAI Secure MCP Tunnel;
5. create the personal ChatGPT developer plugin with **Tunnel** selected and
   verify it in a fresh Work chat;
6. record commit, branch, installation receipts, tunnel health, tool inventory,
   local durable-state reuse, and all fail-visible limitations.

This evidence boundary is not Fuse. It does not accept a PV, move a pointer,
merge `main`, deploy a public endpoint, or infer HIL success.

## Proof sequence

1. Run `/evi`. With no prepared State Travel handoff, `/evi-01-boot` is the
   first normal action. As one atomic operation, call
   `runtime_doctor`, verify source/runtime version parity, verify the exact
   locked ENV/UOP manifest, locks, read-only SQLite checks, and warnings, then
   boot or resume the governed session. If any installation, Flash, project,
   pointer, storage, or session gate fails, stop the entire Boot.
2. If the selected route needs Drive, verify the declared host Google Drive
   connector with a read-only call and keep it distinct from the direct
   ephemeral-server backend. A durable local route does not require Drive.
3. Inspect the Boot result's complete `ordered_source_intake_commands`, then
   choose `/evi-02-git`, `/evi-03-local`, or another displayed lane. Register
   or enroll one exact source/branch without overwriting existing lineage. A
   selected Git update must be a clean, path-bounded fast-forward and must not
   perform a remote write. Explicit branch replacement narrows authority to
   the one already-checked-out branch and writes a receipt.
4. Present all seventeen ordered source-intake commands before Build PV Entry.
   This includes `/evi-04-sqlite-pv-candidate-loader` and
   `/evi-17-project-engulf`. Keep the single `/evi-mode` control sidecar
   available separately. When Planning is selected, verify it appends only a
   privacy-minimized control-plane event and refreshes the derived Plan runtime
   SQLite projection; it must not edit canonical Plan source or queue a task.
   If needed, set exact one-candidate lane-route overrides and verify they are
   named, bounded, and re-locking.
5. Bind the current host session ID and
   record host, server-filesystem class, source-edit authority, `entry_pv`, pointer
   generation, highest accepted ordinal, freshness, backlog, and pending HIL.
6. If no accepted history exists, run `/evi-30-build-pv-entry` and prove all
   eighteen lanes report `FULL_PV1`.
7. Validate the root package and recursive lane bundle: exact membership,
   SHA-256, SQLite integrity/foreign keys, registry IDs, route seal, MMD/DOT,
   tools, TF-IDF, and parser capability receipts.
8. Inspect the PV1 candidate as `UNACCEPTED_CANDIDATE`.
9. Verify `exit_slip.json` and the MCP result contain the same
   `evidence-lane.next-action.v1`, render its neutral
   `suggested_next_prompt` visibly, and stop. Host-rendered gray text is
   optional UI evidence; it is never a submitted choice.
10. Present the six HIL choices through `/evi-80-hil`. Only
    `/evi-90-pv-fuse ... APPROVE` may promote PV1; it must directly hand off
    into the same accepted bytes.
11. Recreate the host task/chat and use `/evi` plus `session_resume` to prove
    accepted PV1 loads directly
    as `entry_pv` without a second full build.
12. Plan any waiting tasks, verify their append-only `QUEUED` events, claim
    exactly one, and classify one bounded active task. Prove the universal
    status vocabulary and explicit history-preserving DROP/SUPERSEDE routes.
13. Use accepted-PV search/fetch only as entry-state evidence. After source
    mutation, use verified live source/diff as current truth.
14. Append visible activity and run exact `cmd:` acceptance checks. Leave prose
    checks `PENDING_HUMAN_REVIEW`.
15. Complete the task through `task_complete_and_refresh`. Confirm the final
    source boundary, automatically run Refresh, create entry/exit slips, and
    stop at HIL. Do not require a separate Exit or Refresh command.
16. Prove PV2 is materialized from PV1: unchanged stable lane files are
    byte-identical, only affected lanes rebuild, deletions become tombstones,
    and no normal full rebuild occurs.
17. Validate and inspect PV2 as an unaccepted candidate, its Delta, ancestry,
    seals, warnings, and acceptance results.
18. Present exactly one of:
    `APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`,
    `ROLLBACK[: PVn]`, `REJECT`, or `FAIL`.
19. If approved, prove byte-preserving Fuse, one CAS pointer advance, and a
    sealed State Travel handoff. Prove the origin window does not enter the
    accepted PV as the next turn.
20. In a fresh Codex task or ChatGPT chat, run `/evi-00-state-travel`; prove
    atomic Boot/Flash, pointer generation, manifest, and package seals are
    verified, accepted PV2 becomes direct entry, the next candidate would be
    PV3, and the state stops at `WAITING_FOR_NEXT_USER_COMMAND`.
21. Exercise `/evi-99-pv-rollback` only with explicit HIL authority:
    backward, forward, same-target no-op, `PROMPT <index>`/`TURN <id>`, and bare
    rollback to the current prompt/session entry PV. Prove raw prompts were not
    stored, candidates and source remain unchanged, and the
    next ordinal remains above highest accepted history.
22. Invoke `/evi-exit-boot` only when the user explicitly wants to deactivate
    the governed session. Prove it preserves installation, Flash receipt, PVs,
    candidates, lineage, backlog, Plan runtime projection, and pointer.
23. If the server is ephemeral, prove encrypted durable-store write and readback
    by hash before treating handoff as durable.
24. Seal the complete run evidence and stop at the next HIL.

Stop on any mismatch, stale pointer, unauthorized branch, dirty forbidden
initial state, route conflict, package validation failure, mutating acceptance
check, missing durable persistence, or exact-token failure.

Automated fixtures establish implementation behavior only. They do not accept
a real project PV or authorize any external write.

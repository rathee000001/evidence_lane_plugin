# First private HIL runbook

This runbook is an execution checklist, not an approval token.

## Preconditions

- private repository clone;
- plugin-local virtual environment bootstrapped from the hash-locked file;
- non-sensitive test Git repository selected;
- exact owner, name, and allowed branch known;
- one local Codex agent;
- no active parallel writer;
- no Drive or remote-Git action unless separately authorized.

## Proof sequence

1. `runtime_doctor`
2. `session_flash_status`
3. verify the exact flash manifest, MMD locks, read-only SQLite checks, and
   bounded `SOURCE_PACKET_PARTIAL_INTEGRITY` warning
4. `project_register`
5. `session_boot`; verify `CREATED` or same-digest `REUSED`
6. `pv_build_initial`
7. inspect PV1 candidate and validation receipt
8. exact `hil_decide` with `APPROVE` for PV1
9. `task_classify`
10. accepted-PV `search`/`fetch`
11. perform one bounded sandbox task
12. append visible evidence with `task_record_activity`
13. after the first source mutation, treat accepted-PV queries as entry-state
    evidence only and use exact repository reads/diffs for current source
14. `task_confirm_source_update`
15. `pv_refresh`
16. inspect PV2 candidate and exact Delta
17. choose one five-way HIL outcome
18. if approved, verify pointer moved exactly once
19. for `APPROVE_WITH_DELTA` or `MORE_RESEARCH`, classify only the exact pending
    correction/research contract
20. for `REJECT` or `FAIL`, restore exact accepted source before
    `hil_return_to_accepted`; otherwise close the session
21. `pv_begin_next_turn` and prove the next candidate is PV3
22. `session_close`
23. seal the run evidence

Stop at any mismatch, stale pointer, unauthorized branch, dirty initial source,
package validation failure, missing required persistence, or exact-token
failure.

## Current automated evidence

The test suite covers whole-source Svelte ingestion, exact bytes, chunks/FTS,
PV tamper detection, render warning behavior, the complete PV1-to-PV2 lifecycle,
all five HIL outcomes, pointer CAS, host persistence routing, AES-GCM sealing,
remote-push denial, exact pending-task continuation, source-restore recovery,
session-flash tamper failure, manifest naming, hook output, and a real MCP STDIO
initialization/tool-call/shutdown cycle.

Automated fixture success does not itself accept a real project PV.

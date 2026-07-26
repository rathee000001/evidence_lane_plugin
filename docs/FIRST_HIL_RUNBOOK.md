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
2. `project_register`
3. `session_boot`
4. `pv_build_initial`
5. inspect PV1 candidate and validation receipt
6. exact `hil_decide` with `APPROVE` for PV1
7. `task_classify`
8. accepted-PV `search`/`fetch`
9. perform one bounded sandbox task
10. append visible evidence with `task_record_activity`
11. `task_confirm_source_update`
12. `pv_refresh`
13. inspect PV2 candidate and exact Delta
14. choose one five-way HIL outcome
15. if approved, verify pointer moved exactly once
16. `pv_begin_next_turn` and prove the next candidate is PV3
17. `session_close`
18. seal the run evidence

Stop at any mismatch, stale pointer, unauthorized branch, dirty initial source,
package validation failure, missing required persistence, or exact-token
failure.

## Current automated evidence

The test suite covers whole-source Svelte ingestion, exact bytes, chunks/FTS,
PV tamper detection, render warning behavior, the complete PV1-to-PV2 lifecycle,
all five HIL outcomes, pointer CAS, host persistence routing, AES-GCM sealing,
remote-push denial, manifest naming, hook output, and a real MCP STDIO
initialization/tool-call/shutdown cycle.

Automated fixture success does not itself accept a real project PV.

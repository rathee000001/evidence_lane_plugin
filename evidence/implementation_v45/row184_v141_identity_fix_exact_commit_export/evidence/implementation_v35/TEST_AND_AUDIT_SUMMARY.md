# Evidence Lane 0.7.0 test and audit summary

## Scope

This release adds five bounded improvements without changing the accepted-PV
schema version:

- optional source-intake Git history modes (`AUTO`, `REQUIRED`, `DISABLED`);
- tolerant visible HIL-intent classification without inferred promotion;
- distinct ordered and idempotent mid-turn steer events in Chat Lineage;
- user-timed State Travel plus explicit unchanged-host continuation;
- Vercel catch-all public-path recovery and clean MCP stdio bootstrap.

## Final local gates

- `python -m compileall`: PASS across plugin source, hooks, and remote adapter.
- Ruff 0.16.1: PASS, no findings.
- mypy 2.3.0: PASS, 45 source files, no issues.
- Codex plugin validator: PASS.
- focused v0.7/lifecycle/MCP gate: PASS, 14 tests.
- complete pytest suite: PASS, 92 tests in 467.89 seconds.
- `git diff --check`: PASS.

The first complete-suite run recorded 90 passes and one failure. The failing
real-stdio test proved that an interrupted first-run virtual environment could
leave a Python executable without dependencies and that bootstrap diagnostics
could enter the JSON-RPC stdout stream. The correction now probes runtime
readiness, recovers a partial environment, redirects all bootstrap diagnostics
to stderr, and allows the governed 900-second first-start window. The exact
previously failing test then passed from the partial environment before the
92-test final run.

## Evidence boundaries

The suite includes dummy sources for all eighteen canonical lanes, SQLite
integrity/foreign-key/FTS validation, Git history and CAS reuse, incremental
refresh, candidate-only project overlays, lifecycle/CAS failures, connector
routing, hooks, package validation, and real stdio MCP transport. These tests
prove deterministic implementation behavior in controlled fixtures. They do
not prove production throughput, external-user success, ChatGPT OAuth, a
durable remote origin, or a same-SHA Vercel preview.

Verdict: **FIX-THEN-PURSUE**. Confidence: **96%**. Evidence that would change
the verdict to pursue is an exact-SHA remote branch, verified Codex pickup in a
fresh task, a healthy same-SHA ChatGPT durable origin and Vercel preview,
confirmed compromised-key revocation, and a fresh unaccepted candidate whose
full Exit Slip passes human review.

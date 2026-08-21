# Plan runtime and universal Delta lifecycle candidate

This candidate implements the direct Plan-lane steer without activating,
accepting, dropping, superseding, or reordering any live backlog task. It does
not accept a PV, move a pointer, merge or push `main`, deploy Vercel, or infer
HIL success.

## Implemented law

- `/evi-mode` remains a separate anytime control sidecar. It still appends the
  privacy-minimized ChatLineage receipt. When Planning (`PL`) is selected, it
  also appends a hash-chained, privacy-minimized control-plane event and
  atomically rebuilds `plan_runtime_projection.sqlite`.
- The runtime projection is derived from the task-backlog event ledger and is
  outside every canonical PV lane sector. It never mutates the immutable Plan
  source lane, queues a Delta, changes lifecycle position, creates a
  candidate, or moves a pointer.
- `/evi-08-plan` remains the explicit exact-file Plan source intake/read
  surface. `/evi-50-task-plan` remains the only bounded task-enrollment
  surface. `/evi-51-backlog` reads the ledger and handles only user-explicit
  history-preserving DROP or SUPERSEDE transitions.
- Every Delta derives its current state from one append-only global and
  per-task hash chain using `QUEUED`, `ACTIVE`, `DONE`, `ACCEPTED`,
  `REJECTED`, `DROPPED`, `SUPERSEDED`, `FAILED`, and `ROLLED_BACK`.
- Automatic Refresh marks the active backlog Delta `DONE`. Exact HIL outcomes
  derive the terminal or follow-up state. DROP deletes nothing; SUPERSEDE
  requires and links one different queued replacement.
- Legacy `COMPLETED_ACCEPTED`, `FOLLOW_UP_PENDING`, and
  `ROLLED_BACK_UNACCEPTED` stores migrate deterministically without changing
  task IDs, sequence, or visible history.

## Validation

- final full suite: 69 passed in 363.73 seconds;
- focused backlog, projection, and compatibility suite: 8 passed;
- Ruff 0.16.0 lint and format: pass across 54 source/test files;
- mypy 2.3.0: 39 source files, zero issues;
- Bandit 1.9.4: zero findings;
- `pip check`: no broken requirements;
- pip-audit 2.10.1: no known dependency vulnerabilities;
- wheel and sdist build: pass;
- isolated wheel install, version readback, schema resource, and locked Flash
  resource readback: pass;
- Markdown links: 92 tracked Markdown files, zero missing local targets;
- legacy `/pv-*` and `/ev` user-command scan: pass;
- changed-file real-secret scan: zero findings;
- `git diff --check`: pass.

Machine-local projection rebuild evidence is indicative, not an acceptance
gate: median 12.10 ms for 20 tasks and 14.38 ms for 100 tasks across ten
rebuilds each.

The staged implementation before this evidence directory is tree
`fc92450add40e0f60f29787f5ff3c3c7bbbee6fe`.

## Preserved governance state

The accepted pointer remains null at generation 0. Both prior PV1 candidates
remain preserved and unaccepted:

- `PV1_CANDIDATE__RUN_01KYG2MA34M7V5C9RDMA3T51DJ`
- `PV1_CANDIDATE__RUN_01KYR17PNDNXXJQQ288T3CG18H`

All 20 linear Deltas remain `QUEUED`, including
`EL-PLAN-LANE-AUTOMATIC-LIFECYCLE-DELTA-020`. The current governed session
remains `TASK_CLASSIFIED`; this implementation overlap does not claim or
complete the queued Delta.

## HIL boundary

The next authorized actions are one feature-branch commit, a non-force push of
that feature branch only, exact-commit Codex installation, and private
ChatGPT Secure MCP Tunnel verification. None of those actions accepts a
project PV. Exact `APPROVE` remains required for Fuse; `main`, the accepted
pointer, the two candidates, and every queued Delta remain untouched.

# Git host pickup, Boot flow, visible lineage, and fresh HIL candidate

This candidate incorporates Delta 21 without activating, completing,
accepting, rejecting, dropping, superseding, or reordering any live backlog
Delta. It does not accept a PV, move a pointer, merge or push `main`, deploy
Vercel, or infer HIL success.

## Corrected lifecycle

- A normal `/evi` run starts with atomic `/evi-01-boot`, which verifies the
  installation and locked ENV/UOP Flash together. Only a prepared post-Fuse
  handoff enters through `/evi-00-state-travel` in a fresh Codex task or
  ChatGPT chat.
- Successful Boot returns and displays all seventeen source-intake commands in
  top-down order, beginning with Git, Local, and SQLite PV Candidate Loader.
  `/evi-mode` remains a separate anytime sidecar.
- One exact already-checked-out Git branch may replace the registered branch
  authority only through an explicit governed flag. The operation narrows the
  authority to one branch, records the prior authority, performs no remote
  write, and cannot silently broaden the branch set.
- The host-owned composer remains outside MCP control. The portable next action
  is still rendered visibly from the returned Exit Slip or State Travel
  contract and is never auto-submitted.

## Visible turn lineage

- `UserPromptSubmit` stores the exact visible user prompt only after
  deterministic secret redaction and binds it to the entry PV, turn ID,
  pointer generation, and a contiguous prompt-index SHA-256 chain.
- A nonblocking `Stop` hook stores the exact visible assistant response only
  after deterministic secret redaction, links it to the same prompt/turn, and
  appends one idempotent ChatLineage event.
- The Stop hook returns only `{"continue":true}`. It never returns a blocking
  decision or continuation reason, never edits the composer, never captures
  private model reasoning, and cannot continue past HIL.

## Plan mode and universal classification

- Planning-mode detection appends a hash-chained, privacy-minimized
  control-plane event and atomically rebuilds the derived Plan runtime
  projection.
- Detection alone does not invent or queue a Delta, change lifecycle position,
  mutate the canonical Plan source lane, create a candidate, or move a pointer.
- The append-only universal Delta lifecycle remains `QUEUED`, `ACTIVE`, `DONE`,
  `ACCEPTED`, `REJECTED`, `DROPPED`, `SUPERSEDED`, `FAILED`, and
  `ROLLED_BACK`. DROP and SUPERSEDE preserve history; SUPERSEDE links one
  different queued replacement.

## Validation

- complete suite: 70 passed in 390.82 seconds;
- focused Planning-mode and backlog suite: 12 passed;
- focused prompt/response hook regression: 1 passed;
- official OpenAI plugin validator: pass;
- Ruff 0.16.0 lint and format: pass across 57 Python files;
- mypy 2.3.0: 42 source and hook files, zero issues;
- Bandit 1.9.4: zero findings;
- `pip check`: no broken requirements;
- pip-audit 2.10.1: no known dependency vulnerabilities;
- root and plugin wheel/sdist builds: pass;
- isolated wheel install and version/schema/locked-Flash resource readback:
  pass;
- Markdown links: 93 tracked Markdown files, zero missing local targets;
- legacy `/pv-*` and `/ev` user-command scan: pass;
- tracked real-secret scan: zero findings; `.env.local` remains ignored and
  untracked;
- `git diff --check`: pass.

The staged implementation before this evidence directory is tree
`f1f6d8fa6e1cb9aa6b3401ca57c9f2d342c6eda8`.

## Preserved governance state

The accepted pointer remains null at generation 0. Both earlier PV1 candidates
remain preserved and unaccepted:

- `PV1_CANDIDATE__RUN_01KYG2MA34M7V5C9RDMA3T51DJ`
- `PV1_CANDIDATE__RUN_01KYR17PNDNXXJQQ288T3CG18H`

All 21 linear Deltas remain `QUEUED`, including
`EL-PLAN-LANE-AUTOMATIC-LIFECYCLE-DELTA-020` and
`EL-GIT-HOST-PICKUP-BOOT-HIL-DELTA-021`. The existing session remains
`TASK_CLASSIFIED`; implementation overlap does not claim or complete any
queued Delta.

## Publication and HIL boundary

The authorized next steps are one commit and non-force push of
`agent/evi-persistent-lifecycle-v0.5.0`, exact-commit Git installation in
Codex, private Secure MCP Tunnel refresh for ChatGPT, host pickup verification,
and construction of one corrected unaccepted candidate. Vercel remains parked
because the private tunnel is the narrower working transport.

After those checks, stop at the new six-way `/evi-80-hil`. Exact `APPROVE`
remains the only Fuse token. `main`, the accepted pointer, both preserved
candidates, and all queued Deltas remain untouched until that later human
decision.

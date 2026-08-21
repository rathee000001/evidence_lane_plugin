# `/evi` persistent lifecycle candidate

The candidate is validated for one exact feature-branch commit and non-force
push. It does not accept a PV, move a pointer, merge or push `main`, deploy
Vercel, or infer HIL success.

## Gray prompt result

The exact Claude comparison is pinned to commit
`889c28a33f16469e8e74aa62ef65559e4a74379d` and tree
`ad81f88d8014fe3c68e6b206e86b574393e29ca5`. Its plugin contains no composer
prefill or prompt-suggestion field. OpenAI's documented plugin manifest exposes
static starter prompts, while Suggested prompts and gray composer rendering
remain host-owned.

Evidence Lane now owns the portable part: every candidate Exit Slip and every
HIL/State Travel MCP result contains the same neutral
`evidence-lane.next-action.v1` contract. It names the valid next command,
selects no decision, sets `auto_submit: false`, and requires
`stop_and_wait: true`. No Stop hook is installed. A fresh installed Codex task
and fresh connected ChatGPT chat are the only valid live rendering test. If
the host does not render gray text, the visible Exit Slip/MCP suggestion is the
fallback.

## Implemented candidate

- `/evi-00-state-travel` is first.
- `/evi-01-boot` atomically verifies Boot and locked ENV/UOP Flash.
- Seventeen source-intake commands sit between Boot and Build PV Entry and
  route all eighteen canonical lanes.
- `/evi-mode` is one separate anytime sidecar and always includes Chat
  Lineage.
- Task completion confirms the final source and automatically Refreshes and
  seals the exit candidate.
- Fuse requires exact `APPROVE`; State Travel then requires a genuinely fresh
  Codex task or ChatGPT chat and verifies Boot/Flash, pointer generation,
  manifest, and package seals.
- A plain fresh-session bind cannot bypass State Travel.
- Rollback remains pointer-only, and `/evi-exit-boot` is the only explicit
  governed-session deactivation.
- Every one of the 31 user-facing commands has a matching native skill.

## Validation

- final full suite: 66 passed in 381.24 seconds;
- targeted State Travel and Mode review: 8 passed;
- Ruff 0.16.0 lint and format: pass across 57 files;
- mypy 2.3.0: 38 source files, zero issues;
- Bandit 1.9.4: zero findings;
- `pip check`: no broken requirements;
- pip-audit 2.10.1: no known dependency vulnerabilities;
- wheel and sdist build: pass;
- isolated wheel install and bundled schema/Flash resource readback: pass;
- Markdown links: 13 files, zero missing;
- legacy `/pv-*` and `/ev` user-command scan: pass;
- `git diff --check`: pass.

The staged implementation before this evidence directory is tree
`6227d6e1df2ef1e69b625d6258a4a698b2426180`.

## Preserved governance state

The accepted pointer remains null at generation 0. Both prior PV1 candidates
remain preserved and unaccepted:

- `PV1_CANDIDATE__RUN_01KYG2MA34M7V5C9RDMA3T51DJ`
- `PV1_CANDIDATE__RUN_01KYR17PNDNXXJQQ288T3CG18H`

All 19 linear Deltas remain `QUEUED`. Implementation overlap introduced by
direct user steers does not activate, accept, remove, skip, or complete any
queued Delta.

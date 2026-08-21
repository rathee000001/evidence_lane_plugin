# Capability routing correction and Plan projection repair

This additive correction follows implementation v31. It does not replace or
reorder any linear Delta, accept a candidate, Fuse a PV, move the accepted
pointer, merge `main`, deploy Vercel, or infer HIL success.

## Runtime doctor correction

The exact-Git 0.5.2 bootstrap exposed a contradiction in the runtime doctor:
Google Drive was reported as globally required at plugin installation even
though the governed host matrix uses local storage for persistent local Codex
and reserves Drive for an explicitly selected ephemeral/public persistence
route.

Version 0.5.3 corrects the report to:

- `CAPABILITY_ROUTED_NOT_GLOBALLY_REQUIRED`;
- `HOST_OAUTH_OPTIONAL_UNTIL_PERSISTENCE_ROUTE_SELECTS_DRIVE`.

The normal host OAuth connector remains the only connector onboarding route.
No connector token is exposed to the MCP, no Google Drive connection is
claimed, and direct server-side Drive persistence remains unconfigured.

## Plan lane verification

The canonical append-only backlog contained all 21 Delta events, but the live
derived Plan SQLite projection was one event behind and correctly reported
`STALE` at 20/21. The canonical backlog was not rewritten. The derived
projection was deterministically rebuilt from that authority and now reports:

- status `PASS`;
- 21 Delta events and 21 `QUEUED` tasks;
- seven immutable plans;
- one hash-chained Planning-mode event;
- SQLite integrity `ok` with zero foreign-key errors;
- `canonical_plan_sector_mutated: false`.

Planning-mode detection remains a privacy-minimized runtime append. It does not
invent a Delta. Delta addition and the universal transitions `ACTIVE`, `DONE`,
`ACCEPTED`, `REJECTED`, `DROPPED`, `SUPERSEDED`, `FAILED`, and `ROLLED_BACK`
remain explicit append-only lifecycle events.

## Validation

- complete suite: 71 passed in 398.88 seconds;
- focused correction suite: 4 passed in 4.71 seconds;
- official OpenAI plugin validator: pass;
- Ruff lint and format: pass;
- mypy: pass across 39 source files;
- Bandit: pass;
- `pip check`: no broken requirements;
- pip-audit: no known dependency vulnerabilities;
- root and plugin wheel/sdist builds: pass;
- isolated 0.5.3 wheel install and version/schema/locked-Flash resource
  readback: pass;
- `git diff --check`: pass.

The staged correction before this evidence directory is tree
`95bbed9a5fdab16ccce11370297bee14feca6573`.

## Preserved HIL boundary

The accepted pointer remains null at generation 0. Both earlier candidates
remain preserved and unaccepted:

- `PV1_CANDIDATE__RUN_01KYG2MA34M7V5C9RDMA3T51DJ`;
- `PV1_CANDIDATE__RUN_01KYR17PNDNXXJQQ288T3CG18H`.

All 21 Deltas remain `QUEUED`, including
`EL-PLAN-LANE-AUTOMATIC-LIFECYCLE-DELTA-020` and
`EL-GIT-HOST-PICKUP-BOOT-HIL-DELTA-021`.

The next authorized work is one non-force feature-branch commit/push, exact
0.5.3 Git installation in Codex, refresh of the existing private ChatGPT Secure
MCP Tunnel, branch-authority pickup, and one fresh unaccepted candidate. The
workflow must then stop at the six-way `/evi-80-hil`; only a later exact
case-sensitive `APPROVE` may Fuse.

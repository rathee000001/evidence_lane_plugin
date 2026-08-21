# Evidence Lane 0.8.3 interrupted-exit recovery evidence

Recorded: 2026-08-01T23:58:28Z

## Observed failure

The exact installed 0.8.2 runtime entered `EXIT_BUILDING` and was terminated by
the MCP client after 900 seconds while the real 18-lane candidate build was
still running. No candidate was sealed, the 43 backlog tasks remained `QUEUED`,
and the accepted pointer remained PV1 generation 1.

## Bounded correction

- Added the canonical `RECOVER_INTERRUPTED_EXIT` self-transition.
- Retry is legal only while `candidate_id` is absent.
- The retry records a visible, private-reasoning-free ChatLineage receipt.
- Recovery records `pointer_moved=false` and `acceptance_inferred=false`.
- A sealed candidate makes recovery fail closed.
- The installed MCP long-tool timeout is now 3600 seconds.

## Verification

- Full suite: `106 passed in 563.26s`.
- Targeted recovery plus atomic batch completion: `2 passed in 38.05s`.
- Ruff: PASS.
- mypy: PASS across 50 source files.
- Plugin validator: PASS.
- `git diff --check`: PASS.

This correction authorizes no Fuse, accepted-pointer movement, main merge, or
State Travel. A later candidate build must still stop at the unaccepted HIL.

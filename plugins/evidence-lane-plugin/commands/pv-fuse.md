---
description: Promote and directly enter one candidate with exact APPROVE
argument-hint: <project_id> <session_id> APPROVE
---

# Evidence Lane PV Fuse

Require the final argument to be the exact case-sensitive token `APPROVE`.
Otherwise do not call a mutating tool.

Call `pv_fuse` once. Report candidate and accepted seals, pointer generation
before/after, byte-preserving promotion, and the automatic direct handoff entry
receipt. Prove that the session's `entry_pv`, entry manifest/package hashes,
freshness, and next candidate ordinal now come from the newly accepted PV.

PV Fuse never rebuilds or remakes the accepted PV and never authorizes a remote
Git push.

---
description: Fuse one exit candidate only with exact APPROVE
argument-hint: <project_id> <session_id> APPROVE
---

# /evi-90-pv-fuse

Require the exact case-sensitive final argument `APPROVE`. Call `pv_fuse`
once. Promotion preserves candidate bytes, moves the pointer by
compare-and-swap, and directly enters the accepted PV without rebuilding.
Fuse never authorizes remote Git.

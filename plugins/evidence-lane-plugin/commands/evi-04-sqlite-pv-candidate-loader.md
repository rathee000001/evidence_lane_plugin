---
description: Load and verify one SQLite-backed PV candidate
argument-hint: <project_id> <candidate-or-accepted-PV>
---

# /evi-04-sqlite-pv-candidate-loader

Resolve the internal `brain_loader` lane through `lane_catalog`, but present its
user-facing name only as **SQLite PV Candidate Loader**. Verify package,
manifest, SQLite integrity, foreign keys, MMD/DOT/tool identities, seals, and
candidate-versus-accepted status. This is a bounded loader/read path; it never
promotes, rewrites, or invents a PV.

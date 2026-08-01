---
description: Move only the accepted pointer through immutable accepted history
argument-hint: [project_id session_id optional-PV]
---

# /evi-rollback

Call `pv_rollback`. Resolve a bare rollback to the session entry PV, require
pointer-generation CAS, preserve every source byte, candidate, receipt, and
accepted package, and record freshness. A pending unaccepted candidate is
preserved. Rollback never accepts or rebuilds anything.

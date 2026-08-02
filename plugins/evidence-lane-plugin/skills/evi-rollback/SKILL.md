---
name: evi-rollback
description: Evidence Lane pointer-only rollback across immutable accepted project versions.
---

# Evidence Lane Rollback

Call `pv_rollback`. Resolve a bare rollback to the session entry PV, require
pointer-generation CAS, preserve every source byte, candidate, receipt, and
accepted package, and record freshness. A pending unaccepted candidate is
preserved. Rollback never accepts or rebuilds anything.

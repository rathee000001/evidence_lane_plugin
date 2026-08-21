---
name: evi-refresh
description: Evidence Lane changed-section incremental Refresh that seals an unaccepted candidate and stops at HIL.
---

# Evidence Lane Refresh

Require the host-specific source confirmation, then call `pv_refresh` or
`task_complete_and_refresh`. Reuse content-addressed chunks and the single Git
history index, rebuild only changed sections/lanes, preserve tombstones and
history, validate all SQLite/FK/FTS/MMD/DOT artifacts, and create a fresh
unaccepted candidate. Return host-specific output links/handoff and stop at
the six-way HIL without Fuse.

---
description: Read the SQLite-brain inspection lane
argument-hint: <project_id> [query-or-path]
---

# Evidence Lane SQLite brain

Resolve `sqlite_brain` through `lane_catalog`, call `lane_status`, then search
or fetch the immutable read-only schema, foreign keys, integrity result,
compatibility evidence, exact database hash, and parser state. Never execute
source SQL or write back to the database.

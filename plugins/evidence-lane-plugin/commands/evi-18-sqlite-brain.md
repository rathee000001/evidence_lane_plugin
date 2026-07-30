---
description: Read the SQLite Brain inspection source lane
argument-hint: <project_id> [query-or-path]
---

# /evi-18-sqlite-brain

Resolve canonical lane `sqlite_brain` through `lane_catalog`, call
`lane_status`, then use bounded `lane_search` or `lane_fetch`. Read immutable
schema, foreign keys, integrity, compatibility, exact database hashes, and
parser state. Never execute source SQL or write back to the database.

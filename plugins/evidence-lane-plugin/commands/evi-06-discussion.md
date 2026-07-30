---
description: Read the Discussion source lane
argument-hint: <project_id> [query-or-path]
---

# /evi-06-discussion

Resolve canonical lane `discussion` through `lane_catalog`, call `lane_status`,
then use bounded `lane_search` or `lane_fetch`. Report decisions, deltas, next
actions, exact source hashes, parser state, and freshness without mutation.

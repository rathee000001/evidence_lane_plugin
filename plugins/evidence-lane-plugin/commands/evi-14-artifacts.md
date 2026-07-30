---
description: Read the Artifacts source lane
argument-hint: <project_id> [query-or-path]
---

# /evi-14-artifacts

Resolve canonical lane `artifacts` through `lane_catalog`, call `lane_status`,
then use bounded `lane_search` or `lane_fetch`. Report exact artifact metadata,
relations, review flags, hashes, and freshness without promoting outputs into
accepted authority.

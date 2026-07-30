---
description: Read or route one canonical Evidence Lane sector
argument-hint: <project_id> <lane|route> [query-or-path-or-source=lane]
---

# /evi-70-lane-route

Call `lane_catalog` first. For reads, call `lane_status` and then bounded
`lane_search` or `lane_fetch`. For an explicit route grant, call
`lane_configure_routes`. Cover every canonical sector without guessing aliases
or executing arbitrary source SQL.

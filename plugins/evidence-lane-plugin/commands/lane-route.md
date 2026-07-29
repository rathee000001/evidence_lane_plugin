---
description: Grant exact sources to canonical lanes for one candidate
argument-hint: <project_id> <source-path=lane> [...]
---

# Evidence Lane source routing grant

Call `lane_catalog`, resolve every target from the one registry, and call
`lane_configure_routes` once for `$ARGUMENTS`. The grant is named, applies to
one candidate build, enters lineage, and relocks after consumption. Accepted
routes persist through later Refreshes. Do not edit source, build, or approve.

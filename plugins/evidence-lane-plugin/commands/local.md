---
description: Intake or read one explicit local-code repository
argument-hint: <project_id> <local-git-path> <owner> <name> <branch> [query-or-path]
---

# Evidence Lane local-code intake

Require successful `/EV` verification in this host task.

- If the project is absent, call `pv_enroll_project` with the exact existing
  local Git path and branch. Adoption never copies, cleans, resets, commits, or
  pushes the repository. Then return to `/EV` to boot.
- If the project is registered, require the path and branch to match its
  authority, then use `lane_status`, `lane_search`, or `lane_fetch` against the
  canonical `local_code` lane.
- Never silently substitute `github_code`, overwrite lineage, or treat live
  working-tree evidence as accepted PV truth.

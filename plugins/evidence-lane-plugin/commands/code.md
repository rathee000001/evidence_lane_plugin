---
description: Read the GitHub-code or local-code lane
argument-hint: <project_id> <github_code|local_code> [query-or-path]
---

# Evidence Lane code sector

Call `lane_catalog`, then require exactly one code mode from `$ARGUMENTS`.
Use `lane_status`, then `lane_search` or `lane_fetch` against that canonical
mode. `/code` is the only deliberate shared alias; never guess GitHub versus
local code. Reads default to accepted truth and label candidates unaccepted.

---
description: Build the initial PV entry candidate or load the accepted entry
argument-hint: <project_id> <session_id>
---

# /evi-30-build-pv-entry

If no accepted PV exists, call `pv_build_initial` once and stop at its HIL. If
an accepted PV exists, load that immutable accepted entry directly; never
rebuild it. Report candidate/entry identity, all eighteen lane health results,
seals, warnings, pointer generation, and the exact next HIL action.
Render the returned `suggested_next_prompt` visibly before stopping. It is a
neutral prompt aid, not a decision; do not submit it or claim the MCP wrote the
host composer.

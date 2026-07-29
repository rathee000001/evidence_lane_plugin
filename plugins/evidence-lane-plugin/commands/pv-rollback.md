---
description: Travel the accepted pointer to immutable PV or prompt-entry history
argument-hint: <project_id> [PVn | PROMPT <index> | TURN <id>]
---

# Evidence Lane rollback

Call `pv_rollback` once for `$ARGUMENTS`. If no PV is supplied, omit
`rollback_to`; the engine must resolve the current prompt-entry PV and fall back
to the governed session-entry PV.

Show accepted history, pointer/generation before and after, exact target seals,
prompt-index resolution receipt when used, freshness, preserved candidates, and
the monotonic next candidate ordinal.
Never restore live source, rebuild a PV, or infer approval.

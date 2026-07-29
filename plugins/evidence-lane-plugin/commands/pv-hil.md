---
description: Record one exact six-option human HIL decision
argument-hint: APPROVE | APPROVE_WITH_DELTA: <correction> | MORE_RESEARCH: <question> | ROLLBACK[: PVn] | REJECT: <reason> | FAIL: <gate>
---

# Evidence Lane HIL decision

Record `$ARGUMENTS` verbatim. For exact `APPROVE`, call `pv_fuse` with the
case-sensitive approval token so promotion and direct handoff are one
user-facing action. For every other outcome, call exactly one `hil_decide`.

Only `APPROVE` promotes the pending candidate. A rollback target may be any
sealed accepted PV, `PROMPT <index>`, or `TURN <id>`; bare `ROLLBACK` uses the
current prompt entry PV and falls back to the governed session entry PV.
Report pointer/generation before and after, target seal, preserved candidate,
freshness, and receipt. Never normalize an unstated target or infer approval.

---
description: Enter one accepted sealed handoff in a genuinely fresh host task
argument-hint: [project_id session_id handoff_id]
---

# /evi-state-travel

Run only when `pv_status` proves an accepted PV and one prepared handoff. In a
genuinely fresh host task or chat, atomically verify runtime doctor, locked
ENV15/UOP15 Flash, new host ID, pointer generation, manifest, package seals,
and freshness through `pv_state_travel_resume`. Stop at
`WAITING_FOR_NEXT_USER_COMMAND`. Never build, Fuse, or infer approval here.

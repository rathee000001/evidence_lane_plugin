---
description: Atomically Boot and verify locked ENV/UOP Flash for one persistent Evidence Lane session
argument-hint: [project_id]
---

# /evi-01-boot

This is the only user-facing Boot/Flash command. In one atomic flow, call
`runtime_doctor`, verify installed manifest/runtime parity and the exact locked
ENV15/UOP15 authority, then call `session_boot` or `session_resume`. Do not
offer or require a separate Flash command.

Report the installation state, authority version and digest, immutable SQLite
integrity, Flash receipt state, store, source authority, accepted entry,
pointer generation, candidates, pending HIL, freshness, backlog, and the next
ordered command. A failed installation, Flash, project, pointer, or session
gate stops the whole Boot.

On success, visibly render the returned `ordered_source_intake_commands` in
their canonical order and the returned `suggested_next_prompt`. Boot may
report the stored entry action, but it must not silently execute Build PV
Entry, classify a task, or choose an intake lane for the user.

Once Boot succeeds, the governed project session remains active and resumable
across host tasks or chats until the user explicitly invokes
`/evi-exit-boot`. Exit Boot closes only the governed session; the plugin remains
installed and the verified Flash remains installation-scoped until plugin
removal. Never infer HIL approval.

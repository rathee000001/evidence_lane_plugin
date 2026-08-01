---
description: Atomically verify runtime, locked Flash, host, and durable storage
argument-hint: [project_id]
---

# /evi-boot

In one atomic flow call `runtime_doctor`, `session_flash_status`, then exactly
one of `session_boot` or `session_resume`. Boot/resume must finish with runtime
activation `ACTIVE`, locked Flash context attached, and visible prompt/response
capture enabled for the exact governed session. Detect Codex desktop, Codex CLI, or
ChatGPT; record local/durable/ephemeral storage capability. Prefer durable local
SQLite whenever it exists. A host without durable storage must have a
transactional runtime connector; Google Drive is optional mirror/fallback and
never primary. Fail closed if any prerequisite is missing. On success display
the six controls, the optional `/evi-plugin` administrative sidecar, and the
`/evi-source-intake` suggested prompt.

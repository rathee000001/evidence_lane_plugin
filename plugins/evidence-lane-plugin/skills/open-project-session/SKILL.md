---
name: open-project-session
description: "Verify the runtime and locked operating-policy package, then start or resume one explicitly selected project session. Use for project entry or client reconnection."
---

# Open project session

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read `client_context`, `runtime_doctor` and `session_flash_status` on the local owner route.
On a scoped HTTPS route, use `session_context` for Flash and engine observations;
its unique connection identity is distinct from the parent grant and reported
host session label. Verify the configured durable server after restart first.
Remote visible hooks require `capture_bind` in the owner grant, a private
`hook_binding_directory` in the remote client configuration, and the same
`EVIDENCE_LANE_REMOTE_CONFIG` selection in the host hook environment. Boot/Resume
writes that local binding after the exact server transition; hooks only use the
existing connection. They cannot Boot, reconnect, or establish native identity.
If a project must be registered, use the Storage workflow with the exact
user-selected source and external state roots, then `project_select` with
explicit permissions. Read `session_status` and its current Root PV digest.
Call `session_boot` when no session is active; otherwise call `session_resume`
with the exact session ID, generation and event digest. Use an actually
reported host session label and state its attribution accurately. Never turn
a client claim into native task attestation.

A live source client requires an accepted State Travel transfer before another
client resumes it. After disconnection, resume still requires the exact saved
head and current project grant. Read back `session_status` and `session_context`, then the full
current Plan through pinned `plan_read` pages. Distinguish a bound capture
channel from observed native hook execution; an untrusted/missing hook remains
unavailable. Do not retry a failed Boot as another lifecycle route.

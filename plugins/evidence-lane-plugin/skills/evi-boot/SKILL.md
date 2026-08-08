---
name: evi-boot
description: Evidence Lane atomic runtime doctor, ENV15/UOP15 Flash, host detection, and durable-storage Boot or resume.
---

# Evidence Lane Boot

In one atomic flow call `runtime_doctor`, `session_flash_status`, then exactly
one of `session_boot` or `session_resume`. Never duplicate an active governed
session. Boot/resume must finish with runtime activation `ACTIVE`, locked Flash
context attached, and visible prompt/response capture enabled for the exact
governed session.

On a ChatGPT Pro connection that exposes the governed read profile, use the
read-safe branch instead: call `runtime_doctor`, `session_flash_status`,
`runtime_activation_status`, and `pv_status`, then render the runtime panel.
This verifies the already running accepted runtime for reading; it does not
call `session_boot` or `session_resume`, create a session, move a pointer, or
claim a lifecycle write. Require an existing `ACTIVE` runtime and matching
locked Flash hashes. Otherwise fail closed as
`CHATGPT_PRO_READ_RUNTIME_NOT_ACTIVE` and direct the operator to the governed
runtime bootstrap. Label a successful result `CHATGPT_PRO_READ_ATTACH`.

Detect Codex desktop, Codex CLI, or ChatGPT and record local, durable, or
ephemeral storage capability. Prefer durable local SQLite whenever it exists.
A host without durable storage must have a transactional runtime connector;
Google Drive is only an optional sealed-artifact carrier for an ephemeral Codex
VM and never primary. Fail closed if any prerequisite is missing. On success
display the six controls, the optional `/evi-plugin` administrative sidecar,
and the `/evi-source-intake` suggested prompt. On ChatGPT Pro, show the same
six controls with their exact host availability; never present an unavailable
write control as completed.

Every Boot and Resume must emit and validate one
`evidence-lane.runtime-continuity.v1` receipt. It binds the host/session, exact
accepted pointer, primary runtime route, and locked ENV/UOP Flash hashes. Entry
and Exit Slips carry that same hashed reference and route contract, never the
ENV/UOP bytes. ChatGPT must report `FORBIDDEN_FOR_CHATGPT_RUNTIME` for Google
Drive. A Codex ephemeral VM may report Drive only as a sealed Entry/Exit
carrier while a transactional connector owns live state. No continuity receipt
may move a pointer, create a candidate, or infer HIL approval.

An already accepted PV is an immutable authority and is revalidated for exact
bytes, manifest/package hashes, pointer identity, and database integrity during
Boot or Resume. Do not retroactively require it to satisfy topology or other
promotability rules introduced after acceptance. Report that compatibility
state explicitly; every successor candidate must still pass all current rules
before it can be promoted.

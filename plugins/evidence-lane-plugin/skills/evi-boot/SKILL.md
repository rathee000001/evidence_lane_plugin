---
name: evi-boot
description: Evidence Lane atomic runtime doctor, ENV15/UOP15 Flash, host detection, and durable-storage Boot or resume.
---

# Evidence Lane Boot

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

In one atomic flow call `runtime_doctor`, `session_flash_status`, then exactly
one of `session_boot` or `session_resume`. Never duplicate an active governed
session. Boot/resume must finish with runtime activation `ACTIVE`, locked Flash
context attached, and visible prompt/response capture enabled for the exact
governed session.
Verify that result with `runtime_activation_status`. Ordinary Boot or Resume
stops with the activation receipt and does not call either project/runtime
renderer.

If the governed session has a canonical task panel, every Boot or Resume must
make exact panel reactivation the host's first post-verification action. This
also applies after a token-driven continuation, stalled Goal, context
compaction, browser or Codex restart, session continuation, or State Travel
destination entry. Re-project all rows before source inspection, mutation,
testing, Git activity, or another lifecycle call; keep exactly one row active;
preserve order and every completed and pending description unabridged; retain
the panel through every pause and HIL; and drop it only after the physically
final six-way HIL decision and all decision-dependent work are complete.

Detect Codex desktop, Codex CLI, or Codex VM and record local, durable, or
ephemeral storage capability. Prefer durable local SQLite whenever it exists.
A host without durable storage must have a transactional runtime connector;
Google Drive is only an optional sealed-artifact carrier for an ephemeral Codex
VM and never primary. Fail closed if any prerequisite is missing. On success
display the six controls, the optional `/evi-plugin` administrative sidecar,
and the `/evi-source-intake` suggested prompt; never present an unavailable
action as completed.

Every Boot and Resume must emit and validate one
`evidence-lane.runtime-continuity.v1` receipt. It binds the host/session, exact
accepted pointer, primary runtime route, and locked ENV/UOP Flash hashes. Entry
and Exit Slips carry that same hashed reference and route contract, never the
ENV/UOP bytes. A Codex ephemeral VM may report Drive only as a sealed Entry/Exit
carrier while a transactional connector owns live state. No continuity receipt
may move a pointer, create a candidate, or infer HIL approval.

An already accepted PV is an immutable authority and is revalidated for exact
bytes, manifest/package hashes, pointer identity, and database integrity during
Boot or Resume. Do not retroactively require it to satisfy topology or other
promotability rules introduced after acceptance. Report that compatibility
state explicitly; every successor candidate must still pass all current rules
before it can be promoted.

`PROJECT_RUNTIME_RENDER_THREE_TRIGGER_LAW` is permanent. The lifecycle owner may
call `render_runtime_panel` and `render_project_panel` only in exactly three
cases: once per tool during a passed State Travel entry; once per tool while
presenting the physically final PV HIL; or after an explicit user request for
the renderer. Ordinary Boot/Resume, owner discovery, verification, Plan/Goal
continuation, restart, reconnect, rehydration, and status readback never call
either renderer. A missing renderer receipt never authorizes an implicit retry
or fallback. These tools remain read-only and use canonical bare names only; a
host display namespace is never part of the Evidence Lane tool contract.

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-boot`. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.

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

Project selection precedes ordinary Boot. In the initial workflow, call
`project_register` only when the chosen `project_id` is unregistered and
require three distinct identities: the task's user-selected workspace or
repository, the user-selected external Project/PV authority root, and the
hidden Codex plugin runtime-control root. Then collect the brief and enter
native Plan mode; EVI Plan persists the first canonical Plan before any Source
Intake/PV0 work. After the user explicitly accepts the Plan, host hooks bind
the Goal and fixed Step Task List, and only then may Source Intake plus initial
Build establish PV0 without HIL. A new ordinary task may choose the initial
workflow and register a new project, even in an existing workspace. A State
Travel destination is never an initial workflow: it reuses the carried project
registration and must not ask for, infer, relocate, or create a Project/PV root.

For a direct/forced same-worktree State Travel destination, the governed
session is necessarily already active. Select `session_resume`, bind its
`host_session_id` to the exact new destination task UUID, and never select
`session_boot`. Then call `runtime_activation_status` before the one-shot
six-field direct route. Status-only Boot checks do not attach the destination.
This exact choice is owned by
`DIRECT_STATE_TRAVEL_DESTINATION_RESUME_ROUTE_LAW` in the State Travel skill.
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

At the same activation boundary, classify the exact model, model-specific
Effort, Speed, and model-visible tool capability receipt. An optional derived
model-variant/submodel label may be preserved but is not a separate required
user setting. Model name
alone never proves compatibility. The current catalog distinguishes GPT-5.6
Sol/Terra/Luna, GPT-5.5, GPT-5.4, and GPT-5.4 Mini; every exact
model-plus-Effort profile requires representative installed-host proof before
governed writes. Low/Light or None stays read-only/bounded until a dedicated
evaluation proves the full lifecycle. The installed `5.3 Codex Spark` profile
is blocked because its plugin/right-panel action surface was absent in direct
host observation. ChatGPT Instant/chat models and the ChatGPT surface remain out of scope. Unknown or
historical profiles are conditional, never silently promoted to PASS. State
Travel must replay the exact already-qualified profile rather than select or
mutate a model setting.

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

`PROJECT_RUNTIME_RENDER_TWO_TRIGGER_LAW` is permanent. The lifecycle owner may
call `render_runtime_panel` and `render_project_panel` only while presenting
the physically final PV HIL or after an explicit user request for the renderer.
State Travel uses native receipts and never invokes either renderer. Ordinary
Boot/Resume, owner discovery, verification, Plan/Goal
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

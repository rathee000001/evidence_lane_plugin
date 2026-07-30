---
name: evidence-lane-code-lifecycle
description: Start with /evi-00-state-travel and govern one universal Evidence Lane project across fresh-window handoff, atomic Boot plus locked ENV/UOP Flash, seventeen source-intake commands, one Mode sidecar, Build PV Entry, bounded tasks, automatic exit-Refresh, exact-APPROVE Fuse, pointer-only Rollback, and explicit Exit Boot. Use when the user selects Evidence Lane, opens an /evi command, queues bounded tasks, builds or refreshes a PV, continues from accepted history, rolls back to accepted history, or prepares a separately authorized Git push.
---

# Evidence Lane universal lifecycle

Use the Evidence Lane tools as one linear state machine. The plugin and its
user-owned store persist across prompts; runtime context does not become PV
evidence.

## Hard boundaries

- Verify the locked ENV15/UOP15 flash outside every PV. Preserve all returned
  warnings, including an incomplete source packet.
- Use one user, one registered project, one accepted entry, one agent, and one
  active bounded task. A multi-task request becomes an ordered backlog, never
  parallel execution.
- Use `lifecycle_transition_law` as the only event/from/to law.
- Never call a candidate accepted. Only exact `APPROVE` promotes candidate
  bytes. Never infer HIL from wording, success, hashes, or tests.
- `ROLLBACK` moves only the accepted pointer to immutable accepted history.
  It accepts `PVn`, `PROMPT <index>`, or `TURN <id>`. A bare rollback resolves
  the current prompt entry and falls back to the governed session entry. It
  never deletes history, rewrites live source, promotes a candidate, or resets
  the monotonic next-PV ordinal.
- Never write remote Git without a separate prepared action and its exact
  one-use confirmation token.
- Record visible operational evidence only; exclude secrets and private model
  reasoning.
- `/evi-mode` is the one anytime Mode sidecar. It may classify ordered
  intersections from the locked ENV15 mode namespace and append only a
  privacy-minimized Chat Lineage receipt. It never changes lifecycle state,
  classifies a bounded task, creates a candidate, or moves a pointer.

## Entry and persistence

1. `/evi-00-state-travel` is the first visible control. With no prepared
   handoff, continue to Boot. After Fuse, require a fresh Codex task or ChatGPT
   chat, run the atomic Boot/Flash verification there, verify the accepted
   pointer and package seals, and stop in `WAITING_FOR_NEXT_USER_COMMAND`.
   Opening the host window is host-mediated and must remain fail-visible.
2. `/evi` presents and follows the remaining numeric top-down command stack.
   `/evi-01-boot` is the only user-facing Boot/Flash command. It atomically
   verifies installed manifest/runtime parity and the locked ENV/UOP authority
   before source intake; preserve every warning. The Flash remains valid until
   plugin removal. A successfully booted governed session stays resumable
   across host tasks or chats until the user explicitly invokes
   `/evi-exit-boot`.
3. Verify the required Google Drive dependency through the host connector's
   normal OAuth route. That connector token is never exposed to this MCP.
   Durable local servers use the local store and may use Drive as a verified
   mirror; explicitly ephemeral servers require the separately configured
   direct server-side Drive backend.
4. If the project is absent, begin source intake with exactly
   `/evi-02-git` and `/evi-03-local`.
   `pv_enroll_project` may clone one selected credential-free HTTPS branch or
   adopt one exact local Git path. `git_sync_selected` may later apply only a
   clean, path-bounded fast-forward. Neither action performs a remote write.
5. Call `session_boot`, or `session_resume` when the one governed session
   already exists. Bind the current SessionStart host ID for prompt indexing.
   Report store, session-entry PV, current accepted PV, pointer generation,
   seals, accepted history, freshness, pending candidate/HIL, and next ordinal.
6. Expose every source-intake command between Boot and Build PV Entry in this order:
   Git, Local, SQLite PV Candidate Loader, Chat Lineage, Discussion, Analysis,
   Plan, Docs, Data/Excel/CSV, PPT, PDF/OCR, Images/OCR, Artifacts, Custom,
   Research, Project Engulf, and SQLite Brain. Mode remains an internal
   compatibility lane reached only through `/evi-mode`, not source intake.
7. If no accepted PV exists, `/evi-30-build-pv-entry` calls
   `pv_build_initial` once. PV1 is the only full eighteen-lane build. Stop at
   HIL.
8. If an accepted PV exists, load it directly. Do not rebuild on entry.

At every step, keep `/evi-mode` available beside the lifecycle. Its
canonical lane set always includes `mode` and `chat_lineage`; return to the
exact prior lifecycle position after classification.

## One registry and eighteen lanes

Call `lane_catalog` before resolving a lane. It is the single authority for
canonical IDs, aliases, commands, parsers, schema contracts, FTS tables, and
mutation policies:

- `github_code` through `/evi-02-git`, or `local_code` through
  `/evi-03-local`
- `chat_lineage`, `discussion`, `analysis`, `plan`, `mode`, `docs`
- `data_excel`, `ppt`, `pdf_ocr`, `images_ocr`, `artifacts`, `custom`
- `brain_loader`, shown only as **SQLite PV Candidate Loader**, followed by
  `research`, `project_engulf`, and `sqlite_brain`

Every lane owns SQLite, authoritative MMD and DOT, tool identity, pointer
evidence, Refresh receipt, and manifest. Use `lane_status`, `lane_search`, and
`lane_fetch`. Retrieval may be FTS5/BM25, explicitly materialized TF-IDF, or
deterministic hybrid. Missing PDF/OCR/office tools are visible blockers; never
claim they ran.

## Task, Refresh, and HIL

1. For multiple bounded tasks, call `pv_plan_tasks`; read them with
   `pv_task_backlog`.
2. Call `task_classify` once. If it came from the backlog, pass its stable
   `backlog_task_id` and match the queued contract exactly.
3. Read accepted evidence first. After source mutation, accepted-PV results are
   entry-state evidence only; use live repository evidence for current truth.
4. Append visible task actions with `task_record_activity`.
5. Confirm the final host source state:
   - ChatGPT: `USER_APPLIED_AND_PULL_CONFIRMED`
   - Codex: `HOST_SANDBOX_FINAL_STATE_CONFIRMED`
6. Call `task_complete_and_refresh`. It records the exact host confirmation
   and performs Refresh as one automatic completion transition. Entry and exit
   slips are generated automatically inside the sealed candidate; Exit and
   Refresh are not user commands. PV2+ starts from the accepted authorities,
   byte-reuses
   unchanged lane SQLite/MMD/DOT/tool files, rebuilds only changed/new sources,
   and tombstones removals. A full fallback is valid only for a declared tool or
   schema contract change.
7. Report executable acceptance health separately from prose checks. Prose
   remains `PENDING_HUMAN_REVIEW`; source-changing checks block the candidate.
8. Stop for exactly one:
   - `APPROVE`
   - `APPROVE_WITH_DELTA` with one correction
   - `MORE_RESEARCH` with one question
   - `ROLLBACK` optionally naming any accepted PV
   - `REJECT` with reason
   - `FAIL` with the exact failed gate
9. `/evi-90-pv-fuse` requires exact case-sensitive `APPROVE` and calls
   `pv_fuse`. Promotion must preserve candidate bytes and seal a State Travel
   handoff without a rebuild or remake. `/evi-00-state-travel` then enters
   those accepted bytes only in a fresh verified host window. After Delta or
   research, classify only the exact stored follow-up;
   an unaccepted initial PV1 correction reseals another PV1 candidate without
   inventing an accepted parent. After rejection/failure, restore the exact
   source before `hil_return_to_accepted`, or close the session.
10. `/evi-exit-boot` is the only explicit governed-session deactivation. It
    closes the active session while preserving plugin installation, the
    installation-scoped Flash receipt, candidates, accepted PVs, ChatLineage,
    backlog, and pointer generation. It is not an automatic Entry/Exit Slip.

## Read and remote behavior

Default reads use current accepted truth and include live freshness. Explicit
candidate reads remain labeled `UNACCEPTED_CANDIDATE`. Use bounded fetches and
allowlisted queries; never execute arbitrary source SQL.

`/evi-99-pv-rollback` moves only the accepted pointer to immutable accepted
history. PV approval does not authorize a push. Only after an explicit user
request may you call `remote_git_prepare_push`, show the exact action, and then
execute with the separately supplied one-use token.

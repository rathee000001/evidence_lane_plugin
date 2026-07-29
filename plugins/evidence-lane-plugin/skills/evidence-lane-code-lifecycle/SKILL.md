---
name: evidence-lane-code-lifecycle
description: Start with /EV and govern one universal Evidence Lane project across explicit Git or local-code intake, eighteen SQLite/MMD/DOT sectors, persistent accepted-PV entry, PV Refresh, exact-APPROVE Fuse, six-way human HIL, indexed rollback, and reversible pointer-only state travel. Use when the user selects Evidence Lane, opens a lane command, queues bounded tasks, builds or refreshes a PV, continues from accepted history, rolls back to an accepted PV or prompt entry, or prepares a separately authorized Git push.
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

## Entry and persistence

1. `/EV` is first. Verify installed manifest/runtime parity, call
   `session_flash_status`, and preserve every warning. The flash remains valid
   until plugin removal.
2. Verify the required Google Drive dependency through the host connector's
   normal OAuth route. That connector token is never exposed to this MCP.
   Durable local servers use the local store and may use Drive as a verified
   mirror; explicitly ephemeral servers require the separately configured
   direct server-side Drive backend.
3. If the project is absent, reveal exactly `/git` and `/local`.
   `pv_enroll_project` may clone one selected credential-free HTTPS branch or
   adopt one exact local Git path. `git_sync_selected` may later apply only a
   clean, path-bounded fast-forward. Neither action performs a remote write.
4. Call `session_boot`, or `session_resume` when the one governed session
   already exists. Bind the current SessionStart host ID for prompt indexing.
   Report store, session-entry PV, current accepted PV, pointer generation,
   seals, accepted history, freshness, pending candidate/HIL, and next ordinal.
5. If no accepted PV exists, call `pv_build_initial` once. PV1 is the only full
   eighteen-lane build. Stop at HIL.
6. If an accepted PV exists, load it directly. Do not rebuild on entry.

## One registry and eighteen lanes

Call `lane_catalog` before resolving a lane. It is the single authority for
canonical IDs, aliases, commands, parsers, schema contracts, FTS tables, and
mutation policies:

- `github_code` through `/git`, or `local_code` through `/local`; `/code`
  remains a compatibility read alias and must still name one exact mode
- `chat_lineage`, `discussion`, `analysis`, `plan`, `mode`, `docs`
- `data_excel`, `ppt`, `pdf_ocr`, `images_ocr`, `artifacts`, `custom`
- `brain_loader`, `research`, `project_engulf`, `sqlite_brain`

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
6. Call `pv_refresh`. Entry and exit slips are generated automatically inside
   the sealed candidate; they are not user commands. PV2+ starts from the
   accepted authorities, byte-reuses
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
9. For approval, require exact case-sensitive `APPROVE` and call `pv_fuse`.
   Promotion and direct accepted-PV handoff must complete without a rebuild or
   remake. After Delta or research, classify only the exact stored follow-up;
   an unaccepted initial PV1 correction reseals another PV1 candidate without
   inventing an accepted parent. After rejection/failure, restore the exact
   source before `hil_return_to_accepted`, or close the session.

## Read and remote behavior

Default reads use current accepted truth and include live freshness. Explicit
candidate reads remain labeled `UNACCEPTED_CANDIDATE`. Use bounded fetches and
allowlisted queries; never execute arbitrary source SQL.

PV approval does not authorize a push. Only after an explicit user request may
you call `remote_git_prepare_push`, show the exact action, and then execute with
the separately supplied one-use token.

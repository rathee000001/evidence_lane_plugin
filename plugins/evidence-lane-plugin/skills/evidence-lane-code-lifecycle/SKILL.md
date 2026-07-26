---
name: evidence-lane-code-lifecycle
description: Govern one Git/code task through Evidence Lane PV Entry, visible ChatLineage, deterministic PV Exit, and an explicit five-way human HIL decision. Use when the user selects Evidence Lane, asks to build or refresh a code PV, continue from an accepted PV, query SQLite project state, or prepare a separately authorized Git push.
---

# Evidence Lane code lifecycle

Use the Evidence Lane MCP tools as one linear state machine. The plugin remains
installed until the user removes it, but every work session still begins with an
explicit governed session boot.

## Hard boundaries

- Treat the verified ENV15/UOP15 authority as installation-scoped, read-only,
  immutable, and outside every PV. Never describe the incomplete source packet
  as intact.
- Use one user, project, accepted entry PV, agent, sandbox/source, and bounded task.
- Never spawn parallel agents for the governed task.
- Never treat a prompt, successful build, valid hash, or candidate as HIL approval.
- Only `APPROVE` advances the accepted pointer.
- Never place session boot context or user/operator setup data inside a PV.
- Never upload a source clone, dependency tree, cache, browser profile, or secret.
- Never push Git remotely without a separately prepared action and the exact
  one-use confirmation token.
- Do not expose private model reasoning. Record only visible operational evidence.

## Linear workflow

1. Call `runtime_doctor`.
2. Call `session_flash_status`. Require the exact authority version and digest,
   valid ENV/UOP MMD locks, SQLite integrity, and the visible
   `SOURCE_PACKET_PARTIAL_INTEGRITY` warning.
3. If needed, call `project_register` with the exact repository owner, name, and
   authorized branches.
4. Call `session_boot`.
   - The first boot creates the installation-scoped flash receipt.
   - Later boots with the same authority digest reuse it.
   - A changed digest blocks pending an explicit authority migration.
   - Local Codex Desktop or durable local Codex CLI uses local immutable storage.
   - ChatGPT, public AI, Codex VM, or any ephemeral host requires configured
     user-owned Google Drive persistence.
5. If there is no accepted PV, call `pv_build_initial`, present the PV1 candidate,
   and stop for one exact `hil_decide`.
6. After PV1 is approved, call `task_classify` once.
7. Use `search`, `fetch`, `pv_summary`, and `pv_query` to recover accepted state.
8. Append visible prompts, tool activity, commands, file effects, tests, build
   outputs, Git diff, warnings, errors, usage, and final response with
   `task_record_activity`.
   - After the first file creation, modification, or deletion, accepted-PV
     queries are entry-state evidence only.
   - Use exact repository reads and Git diff for the current post-edit source.
9. Before Refresh:
   - ChatGPT requires the user to apply its code Delta, commit as they choose,
     pull the resulting source into the governed repository, then call
     `task_confirm_source_update` with
     `USER_APPLIED_AND_PULL_CONFIRMED`.
   - Codex hosts confirm the bounded final sandbox/source state with
     `HOST_SANDBOX_FINAL_STATE_CONFIRMED`.
10. Call `pv_refresh`. It creates the next candidate and never promotes it.
11. Present exactly one HIL decision:
    - `APPROVE`
    - `APPROVE_WITH_DELTA` with one bounded correction
    - `MORE_RESEARCH` with one bounded question
    - `REJECT` with reason
    - `FAIL` with the exact failed gate
12. After `APPROVE`, call `pv_begin_next_turn` to prove the next entry and next
    candidate number.
13. After `APPROVE_WITH_DELTA` or `MORE_RESEARCH`, classify only the exact
    follow-up class and outcome stored by the HIL decision. Do not broaden it.
14. After `REJECT` or `FAIL`, either:
    - restore the exact accepted source and call `hil_return_to_accepted`; or
    - call `session_close`.
    `hil_return_to_accepted` must not move the pointer and is impossible when no
    accepted PV exists.
15. Call `session_close` when the governed line ends.

## Read behavior

Use `search` first for discovery and `fetch` for exact evidence. File references
use `file:<repository-relative-path>`; chunk references use `chunk:<numeric-id>`;
symbol references use `symbol:<numeric-id>`. Use bounded line windows for large
files and `pv_query` with `imports` for dependency edges. Default reads use the
current accepted PV. If a candidate is explicitly reviewed, preserve the
`UNACCEPTED_CANDIDATE` label and never describe it as accepted truth. Use
`pv_diff` only between validated accepted or preserved candidate PVs.

## Remote Git behavior

Approval of a PV does not authorize a remote Git write. Only after the user
explicitly requests a push may you call `remote_git_prepare_push`. Show the exact
remote, local ref, remote branch, accepted PV, and pointer generation. Execute
only after the user separately supplies the returned exact confirmation token.

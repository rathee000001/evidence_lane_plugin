---
name: evidence-lane-code-lifecycle
description: Govern one Git/code task through Evidence Lane PV Entry, visible ChatLineage, deterministic PV Exit, and an explicit five-way human HIL decision. Use when the user selects Evidence Lane, asks to build or refresh a code PV, continue from an accepted PV, query SQLite project state, or prepare a separately authorized Git push.
---

# Evidence Lane code lifecycle

Use the Evidence Lane MCP tools as one linear state machine. The plugin remains
installed until the user removes it, but every work session still begins with an
explicit governed session boot.

## Hard boundaries

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
2. If needed, call `project_register` with the exact repository owner, name, and
   authorized branches.
3. Call `session_boot`.
   - Local Codex Desktop or durable local Codex CLI uses local immutable storage.
   - ChatGPT, public AI, Codex VM, or any ephemeral host requires configured
     user-owned Google Drive persistence.
4. If there is no accepted PV, call `pv_build_initial`, present the PV1 candidate,
   and stop for one exact `hil_decide`.
5. After PV1 is approved, call `task_classify` once.
6. Use `search`, `fetch`, `pv_summary`, and `pv_query` to recover accepted state.
7. Append visible prompts, tool activity, commands, file effects, tests, build
   outputs, Git diff, warnings, errors, usage, and final response with
   `task_record_activity`.
8. Before Refresh:
   - ChatGPT requires the user to apply its code Delta, commit as they choose,
     pull the resulting source into the governed repository, then call
     `task_confirm_source_update` with
     `USER_APPLIED_AND_PULL_CONFIRMED`.
   - Codex hosts confirm the bounded final sandbox/source state with
     `HOST_SANDBOX_FINAL_STATE_CONFIRMED`.
9. Call `pv_refresh`. It creates the next candidate and never promotes it.
10. Present exactly one HIL decision:
    - `APPROVE`
    - `APPROVE_WITH_DELTA` with one bounded correction
    - `MORE_RESEARCH` with one bounded question
    - `REJECT` with reason
    - `FAIL` with the exact failed gate
11. After `APPROVE`, call `pv_begin_next_turn` to prove the next entry and next
    candidate number.
12. Call `session_close` when the governed line ends.

## Read behavior

Use `search` first for discovery and `fetch` for exact evidence. File references
use `file:<repository-relative-path>`; chunk references use `chunk:<numeric-id>`.
Use `pv_diff` only between validated accepted or preserved candidate PVs.

## Remote Git behavior

Approval of a PV does not authorize a remote Git write. Only after the user
explicitly requests a push may you call `remote_git_prepare_push`. Show the exact
remote, local ref, remote branch, accepted PV, and pointer generation. Execute
only after the user separately supplies the returned exact confirmation token.

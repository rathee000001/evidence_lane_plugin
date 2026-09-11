---
name: manage-project-plan
description: "Create, inspect or safely steer the current project plan while preserving completed history and replacing only affected work. Use for planning and semantic changes."
---

# Manage project plan

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read `plan_read` and preserve its revision and document digest.
Follow every page with that explicit revision; never combine pages from different
revisions. Use the exact task definition from its page when its operation,
dependency or acceptance contract is needed. For host synchronization, use
`plan_host_status`: it returns the complete ordered project rows and the exact
linked-file projection state, without a partial window. A current Plan read or
file hash does not independently prove that a particular Codex window rendered
the host list or changed the native Goal.

Use `plan_host_bind` only on the local administrative route and only with the
exact current task ID, Plan ID, absolute Codex `PLAN.md` path and observed file
hash. Never choose the newest Plan directory. The action verifies the path under
the current Codex plans root, records the binding in the project Plan database,
and atomically replaces that exact file with all current project rows. A changed
file is preserved and reported as a collision. Use `plan_host_sync` only to
reconcile the current pending full projection; it cannot select another path.

For an initial Plan, use `plan_create` only after checking that no canonical
Plan exists. Translate the user's outcome into the complete ordered task set:
stable IDs, dependencies, exact owning actions, profiles, path/tool budgets,
acceptance checks and stop conditions. Keep source registration/preparation
evidence distinct from sector execution. Prepared source tasks can be adopted
here; preparation itself does not execute them. Carry an attributed
`task_mode_binding` into each applicable task when explicit classification
provided one. Do not infer an execution grant from a recipe or mode query.

Read `validation_policy_read` for this project's additional CI checks. An
unconfigured project inherits no maintainer CI. When the user's work requires
project checks, use `validation_policy_set` with the observed policy revision
and digest, distinct check IDs, literal argv, a pinned executor, relative working
directory, change selectors and explicit time/output bounds. Prose and repository
manifests are evidence for a proposal, not executable permission. Save only the
authorized checks; use an empty check list to clear additional configuration.
Read back the exact saved revision. Saving a policy runs no checks, changes no
Plan task, and establishes no tool readiness. Owning profile verifiers remain
required. For each newly adopted task that should use the policy, add
`validation_policy` with its exact `policy_revision` and revision digest as
`policy_digest`, explicit `input_paths`, `exclude_paths` and file/byte bounds.
The observation paths must fit the task's path grant; its tools must cover every
context-matching check. The command's working directory also needs that grant.
Selection uses added, changed and deleted files measured across that task's
operation, together with its actual owning profile/action. Use
`run_all_checks=true` only for explicitly requested verification regardless of
file changes. Keep cache, output and dependency exclusions visible in the
contract: observations cover declared files, not the whole filesystem or all
dependencies. Never silently activate a changed policy on sealed work.
Read `delta_status` for its `validation` summary; `include_verification=true`
also includes output previews. A passed CI summary alone is not Delta completion.
Required check failure or changed observed inputs prevents verified task exit.

First distinguish an informational question from a semantic change to outcomes,
dependencies, acceptance, stop conditions or the execution route. A question
uses a bounded read and does not append a steer. For a semantic change, inspect
`steer_preview`, then use `steer_submit` with the exact captured source event,
current revision and affected tasks. Sparse capture requires resupplying the
same visible source text for its hash check. Inspect the affected job through
`delta_status` while it reaches a safe checkpoint. A stop uses that same attributed path;
it does not mean the worker has already stopped. Uncertain external effects
must be reconciled through Recovery before replacement.

Call `plan_refresh` with the exact revision, document digest, steer ID and the
complete replacement Plan. Preserve completed task definitions and evidence;
retain existing logical IDs for changed pending work. An idle selected task
needs its engine checkpoint; an admitted worker needs its own verified boundary.
The engine rejects stale or uncheckpointed replacement and late worker results.
After success read back every page of the new revision and inspect
`plan_host_status`. The project Plan database commit occurs before atomic
replacement of the exact linked `PLAN.md`. Preserve a contiguous completed
prefix, one active next row and a pending suffix; never publish a shortened
projection. Task-state transitions prepare the same full projection. If an
automatic file update fails, keep the project mutation, leave the projection
pending with its exact error, preserve the conflicting file, and use
`plan_host_sync` after resolving the binding or collision. An interrupted
response needs readback and exact request replay, not a newly invented Plan.

Native Goal creation or completion remains a separate explicit host action.
Plan completion, a Stop hook or a session closure cannot perform it.

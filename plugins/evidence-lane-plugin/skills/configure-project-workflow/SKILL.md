---
name: configure-project-workflow
description: "Derive or change the project workflow from explicitly selected sources and the requested outcome. Use during initial project classification or an explicit workflow change."
---

# Configure project workflow

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Select a complete registered source batch and the user's
requested outcome, then call `project_recipe`. Review the proposed retained
sectors, artifact meanings, paired ENV/UOP class policy and execution stages.
Code, data, documents, media, research, mixed and explicit custom types retain
their own validation strategy. Follow the selected lane checks; recipe defaults
never impose this plugin's maintainer CI on an unrelated project.
Inspect `project_validation_policy` for that project's exact saved revision or
`not_configured` status. It is configuration evidence, not executed checks or
permission to modify the Plan. Use the Plan workflow for an authorized policy change.
Optional explicit modes or a mode request synchronize classification in the
same proposal while keeping recipe and mode distinct. Treat an inferred project type
as a proposal; use an explicit type when the user selected one. A recipe does
not change the Plan, create a sector or verify current source bytes. Use the
Plan workflow to incorporate an authorized recipe change into current work.
Use `git_mode=AUTO` for an optional repository arm, `REQUIRED` when the outcome
depends on Git, or `DISABLED` for content-only work. AUTO uses bounded local Git
metadata when the executable is available; an unavailable or undetected repository
is reported explicitly. The exact selected source root must be the worktree root;
a parent repository is never adopted. Unborn and detached states retain their
specific prerequisites. Detection does not initialize, fetch, switch branches,
rewrite selected lanes or attest dirty bytes.
Use `lane_workflows` and `operation_contracts` for the current lane-owned intake,
read, refresh and change actions, tool routes, artifact meanings and verification
checks. These are candidate actions, not a queue or a dependency readiness claim.
Prepare the exact Sources selection through `source_prepare_tasks`, then adopt
the resulting task contracts through Plan. Select Git synchronization or remote
publication actions only when the user's outcome requires them and their current
contracts admit the operation. Non-Git content work needs no repository setup.
The result is complete or fails its explicit byte budget; select a smaller
registered batch or increase `max_result_bytes` within its schema limit when needed.

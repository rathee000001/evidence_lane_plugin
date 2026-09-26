<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Windows Studio

Studio is a visible, read-only window into the selected Evidence Lane project. It helps a person understand the state of the work without creating a second place to administer the project.

## What Studio is for

Use Studio to inspect:

- **Plan** — current revision, task order, completed history, one active task, and pending work.
- **Jobs** — admitted work, running state, completion, failure, and verification outcome.
- **Workers** — bounded operating-system processes used for tool work.
- **Evidence** — outputs, source trails, checks, and recorded relationships.
- **Toolchains** — declared tools, required packages, and measured readiness.
- **Connections** — project connector definitions, scope, status, and expiry.
- **Learning** — project-local procedural lessons and their provenance.
- **Compute** — CPU and eligible acceleration observations.
- **Diagnostics** — engine, client, project, installation, and failure state.

## What Studio does not do

Studio does not create projects, steer a Plan, start jobs, grant a connection, select compute, accept work, or perform recovery. Direct those changes through the appropriate Codex workflow.

## One persistent window

The supported launcher opens or restores one Studio window. The engine can continue in the background without a console. Closing the visible window does not automatically delete the shared runtime or project state.

Use the owned Desktop shortcut or the direct Windows Start-menu entry. A shortcut that no longer matches its installer ownership receipt is preserved and rejected rather than overwritten silently.

## Reading state honestly

Studio displays observations from the connected engine. A visible window is not proof that:

- the selected project is connected;
- source evidence is fresh;
- a listed tool is ready;
- a running job completed;
- a failed operation had no effects;
- the shown Plan matches another Codex task.

Check the project identity, revision, timestamps, status, and verification evidence shown in the relevant view.

## Visual redesign

The next coordinated Studio release is being designed in the accepted **Cosmic Observatory** language from the public website. Its concepts cover all nine views, living connections, detail inspectors, keyboard focus, empty/loading/error states, and reduced-motion behavior while preserving the same read-only backend.

The current redesign is included in the v4.0.9 experimental snapshot. It remains an incomplete on-hold interface and is not evidence that the broader product plan or installed runtime is complete.

## If Studio looks stale

1. Confirm the manager and active runtime versions separately.
2. Inspect engine readiness and the selected project binding.
3. Compare the Plan revision and linked host projection.
4. Restore the window through the supported launcher.
5. Use [Troubleshooting](TROUBLESHOOTING.md) before reinstalling or replaying work.

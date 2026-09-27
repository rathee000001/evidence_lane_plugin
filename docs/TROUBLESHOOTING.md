<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Troubleshooting

Identify which state failed before retrying. Preserve exact error codes, release identity, task identity, and available receipts.

## Plugin is installed but Studio is missing

Check the plugin manager version and the active shared-runtime version separately. First detection may still be acquiring, materializing, validating, registering, or recovering a release.

Do not call a partial installation active. Inspect `installation-status.json`, the release receipt, and the stable launcher paths.

## Installation is interrupted

Evidence Lane preserves eligible prior releases and quarantines failed new releases. Do not manually merge a staging folder or copy runtime trees. Resume through the packaged installer after identifying the failed phase.

If the process is live and CPU/disk evidence shows progress, wait on the same process. A quiet console is not proof of a stall.

## Studio is absent from Installed Apps or Windows Start

Confirm that the active release is v4.0.10 or later and first detection reached `ACTIVE_EXACT_RELEASE`. Windows should report:

- **Evidence Lane Studio** in Installed Apps;
- `EvidenceLaneStudio.exe` in the current-user App Paths registry;
- Start and desktop entries with the Evidence Lane icon;
- application identity `EvidenceLane.Studio`;
- a per-user shell under `%LOCALAPPDATA%\Programs\Evidence Lane Studio`.

Search finding an old `.lnk` file is not proof that the application is installed. If Windows still shows a blank icon or an old shortcut-only result, inspect the normal application registration and restart a fresh Start/Search session after the exact installer completes. Do not modify the unrelated **Evidence Lane** application.

## Studio has the wrong title or window size

The supported title is **Evidence Lane Studio**. The launcher opens or restores one maximized window. A title such as `Projects · Evidence Lane Studio`, a normal-sized restored window, or multiple newly opening windows indicates an older Studio bundle or launch path. Verify the managed plugin version, active release receipt, Windows application version and exact shell target before retrying.

## Native Codex thread tools are missing in one task

Evidence Lane does not own native Codex task/thread tools. Confirm that `.mcp.json` contains only the optional `evidence-lane` server and no `codex_app` namespace. A turn that began before a reconnect keeps its original enabled tool catalog; additional messages steered into that same in-progress turn do not add tools. Finish or stop that turn, then start a fresh turn before diagnosing an MCP collision.

## Studio opens with no project

Confirm that the engine is running, the client is connected, and the project was explicitly registered. Studio does not register projects itself.

## Studio shows a stale Plan

Compare:

- authoritative project Plan revision;
- linked host Plan path and digest;
- complete row count;
- one active task ID;
- current task states.

Refreshing a display must not rewrite the Plan.

## Hook source reports issues

Keep all rows disabled. Verify the plugin manager source, command paths, JSON-only output contract, event-specific timeout, and trust readback. Do not enable a failing row to discover what happens.

## A listed tool is not ready

Catalogue presence is not readiness. Check dependencies, configured provider, credentials, license, device compatibility, operation eligibility, and the visible fallback.

## Sources or evidence look stale

Compare current bytes with the registered snapshot. Use **Refresh changed evidence** for selected material. Historical receipts do not prove present-day freshness.

## An operation has uncertain effects

Do not replay automatically. Preserve the operation ID and use its status or recovery workflow to determine committed effects. A timeout can occur after an effect.

## The Plan changed during work

Read the new revision and the worker's admitted task contract. A stale worker result cannot complete a replacement task.

## Recovery is needed

Create and verify a coherent backup when the engine state allows it. Offline repair requires a stopped owner. Restore to a fresh destination when the workflow requires it; preserve the original root and uncertainty.

<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Installation

Evidence Lane uses a plugin-first installation with two separate owners:

- **Codex manages the plugin** in its plugin cache/runtime.
- **Windows manages the visible Studio application** in Installed Apps, while first detection owns the shared engine and retained toolchains under the stable runtime root.

Both sides are tied to one immutable Evidence Lane release identity. Studio does not install an extra MCP server into Codex or replace native Codex tools.

## Before you install

You need:

- a persistent local Windows PC;
- Codex Desktop Stable or Beta;
- Git and network access to the public repository and release assets;
- enough disk space for the selected shared components;
- permission to create the shared installation under `C:\Apps\EvidenceLaneStudio` or an explicit supported override;
- compatible hardware only for optional accelerated providers.

Project databases, source files, and credentials are not release payloads.

## Install the exact marketplace snapshot

The current v4.0.10 experimental marketplace snapshot is bound to one immutable tag. It is incomplete and is not production-ready. From a terminal where the `codex` command is available:

```powershell
codex plugin marketplace add https://github.com/rathee000001/evidence_lane_plugin `
  --ref evidence-lane-v4.0.10-bundle-977fb5ec2702ff9d `
  --sparse .agents/plugins/marketplace.json `
  --sparse plugins/evidence-lane-plugin `
  --json

codex plugin add evidence-lane-plugin@evidence-lane-github --json
```

If `evidence-lane-github` is already configured for a different snapshot, inspect it before replacing or upgrading it. Do not point an installed release at unreviewed working-tree files.

## What first detection does

On the next supported plugin detection, the installer:

1. verifies the release binding, source manifest, asset names, sizes, and SHA-256 values;
2. selects required components for the measured Windows host;
3. preserves an eligible prior release for rollback;
4. materializes and validates the new shared runtime;
5. verifies all registry-derived retained tool assignments—103 in v4.0.10—and the selected native, model, Node, Power BI and provider components;
6. registers the windowless engine at login;
7. installs or upgrades **Evidence Lane Studio** as a normal per-user Windows application;
8. creates its Installed Apps record, App Paths entry, `EvidenceLane.Studio` identity, Start entry, desktop shortcut, icon and uninstaller;
9. launches the engine and one maximized window titled **Evidence Lane Studio**;
10. records an installation receipt without copying project databases or credentials.

The process is intentionally expensive because it verifies the full installed tree. Do not interrupt it between publication of the installation pointer and Windows registration.

## Shared installation and project state

The default shared root is:

```text
C:\Apps\EvidenceLaneStudio
```

`EVIDENCE_LANE_STUDIO_ROOT` is the explicit override. Shared tools are installed once. Every project still uses its own selected state root with separate project records.

The small Windows application shell is registered under:

```text
%LOCALAPPDATA%\Programs\Evidence Lane Studio
```

It starts the exact launcher in the shared root. The shell does not duplicate the engine, providers, model assets or retained toolchains.

## Hardware and licensed components

CPU is always the baseline. CUDA and DirectML are selected only when the host and operation support them. Provider selection is recorded; it is not inferred from a package name.

Ghostscript remains separately license-gated. External services remain unconfigured until their own credentials and project grants are supplied.

## Verify the manager installation

```powershell
codex plugin list --json
```

Confirm that `evidence-lane-plugin@evidence-lane-github` is installed, enabled, and reports the selected version. Manager installation does not by itself prove that the shared runtime finished first detection.

The plugin manifest declares only the optional `evidence-lane` MCP server. Native Codex application tools remain owned by Codex. A task that was already in progress before an app reconnect keeps the tool catalog from the start of that turn; start a fresh turn before diagnosing that as an MCP namespace collision.

## Verify the active runtime

After first detection, confirm:

- `installation-status.json` reports `ACTIVE_EXACT_RELEASE`;
- `.evidence-lane-release.json` identifies the exact release and selected components;
- Windows Installed Apps contains **Evidence Lane Studio** with the exact release version;
- App Paths resolves `EvidenceLaneStudio.exe` to the per-user application shell;
- Windows Start and the desktop show the Evidence Lane icon and exact application name;
- the Start and desktop shortcuts carry the `EvidenceLane.Studio` application identity;
- the windowless engine is running;
- exactly one maximized window titled **Evidence Lane Studio** opens or can be restored;
- the current tool catalog still contains all 103 retained v4.0.10 entries and the selected dependency records;
- project state and credentials were not changed by the release installer.

## Upgrade behavior

An upgrade validates the current release before stopping it, preserves the prior release, builds the new release separately, validates the complete tree, then publishes it. The Windows installer upgrades the application identity in place using the same AppId. A failed release is quarantined. A changed startup entry or installation file fails closed instead of being overwritten silently.

## Uninstall boundaries

Removing **Evidence Lane Studio** from Windows Installed Apps removes the per-user shell, Start entry, desktop shortcut, App Paths registration and application identity. It does not silently delete project source, external project-state roots, credentials or the Codex-managed plugin. The large shared runtime remains separately governed by the plugin installer so a Windows shell uninstall cannot destroy project material.

Remove or change the Codex plugin through the normal plugin manager. Treat shared-runtime cleanup as a separate explicit maintenance action after project references and rollback needs are checked.

See [Troubleshooting](TROUBLESHOOTING.md) before retrying a failed or interrupted upgrade.

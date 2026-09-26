<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Installation

Evidence Lane uses a plugin-first installation. The Codex-managed plugin verifies and provisions the shared Windows engine, Studio, and toolchain from one immutable release identity.

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

The current v4.0.8 experimental marketplace snapshot is bound to one immutable tag. It is incomplete and is not production-ready. From a terminal where the `codex` command is available:

```powershell
codex plugin marketplace add https://github.com/rathee000001/evidence_lane_plugin `
  --ref evidence-lane-v4.0.8-bundle-977fb5ec2702ff9d `
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
5. registers the windowless engine at login;
6. creates the Desktop and direct Start-menu Studio shortcuts;
7. launches the engine and one visible Studio window;
8. records an installation receipt without copying project databases or credentials.

The process is intentionally expensive because it verifies the full installed tree. Do not interrupt it between publication of the installation pointer and Windows registration.

## Shared installation and project state

The default shared root is:

```text
C:\Apps\EvidenceLaneStudio
```

`EVIDENCE_LANE_STUDIO_ROOT` is the explicit override. Shared tools are installed once. Every project still uses its own selected state root with separate project records.

## Hardware and licensed components

CPU is always the baseline. CUDA and DirectML are selected only when the host and operation support them. Provider selection is recorded; it is not inferred from a package name.

Ghostscript remains separately license-gated. External services remain unconfigured until their own credentials and project grants are supplied.

## Verify the manager installation

```powershell
codex plugin list --json
```

Confirm that `evidence-lane-plugin@evidence-lane-github` is installed, enabled, and reports the selected version. Manager installation does not by itself prove that the shared runtime finished first detection.

## Verify the active runtime

After first detection, confirm:

- `installation-status.json` reports `ACTIVE_EXACT_RELEASE`;
- `.evidence-lane-release.json` identifies the exact release and selected components;
- the Desktop and direct Start-menu shortcuts target the stable launcher;
- the windowless engine is running;
- exactly one Studio window opens or can be restored;
- project state and credentials were not changed by the release installer.

## Upgrade behavior

An upgrade validates the current release before stopping it, preserves the prior release, builds the new release separately, validates the complete tree, then publishes and registers it. A failed release is quarantined. A changed shortcut, startup entry, or installation file fails closed instead of being overwritten silently.

See [Troubleshooting](TROUBLESHOOTING.md) before retrying a failed or interrupted upgrade.

<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Quickstart

Use this guide when you want the shortest supported path from a fresh installation to a project you can inspect and continue.

## 1. Confirm the supported host

Use a persistent local Windows PC with Codex Desktop Stable or Beta. Make sure the source folder you want to work with already exists and choose a separate durable folder for Evidence Lane project state.

Evidence Lane does not currently advertise the Windows Studio on macOS, Linux, an ephemeral sandbox, or Codex CLI.

## 2. Install the managed plugin

Install an exact published release through the Codex plugin manager. The current release is [v4.0.7](https://github.com/rathee000001/evidence_lane_plugin/releases/tag/evidence-lane-v4.0.7-bundle-977fb5ec2702ff9d). Follow [Installation](INSTALL.md) for the exact commands and verification steps.

The plugin comes first. Its first-detection route provisions the shared engine, Studio, and required local toolchain under `C:\Apps\EvidenceLaneStudio` unless an explicit supported override is configured.

## 3. Open or resume the project

In Codex, use the **Open or resume a project** workflow. Select the project explicitly. Evidence Lane checks the connected runtime and any existing session before it opens a new one.

You should be able to inspect:

- the project identity;
- its source root and separate state root;
- the current Plan revision;
- the current session state;
- any recorded recovery or continuation boundary.

## 4. Choose project storage

Use **Choose project storage** to register the source folder and its separate state folder. Registration records the relationship; it does not silently move source files or copy project databases into the shared installation.

## 5. Bring in the first sources

Use **Bring in project sources**. Select only material relevant to the project. Evidence Lane classifies the selection and prepares bounded source work before parsing or indexing begins.

Supported source families include code, Word, PowerPoint, Excel, structured data, Tableau, Power BI, PDF/OCR, images and media, research/web material, generated artifacts, and explicitly selected custom data such as SQLite.

## 6. Inspect or create the Plan

Use **Create or update the Plan**. Confirm the intended outcome, ordered tasks, checks, and current task. Completed history remains visible. A new direction replaces only affected unfinished work after a safe checkpoint.

## 7. Carry out one eligible task

Use **Carry out the next task**. The engine checks the current task, eligible tools, provider readiness, and operation scope. A running job is not complete. Inspect the addressed output and its recorded verification result.

## 8. Open Studio

Launch **Evidence Lane Studio** from the Desktop shortcut or the direct Start-menu entry. Studio is read-only. Use it to inspect the Plan, jobs, evidence, workers, tools, connections, learning, compute, and diagnostics.

## 9. Continue deliberately

Choose the next workflow based on the need:

- refresh changed sources;
- revise the Plan;
- recall project context;
- inspect evidence;
- transfer work to another client;
- back up or recover;
- close the project session.

## Stop when a boundary is unclear

Do not replay an uncertain operation, force a stale Plan result into the current revision, or treat a visible Studio window as proof of a connected project. Use [Troubleshooting](TROUBLESHOOTING.md) to identify the failed state first.

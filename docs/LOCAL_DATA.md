<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Local data

Evidence Lane separates the shared installation from project-owned records.

## Shared installation

The default shared root is `C:\Apps\EvidenceLaneStudio`. It contains the engine, Studio, plugin runtime copy, and selected shared tools. It is reused across projects.

## Per-project state

Each project uses its own selected durable state root. Project records keep separate responsibilities for the Plan, sources, evidence, receipts, memory, lessons, instructions, sessions, and source-specific material.

References connect those records without flattening them into one universal database. Source-specific project records are created only when the relevant source family is selected and used.

## Source files

Source files stay in their selected source locations. Evidence Lane records identities and creates addressed project artifacts through owning workflows. It does not silently move the user's source tree into the shared runtime.

## Backups

A coherent backup preserves the relationship among separate project records and registered files. Offline recovery requires a stopped owner. Verify a backup before treating it as recoverable.

## External access

Local storage does not make every operation offline. Inspect the declared network route for research, repositories, configured services, model providers, or observability tools.

## Removal

Plugin removal, shared-runtime cleanup, project-state deletion, and connector revocation are different actions. Removing one does not imply the others. Preserve project data and provenance unless their exact removal is authorized.

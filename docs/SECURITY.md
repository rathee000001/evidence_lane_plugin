<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Security

Evidence Lane protects project boundaries by keeping access explicit, validating source and task identity, bounding tool work, and preserving evidence about actual effects.

## Core security expectations

- Operations stay within selected project paths, current task contracts, and connector grants.
- Studio remains read-only for human users.
- Workers are bounded child processes owned by the engine.
- Credentials do not enter public examples, project evidence, or release receipts.
- Release assets and installed source are checked against exact sizes and SHA-256 values.
- Changed installer-owned startup or shortcut entries fail closed.
- Uncertain one-shot effects are inspected before replay.
- Stale Plan results cannot complete replacement tasks.

## Connections and providers

A transport connection is not a project grant. A registered provider is not proof of readiness. Check configuration, scope, expiry, credentials, license, selected targets, and measured operation readiness.

## Hooks

Hooks are installed trusted through the supported manager and disabled by default. They validate/redact input and emit bounded results. They must not expose credentials, create projects implicitly, or control the host or project lifecycle.

## Reporting a concern

Follow the repository's root [SECURITY.md](../SECURITY.md) reporting instructions. Do not post secrets, private exploit details, unredacted project data, or credentials in a public issue.

Include the affected release, exact operation or surface, reproduction boundary, and whether any effect may have committed.

<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Privacy and data boundaries

Evidence Lane is local-first, but “local-first” does not mean every operation is offline. Privacy depends on the selected source, workflow, connection, provider, and public surface.

## Public website

The public website presents bundled product information and interactive illustrations. Its searches and filters do not submit commands to a user's Evidence Lane engine. The illustrative Studio is not connected to a local project.

The site is hosted on Vercel and is subject to the hosting platform's operational logs and policies. Links to GitHub or another service open that service under its own policies.

## Local installation

The shared engine, Studio, and local toolchain are installed under the selected Windows shared root. Project databases and credentials are not release payloads.

## Project information

Each project uses an explicitly selected durable state root. Selected source files stay in their source locations unless an owning operation creates an addressed project artifact. Separate project records retain their own purpose and attribution.

## External services

Research, repositories, model providers, vector stores, observability services, or other APIs may send data outside the local machine. Review the exact provider, operation, selected target, credentials, and project grant before use.

Evidence Lane must not silently send private source bytes to create a project image or other optional media. Any later generated-cover provider requires explicit configuration, a cost boundary, safe projected input, provenance, and visible status.

## Hooks and capture

Hook enablement is user-owned and disabled by default. Captured host events, project memory, task exchanges, instructions, and source evidence remain separately attributed. Redaction reduces exposure but does not replace access control.

## Public artifacts

Do not publish project databases, credentials, internal receipts, private logs, or proprietary source material in issues, releases, documentation, websites, or demonstrations.

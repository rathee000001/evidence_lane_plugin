<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Integrations

Evidence Lane uses several kinds of connection. Keeping them distinct prevents a transport connection from being mistaken for permission to use a project resource.

## Project connections

A project connector defines a bounded relationship to a selected resource or service. Its record can include:

- purpose;
- allowed operations;
- selected targets;
- configuration references;
- version;
- expiry;
- active or revoked state.

Use **Inspect connections**, **Configure a connection**, or **Withdraw a connection**. Saving a connector does not install arbitrary code or prove that the service is reachable.

## SDK and MCP

The SDK and MCP expose typed Evidence Lane operations to supported clients. They are transport and interface surfaces. They do not become the project authority, choose a project silently, or grant an external service permission.

## Tools and providers

Tools perform operation-specific work. Providers supply compute, models, or services. A tool may have a primary route and fallbacks. Readiness depends on the operation, installed dependencies, credentials, license, device compatibility, and current health.

## External services

External vector stores, observability services, research services, repositories, and other APIs remain unconfigured until the user supplies their supported configuration and project grant. Credentials must not be written into project evidence, public examples, or release receipts.

## Host Hooks

Hooks capture supported host events through plugin-owned stages. They are trusted through the supported manager and disabled by default. Enabling Hooks is user-owned. Hook capture does not authorize project work, move the Plan, or prove completion.

## Readiness checklist

Before using an integration, confirm:

1. the selected project and purpose;
2. the exact registered connector or provider;
3. the permitted operations and targets;
4. required configuration, license, and credentials;
5. measured runtime readiness;
6. expected output and verification;
7. expiry and revocation behavior.

## Failure behavior

A registered connector can be unready. A transport can be connected while the project grant is missing. A provider can be installed but incompatible with the current host. Evidence Lane reports those states separately and should fail closed rather than silently broadening access.

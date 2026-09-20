<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# SDK, MCP, and Hooks

This guide is for developers and advanced users who need to understand how a public workflow reaches the persistent engine.

## From intention to operation

The path is:

1. A public skill expresses the user's workflow.
2. The workflow selects a typed operation from the current registry.
3. SDK or MCP carries the validated request to the engine.
4. The engine checks the project, task, scope, tools, provider readiness, and operation rules.
5. Bounded worker processes perform admitted tool work.
6. The engine records the result and verification state.
7. Studio displays the resulting observations read-only.

## Current release snapshot

The v4.0.7 source snapshot contains 24 public skills, 295 typed actions, and 103 retained tool/dependency assignments. These numbers are generated release facts, not permanent product limits. Current registries and schemas remain authoritative.

## Skills

Skills describe procedures in user language. They do not duplicate engine implementations. Each skill names when it fits, what it may call, and what boundary must remain visible.

## Typed actions

Actions define inputs, state-change behavior, project/task requirements, and output schema. Use the live action schema rather than inventing parameters from examples.

## MCP

The plugin's MCP server exposes current typed operations through the supported Codex connection. MCP is not the Studio server UI, a project connector grant, an external tool provider, or an acting AI agent.

## SDK

The SDK supplies typed client and internal execution owners. Internal modules remain implementation detail; public consumers should use documented operations and schemas.

## Workers

Workers are owned operating-system child processes used for bounded tool execution. They are not AI subagents. Worker start, output, failure, timeout, and verification remain separate observations.

## Hooks

The current Hook package covers 12 supported events with 48 visible event/stage rows. Interrupt and SessionEnd use a three-second host boundary; other supported events use their declared bounded value. Rows are installed trusted and disabled by default.

Hook stages validate and redact input, classify/seal the event, deliver it once, and emit a bounded result. They do not create a project implicitly, expose credentials, control the host, or declare Plan/Goal completion.

## Error handling

Typed errors should identify which boundary failed: project selection, stale task, source identity, connector grant, tool readiness, timeout, malformed input, output validation, or engine availability. Do not retry a possibly committed one-shot operation until its owning status/recovery workflow establishes the effect.

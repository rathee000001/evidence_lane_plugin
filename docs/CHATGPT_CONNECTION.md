# ChatGPT connection boundary

Evidence Lane uses one plugin directory and one canonical MCP tool surface for
Codex and ChatGPT Work. Product support, MCP transport, source-edit authority,
and durable server storage are separate capability axes.

## Supported packaging

The package contains:

- a bundled local STDIO MCP server in `.mcp.json`;
- the Evidence Lane lifecycle skill and prompt-bar command templates;
- SessionStart and privacy-minimized UserPromptSubmit hooks;
- a required Google Drive app dependency in `.app.json`.

Supported local plugin surfaces can install this package from the private Git
marketplace and start a new task or chat. Codex materializes a versioned local
cache because executable plugins must run somewhere, but that cache is derived
from the pinned Git snapshot and never imports the F-drive checkout. The Google
Drive dependency uses the host's normal OAuth connector flow; its token is not
exposed to the Evidence Lane Python process.

## ChatGPT desktop versus web

ChatGPT desktop Work may use installed local plugin capabilities available on
that surface. A local STDIO server can therefore have a durable local
filesystem even though the UI host is ChatGPT.

ChatGPT cannot derive or execute an MCP server from a Git repository URL. It
requires a registered remote MCP connection at a public HTTPS `/mcp` endpoint.
After registration, the technical app ID starts with `plugin_asdk_app` and can
be referenced from `.app.json`. This repository has no registered Evidence
Lane remote app ID, so it does not invent one.

The server supports authenticated Streamable HTTP:

- non-loopback bind requires either a static bearer token or a complete OAuth
  JWT resource-server configuration;
- the advertised base URL must be HTTPS through
  `EVIDENCE_LANE_MCP_BASE_URL`;
- static bearer is a Codex-only private-client fallback because ChatGPT cannot
  present arbitrary custom API keys;
- ChatGPT uses an established OAuth issuer whose JWTs are checked for signature,
  issuer, audience, expiry, subject, asymmetric algorithm, and scopes;
- a production remote connection still requires a long-lived container,
  durable volume, OAuth issuer, deployment, registration, host review, and a
  new explicit HIL.

## Source and persistence capabilities

`session_boot` and `session_resume` record two independent facts:

- whether the MCP server has a durable filesystem;
- whether the client can edit the governed source directly.

When source editing is user-mediated, Refresh remains blocked until the exact
source update is present and the user supplies
`USER_APPLIED_AND_PULL_CONFIRMED`. A directly authorized Codex-style source
boundary uses `HOST_SANDBOX_FINAL_STATE_CONFIRMED`.

A durable server uses its user-owned local store. An explicitly ephemeral
server requires the direct Google Drive persistence backend. The installed
Drive connector may provide OAuth onboarding and a separately verified mirror,
but model-mediated connector access is not substituted for atomic server
persistence.

## Connection proof

Before calling ChatGPT parity demonstrated, verify all of:

1. the exact reviewed plugin release is installed;
2. the ChatGPT surface exposes every canonical MCP tool, including `/ev`
   resume, Refresh, Fuse, six-way HIL, indexed rollback, and Git/local intake;
3. the required Google Drive connector is connected through normal OAuth;
4. `runtime_doctor`, flash, and persistent-state reads pass;
5. source-edit authority is recorded truthfully;
6. the chosen server store survives a new chat/task;
7. an accepted PV is loaded directly without a rebuild;
8. a remote deployment, if used, has authenticated HTTPS and verified durable
   readback;
9. no tool or permission reduction is mislabeled as parity;
10. the next real PV remains behind explicit HIL.

Installation and connector sign-in alone satisfy none of the PV/HIL gates.
See `REMOTE_DEPLOYMENT.md` for the container contract and exact ChatGPT fields.

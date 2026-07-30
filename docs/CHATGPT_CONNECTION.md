# ChatGPT connection boundary

Evidence Lane uses one plugin directory and one canonical MCP tool surface for
Codex and ChatGPT Work. Product support, MCP transport, source-edit authority,
and durable server storage are separate capability axes.

## Supported packaging

The package contains:

- a bundled local STDIO MCP server in `.mcp.json`;
- the Evidence Lane lifecycle skill plus matching native skills and root
  command templates for the complete ordered `/evi` surface;
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

ChatGPT cannot derive or execute an MCP server from a Git repository URL. A
personal developer-mode plugin can reach the exact local MCP through OpenAI
Secure MCP Tunnel. The tunnel client runs inside the local trust boundary,
forwards ChatGPT MCP work to the local process, and leaves the SQLite authority
on the durable local machine. This is the selected private single-user HIL
route.

ChatGPT's **Suggested prompts** setting is host-owned. It may surface
context-aware follow-ups, but the MCP has no documented gray-composer write
field. Evidence Lane therefore returns and seals a neutral
`suggested_next_prompt`; ChatGPT must show it visibly before HIL even when the
host does not render gray text. See `PROMPT_SUGGESTION_COMPATIBILITY.md`.

A later public plugin instead requires a registered remote MCP connection at a
stable public HTTPS `/mcp` endpoint. After registration, the technical app ID
starts with `plugin_asdk_app` and can be referenced from `.app.json`. This
repository has no registered Evidence Lane public app ID, so it does not invent
one.

The server supports authenticated Streamable HTTP:

- non-loopback bind requires either a static bearer token or a complete OAuth
  JWT resource-server configuration;
- the advertised base URL must be HTTPS through
  `EVIDENCE_LANE_MCP_BASE_URL`;
- static bearer is a Codex-only private-client fallback because ChatGPT cannot
  present arbitrary custom API keys;
- ChatGPT uses an established OAuth issuer whose JWTs are checked for signature,
  issuer, audience, expiry, subject, asymmetric algorithm, and scopes;
- a production public connection still requires a long-lived container,
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

After exact approval, Fuse does not claim that a new UI window exists. It seals
the accepted PV and returns `OPEN_NEW_CODEX_TASK` or
`OPEN_NEW_CHATGPT_CHAT`. The host or user opens that window. In the fresh
session, `/evi-00-state-travel` verifies Boot, locked Flash, accepted pointer
generation, manifest, and package seals through the same atomic Boot/Flash
operation, enters the accepted bytes without a rebuild, and stops at
`WAITING_FOR_NEXT_USER_COMMAND`.

## Connection proof

Before calling ChatGPT parity demonstrated, verify all of:

1. the exact reviewed plugin release is installed;
2. the ChatGPT surface exposes every canonical MCP tool and the complete
   ordered `/evi` controls: State Travel first, atomic Boot/Flash, all seventeen
   source-intake commands, the separate Mode sidecar, Build PV Entry, automatic
   exit-Refresh, HIL, Fuse, Rollback, and explicit Exit Boot; Planning mode
   updates only the derived Plan runtime projection, while DROP/SUPERSEDE use
   the explicit append-only backlog transition;
3. the required Google Drive connector is connected through normal OAuth;
4. `runtime_doctor`, flash, and persistent-state reads pass;
5. source-edit authority is recorded truthfully;
6. the chosen server store survives a new chat/task;
7. after Fuse, a fresh ChatGPT chat completes State Travel, loads the accepted
   PV without a rebuild, verifies pointer and seals, and waits for the user;
8. the selected Secure MCP Tunnel is healthy and the local store survives a
   new ChatGPT chat; a public remote deployment, if later used, has
   authenticated HTTPS and verified durable readback;
9. no tool or permission reduction is mislabeled as parity;
10. the next real PV remains behind explicit HIL.
11. the candidate Exit Slip and MCP response carry the same neutral next-action
    contract, and no suggestion is treated as a submitted HIL decision.

Installation and connector sign-in alone satisfy none of the PV/HIL gates.
See `REMOTE_DEPLOYMENT.md` for the container contract and exact ChatGPT fields.

For the private single-user HIL, Codex installs the candidate from one exact Git
branch commit. ChatGPT web selects the associated Secure MCP Tunnel and calls
that same locally running build. The Git URL is code provenance, not the
ChatGPT server URL. Vercel is neither an installer nor part of this HIL. It
remains a future public-hosting option only after durable external state and
production authentication are implemented and separately accepted.

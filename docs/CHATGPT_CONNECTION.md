# ChatGPT connection boundary

Evidence Lane is a tool-only MCP app. Its canonical tools and deterministic
engine are host-neutral, but the connection method is not.

## Current supported boundary

- Codex desktop and CLI can run the private plugin over local STDIO.
- ChatGPT cannot connect directly to a local MCP server.
- A private local or on-premises runner requires OpenAI Secure MCP Tunnel.
- A later alternative is a stable public HTTPS MCP endpoint plus Developer Mode.
- Hosting on Vercel is deliberately deferred.
- ChatGPT tool availability still depends on account/workspace permissions.
- The current personal ChatGPT account is Pro with Developer Mode enabled, but
  the live Create Plugin form lists no available Secure MCP Tunnel.
- OpenAI currently limits Pro custom MCP connections to read/fetch; the full
  write-capable MCP surface required by this HIL is available only on eligible
  Business or Enterprise/Edu workspaces.
- ChatGPT may cache a reviewed tool snapshot; changed tools require app refresh
  and review before the host should be treated as updated.

The repository must not claim a ChatGPT installation merely because local HTTP
or STDIO works. A valid ChatGPT proof requires the eligible account, an actual
tunnel or HTTPS connection, successful tool discovery, and one non-mutating
`runtime_doctor` or `session_flash_status` call.

Therefore the 0.2.0 candidate is **not installed in ChatGPT**. Creating a
read-only Pro connection would be a reduced product boundary, not host parity,
and must not be reported as the accepted agentic-code plugin. The blocked
connection can resume only after:

1. a Secure MCP Tunnel is created and associated with the target ChatGPT
   workspace and Platform organization;
2. `tunnel-client` is running against the private Evidence Lane MCP server;
3. the target workspace permits the required write tools;
4. ChatGPT discovers and reviews the same canonical tool definitions; and
5. a non-mutating doctor/flash call proves the live connection.

## Host-specific source rule

When ChatGPT proposes a code Delta for the user to apply, Refresh stays blocked
until the repository is pulled to the exact final commit and the user provides
`USER_APPLIED_AND_PULL_CONFIRMED`.

Codex desktop, CLI, or VM uses
`HOST_SANDBOX_FINAL_STATE_CONFIRMED` after its bounded local/sandbox work.
Ephemeral hosts and public AI routes require durable user-owned PV persistence;
local persistent Codex may use the governed local store.

Official references:

- <https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt>
- <https://help.openai.com/en/articles/12515353-build-with-the-apps-sdk>

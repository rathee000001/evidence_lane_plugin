# Host capability matrix

The same deterministic PV engine, eighteen-lane registry, and transition law
apply across hosts. A host may expose fewer capabilities; reduced capability
never expands authority or silently becomes parity.

| Capability | Durable local MCP (Codex or ChatGPT desktop) | Ephemeral Codex/runner | ChatGPT web through Tunnel or remote MCP | Mobile review |
| --- | --- | --- | --- | --- |
| Session flash | Exact bundled authority verified locally | Exact bundle verified in runner | Tunnel verifies the local bundle; public remote verifies its deployed bundle | Reads remote receipt only |
| Source read | Registered local path or enrolled clone | Authorized sandbox clone | Tunnel reaches the registered local source; public remote uses connected source capability | Remote review only |
| Source write | Bounded authorized worktree | Bounded authorized sandbox | Tunnel uses bounded local authority; public remote writes only when its workspace permits | No source execution |
| PV engine | Local plugin runtime | Remote MCP runtime | Local runtime through Tunnel, or public remote runtime | Remote runtime |
| Durable project store | User-owned local data root | Direct external durable backend required | Tunnel keeps the user-owned local root; public ephemeral servers require a direct backend | Same external store |
| Prompt-to-prompt entry | `/evi` resumes accepted/pending state directly | Verified durable readback before load | Tunnel resumes local state; public server resumes its registered durable state | Read-only status |
| State Travel window | Host opens a fresh Codex task or ChatGPT desktop chat; plugin verifies the new host session | Host opens a fresh task and rebinds durable state | Host/user opens a fresh chat and rebinds the tunneled or registered server session | Review request only |
| Source confirmation | `HOST_SANDBOX_FINAL_STATE_CONFIRMED` | `HOST_SANDBOX_FINAL_STATE_CONFIRMED` | `USER_APPLIED_AND_PULL_CONFIRMED` when ChatGPT cannot apply locally | Review only |
| HIL decision | Explicit human tool call | Explicit human tool call | Explicit human tool call | Explicit remote control |
| Rollback | Accepted-pointer CAS only | Same law after durable-state verification | Same law after durable-state verification | Decision request only |
| Prompt indexing | Hook stores entry PV, turn ID, and SHA-256; no raw prompt | Same only when host hooks run and durable index exists | Server/session reference support must be demonstrated | Review reference only |
| Google Drive connector | Required host OAuth dependency; verified mirror role | Connector may assist, but direct backend is required when server is ephemeral | Required host OAuth dependency; no token passed to MCP | Connector UI only |
| Remote Git write | Separate two-step gate | Separate two-step gate | Separate two-step gate | Separate two-step gate |

## Currently demonstrated

- local Codex source and test execution;
- local STDIO MCP initialization and tool calls;
- persistent user-owned local-store behavior in fixtures;
- host-kind aliases plus explicit filesystem/source-edit capability axes;
- prompt-indexed rollback without raw prompt retention;
- required Google Drive connector packaging;
- encrypted durable-backend behavior against an in-memory contract;
- a standalone, non-editable bootstrap from the plugin snapshot without
  repository-level packaging or a pre-existing virtual environment;
- local OAuth resource-server checks for public health, protected-resource
  metadata, and an authenticated `/mcp` challenge;
- complete `/evi` command/native-skill parity, with atomic Boot/Flash,
  seventeen source-intake commands before Build PV Entry, one separate Mode
  sidecar, and explicit Exit Boot;
- fresh-window Codex and ChatGPT State Travel fixtures that verify atomic
  Boot/Flash, accepted pointer generation, package seals, and the final wait
  state.

## Not currently demonstrated

- a live governed Drive upload, content readback, and hash verification;
- a secure write-capable ChatGPT MCP connection through the selected Tunnel;
- a live public HTTPS deployment with a durable mounted volume;
- native prompt-bar chrome changes.

ChatGPT desktop may use a locally bundled plugin surface. ChatGPT web can use a
registered Secure MCP Tunnel for private developer testing or a public remote
MCP for release. A local STDIO build alone proves neither connection, and a
read-only or partial connection is a different product. See
[`CHATGPT_CONNECTION.md`](CHATGPT_CONNECTION.md).

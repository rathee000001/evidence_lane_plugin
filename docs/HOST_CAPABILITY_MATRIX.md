# Host capability matrix

The same deterministic PV engine, eighteen-lane registry, and transition law
apply across hosts. A host may expose fewer capabilities; reduced capability
never expands authority or silently becomes parity.

| Capability | Durable local MCP (Codex or ChatGPT desktop) | Ephemeral Codex/runner | ChatGPT web remote MCP | Mobile review |
| --- | --- | --- | --- | --- |
| Session flash | Exact bundled authority verified locally | Exact bundle verified in runner | Exact bundle verified in remote MCP runner | Reads remote receipt only |
| Source read | Registered local path or enrolled clone | Authorized sandbox clone | Connected tool/sandbox only | Remote review only |
| Source write | Bounded authorized worktree | Bounded authorized sandbox | Only if the connected MCP/workspace permits the canonical write tools | No source execution |
| PV engine | Local plugin runtime | Remote MCP runtime | Remote MCP runtime | Remote runtime |
| Durable project store | User-owned local data root | Direct external durable backend required | Depends on registered server storage; ephemeral servers require direct backend | Same external store |
| Prompt-to-prompt entry | `/ev` resumes accepted/pending state directly | Verified durable readback before load | Registered server resumes the same state | Read-only status |
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
  metadata, and an authenticated `/mcp` challenge.

## Not currently demonstrated

- a live governed Drive upload, content readback, and hash verification;
- a secure write-capable ChatGPT MCP connection and OAuth round trip;
- a live public HTTPS deployment with a durable mounted volume;
- native prompt-bar chrome changes.

ChatGPT desktop may use a locally bundled plugin surface. ChatGPT web still
requires a registered remote MCP connection. A local STDIO build does not prove
that remote connection, and a read-only or partial connection is a different
product. See [`CHATGPT_CONNECTION.md`](CHATGPT_CONNECTION.md).

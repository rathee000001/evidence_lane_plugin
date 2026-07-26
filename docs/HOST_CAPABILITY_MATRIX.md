# Host capability matrix

The same deterministic PV engine and HIL state machine apply across hosts. A
host may expose fewer tools; reduced capability never expands authority.

| Capability | Local Codex desktop/CLI | Codex VM or ephemeral agent | ChatGPT/public AI | Android/mobile control |
| --- | --- | --- | --- | --- |
| Session flash | Bundled authority verified locally | Bundled authority verified in runner | Verified in remote MCP runner | Reviews same remote receipt |
| Repository read | Local authorized path | Authorized sandbox clone | Connected tool/sandbox only | Remote review only |
| Repository write | Bounded local worktree | Bounded sandbox | User or approved sandbox applies Delta | No local execution |
| Terminal/tests | Host permission | Sandbox permission | Only if connected tool permits | No |
| PV engine | Local plugin runtime | Remote runner | Remote runner | Remote runner |
| Durable PV route | Local user-owned store | Google Drive required | Google Drive required | Same remote store |
| Source confirmation | `HOST_SANDBOX_FINAL_STATE_CONFIRMED` | `HOST_SANDBOX_FINAL_STATE_CONFIRMED` | `USER_APPLIED_AND_PULL_CONFIRMED` | Review/decision only |
| HIL decision | Explicit human tool call | Explicit human tool call | Explicit human tool call | Explicit remote control |
| Remote Git write | Separate two-step gate | Separate two-step gate | Separate two-step gate | Separate two-step gate |

The current private HIL proves the local Codex STDIO route. ChatGPT cannot
connect directly to a local MCP process; it requires Secure MCP Tunnel or a
stable HTTPS endpoint and an eligible Developer Mode connection. Google Drive
and ChatGPT connection boundaries remain unconfigured until those exact host
authorities exist. See [`CHATGPT_CONNECTION.md`](CHATGPT_CONNECTION.md).

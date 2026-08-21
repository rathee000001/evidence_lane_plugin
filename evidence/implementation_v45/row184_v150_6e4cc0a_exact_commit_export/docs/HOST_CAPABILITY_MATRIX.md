# Host capability matrix

The same transition law, eighteen-lane registry, project overlays, and six HIL
choices apply across hosts. Reduced host capability never expands authority.

| Capability | Durable Codex desktop/CLI | Ephemeral Codex/runner | ChatGPT mounted plugin or durable MCP | Review-only client |
| --- | --- | --- | --- | --- |
| Atomic Boot and Flash | Local doctor, locked Flash, local resume | Doctor and Flash, then durable-connector check | Mounted plugin or durable origin performs Boot/Flash | Reads receipts only |
| Runtime state | User-owned local SQLite | Configured transactional connector or fail closed | ChatGPT host's mounted persistent store or durable origin; never Codex or Vercel local files | None |
| Source read/write | Registered local Git path, bounded writes | Authorized sandbox, bounded writes | Append-only plugin writes and bounded source actions inside the ChatGPT host/origin capability; otherwise user-mediated output | No execution |
| Optional Git source arm | AUTO history or content fallback; REQUIRED/DISABLED explicit | Same within authorized source | Origin-side registered source capability | Read-only artifacts |
| Source confirmation | `HOST_SANDBOX_FINAL_STATE_CONFIRMED` | Same | `USER_APPLIED_AND_PULL_CONFIRMED` when user mediated | Review only |
| Chat Lineage | Hooks append visible prompt, steers, output, and telemetry into session plus project SQLite/FTS heads | Host hooks when available | ChatGPT host's own persistent plugin/runtime authority | Read-only |
| Prompt persistence | Resumes the single governed session | Durable readback required | Mounted-store or durable-origin readback required | None |
| State Travel | User-requested/context-full fresh Codex task | Same after durable verification | Same exact unfinished row in a fresh ChatGPT chat bound to its own verified runtime | Review request only |
| HIL/Fuse | Explicit human tool call | Explicit human tool call | Explicit human tool call | Decision request only |
| Rollback | Accepted-pointer CAS only | Same after durable readback | Same at durable origin | Request only |
| Google Drive | Optional mirror/fallback | Optional mirror; not runtime authority | Optional mirror at origin | Connector UI only |
| Remote Git write | Separate prepare/one-use confirmation | Same | Same | Request only |

The public controls after root `/evi` are Boot, Rollback, Build, Refresh, Mode,
and Source Intake. State Travel is eligible only with a sealed unfinished-work
or accepted-entry handoff and is shown only after explicit user request or
genuine context exhaustion. Exit Boot
explicitly detaches Flash context and capture while preserving the installation,
immutable store, and verification receipt.

Locally demonstrated behavior must be reported separately from live external
proof. A local test does not prove marketplace pickup, ChatGPT OAuth, durable
origin availability, a Vercel preview, or State Travel in a fresh host.

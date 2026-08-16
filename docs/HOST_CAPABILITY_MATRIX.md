# Host capability matrix

The same transition law, eighteen-lane registry, project overlays, and six HIL
choices apply across hosts. Reduced host capability never expands authority.

| Capability | Durable Codex desktop/CLI | Ephemeral Codex/runner | Review-only client |
| --- | --- | --- | --- |
| Atomic Boot and Flash | Local doctor, locked Flash, local resume | Doctor and Flash, then durable-connector check | Reads receipts only |
| Runtime state | User-owned local SQLite | Durable mount or configured transactional connector; otherwise fail closed | None |
| Active surface | Proven `CODEX` surface in stable/current or Beta multi-surface desktop container | Proven `CODEX` execution surface | No execution |
| Native lifecycle transport | Package-local native MCP; no network tunnel | Package-local native MCP; an interactive VM may require one VM-lifetime support tunnel | Receipts only |
| Source read/write | Registered local Git path, bounded writes | Authorized sandbox, bounded writes | No execution |
| Optional Git source arm | AUTO history or content fallback; REQUIRED/DISABLED explicit | Same within authorized source | Read-only artifacts |
| Source confirmation | `HOST_SANDBOX_FINAL_STATE_CONFIRMED` | Same | Review only |
| Chat Lineage | Hooks append visible prompt, steers, output, and telemetry into session plus project SQLite/FTS heads | Host hooks when available | Read-only |
| Prompt persistence | Resumes the single governed session | Durable readback required | None |
| Host-entry gate | Not required; exact local SQLite authority is reused | Exact expiring envelope plus transactional single-consumption receipt before work | Cannot consume |
| State Travel | User-requested/context-full fresh Codex task | Same after durable verification | Review request only |
| HIL/Fuse | Explicit human tool call | Explicit human tool call | Decision request only |
| Rollback | Accepted-pointer CAS only | Same after durable readback | Request only |
| Google Drive | Optional sealed mirror, never runtime authority | Optional sealed carrier, never runtime authority | Connector UI only |
| Storage connector | Separate persistence capability; never an additional-plugin slot | Required only when no durable filesystem/mount exists | Connector UI only |
| Additional plugin slots | Exactly eight governed plugin/toolchain grants only | Same | No execution |
| Remote Git write | Exact prepared non-protected test branch under a valid standing grant | Same | Request only |

The public controls after root `/evi` are Boot, Rollback, Build, Refresh, Mode,
and Source Intake. State Travel is eligible only with a sealed unfinished-work
or accepted-entry handoff and is shown only after explicit user request or
genuine context exhaustion. Exit Boot
explicitly detaches Flash context and capture while preserving the installation,
immutable store, and verification receipt.

Container/package/process/title/CWD identity is not active-surface proof. The
runtime classifier binds container channel, `active_surface=CODEX`, workspace
class, durability, model/reasoning/service profile, project/session/workspace/
host-session namespace, and exposed native capabilities. Missing or ambiguous
capability evidence fails closed without silently selecting a storage connector,
Google Drive, a plugin slot, or a tunnel.

Locally demonstrated behavior must be reported separately from live external
proof. A local test does not prove marketplace pickup, durable connector
availability, a website preview, or State Travel in a fresh host.

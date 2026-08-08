# Persistent Windows ChatGPT read tunnel

The governed Windows tunnel uses the official OpenAI `tunnel-client` v0.0.10
binary pinned by SHA-256. The installer copies it out of the temporary download
directory, asks for the contributor's OpenAI Tunnel ID once, encrypts the
Runtime API key with current-user Windows DPAPI, creates an exact
`CHATGPT_PRO_READ` MCP profile, and registers `EvidenceLane-Tunnel-v140` with
Task Scheduler. The tunnel is for ChatGPT only. Codex installs the exact Git
plugin and keeps the complete local lifecycle; it does not use Vercel or this
tunnel as its plugin transport.

The task starts automatically at the user's first Windows sign-in after boot.
That boundary is intentional: current-user DPAPI avoids a plaintext or
machine-wide Runtime API key. It does not run before any user has signed in.
Task Scheduler restarts the daemon after non-zero exits, and the launcher
requires an exact binary hash plus a successful control-plane poll before it is
reported ready.

## One-time installation

Create one tunnel in the OpenAI Platform first. Run the installer from the
reviewed plugin checkout in a normal PowerShell window. Paste the resulting
`tunnel_...` identifier once, then enter a **Runtime API key**, never an Admin
key, at the masked prompt:

```powershell
& ".\plugins\evidence-lane-plugin\scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1"
```

The generated child launcher sets `EVIDENCE_LANE_MCP_EXPOSURE_PROFILE` to
`CHATGPT_PRO_READ`. That profile exposes exactly 21 annotated read-only tools
for accepted-PV status, Entry/Exit slips, ENV/UOP Flash, lane search/fetch,
diffs, task backlog, and governed panels. It exposes no Build, Refresh, Fuse,
rollback, pointer movement, source write, Git write, deployment, or connector
mutation tool. ChatGPT's native ENV/UOP and Project Mutation workflow remains a
separate host-owned capability; the MCP never claims to perform that mutation.

After installation, add or reconnect Evidence Lane once in ChatGPT using the
same Tunnel ID. Upload the shipped `assets/evidence-lane-icon.png` when the
ChatGPT development form requests the app icon. The server metadata reports
Evidence Lane `1.4.0`, the owned website, and the same public 256-by-256 icon.

`-MigrateCurrentRuntime` is optional. It stops only the process identified by
the historical PID file after its binary SHA-256 matches the pinned client,
then starts the v1.4 scheduled copy. Omit the switch to leave an older healthy
tunnel untouched until the replacement has passed.

## Operator commands

Start or confirm the task:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v140\Manage-EvidenceLaneTunnel.ps1" -Action Start
```

Read status without exposing the key:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v140\Manage-EvidenceLaneTunnel.ps1" -Action Status
```

Repair a stopped or unhealthy task:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v140\Manage-EvidenceLaneTunnel.ps1" -Action Repair
```

Remove the exact installer-owned scheduled task, profile, DPAPI envelope, and
runtime only after reviewing the bound path:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v140\Manage-EvidenceLaneTunnel.ps1" -Action Remove -ConfirmRemoval
```

Removal fails closed unless the target is inside the current user's
`EvidenceLanePV` directory and its installation marker binds the exact runtime
root and scheduled-task name.

The status contract is `PASS` only when the scheduled task exists, the pinned
binary hash matches, its PID is live, and `tunnel-client health` observes a
successful control-plane poll, and the generated profile points to the exact
read-only child launcher. The scripts never print or write the plaintext
Runtime API key. Task Scheduler restarts it after sign-in and `Repair` recovers
an unhealthy verified instance without creating a second live daemon.

`DELTA079B_WINDOWS_TUNNEL_PERSISTENCE_RECEIPT.json` is point-in-time
installation evidence, not a permanent claim that its recorded PID or local
health port remains current. Its top-level `receipt_sha256` seals canonical JSON
with only that field removed. Live readiness must always be obtained from the
Status command; the v1.3 receipt-seal regression test rejects a stale seal.

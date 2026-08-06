# Persistent Windows OpenAI tunnel

The governed Windows tunnel uses the official OpenAI `tunnel-client` v0.0.10
binary pinned by SHA-256. The installer copies it out of the temporary download
directory, encrypts the Runtime API key with current-user Windows DPAPI, and
registers `EvidenceLane-Tunnel-v130` with Task Scheduler.

The task starts automatically at the user's first Windows sign-in after boot.
That boundary is intentional: current-user DPAPI avoids a plaintext or
machine-wide Runtime API key. It does not run before any user has signed in.
Task Scheduler restarts the daemon after non-zero exits, and the launcher
requires an exact binary hash plus a successful control-plane poll before it is
reported ready.

## One-time installation

Run this in a normal PowerShell window. Enter a **Runtime API key**, never an
Admin key, at the masked prompt:

```powershell
& "F:\test codex\plugins\evidence-lane-plugin\scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1" -MigrateCurrentRuntime
```

`-MigrateCurrentRuntime` stops only the process identified by the historical
PID file after its binary SHA-256 matches the pinned client, then starts the
scheduled copy. Omit the switch to leave a healthy interactive tunnel running
until the next sign-in.

## Operator commands

Start or confirm the task:

```powershell
& "C:\Users\rathe\EvidenceLanePV\tunnel-runtime\Manage-EvidenceLaneTunnel.ps1" -Action Start
```

Read status without exposing the key:

```powershell
& "C:\Users\rathe\EvidenceLanePV\tunnel-runtime\Manage-EvidenceLaneTunnel.ps1" -Action Status
```

Repair a stopped or unhealthy task:

```powershell
& "C:\Users\rathe\EvidenceLanePV\tunnel-runtime\Manage-EvidenceLaneTunnel.ps1" -Action Repair
```

The status contract is `PASS` only when the scheduled task exists, the pinned
binary hash matches, its PID is live, and `tunnel-client health` observes a
successful control-plane poll. The scripts never print or write the plaintext
Runtime API key.

`DELTA079B_WINDOWS_TUNNEL_PERSISTENCE_RECEIPT.json` is point-in-time
installation evidence, not a permanent claim that its recorded PID or local
health port remains current. Its top-level `receipt_sha256` seals canonical JSON
with only that field removed. Live readiness must always be obtained from the
Status command; the v1.3 receipt-seal regression test rejects a stale seal.

# Persistent versioned Windows secure MCP tunnel

The governed Windows tunnel uses the official OpenAI `tunnel-client` v0.0.10
binary pinned by SHA-256. The installer copies it out of the temporary download
directory, asks for the contributor's OpenAI Tunnel ID once, encrypts the
Runtime API key with current-user Windows DPAPI, creates an exact
versioned secure MCP transport profile, and registers `EvidenceLane-Tunnel-v200`
with Task Scheduler. The transport itself is host-neutral. Its currently served
remote layer is `CHATGPT_PRO_GOVERNED`; that layer is not the tunnel's identity.
Codex installs the exact plugin and keeps the complete local lifecycle on its
package-local native MCP. A tunnel surface is never accepted as Codex lifecycle
proof or as a fallback for missing native tools.

The tunnel is transport-only and binds no project. Its child process receives a
portable `EVIDENCE_LANE_DATA_ROOT` (the per-user `EvidenceLanePV` directory by
default), while every project-scoped MCP call must supply the exact `project_id`.
The server then resolves only `<data-root>/projects/<project_id>` and permits no
cross-project fallback. Use `-DataRoot <durable-directory>` to select another
reviewed, secret-free local root during installation.

The stable version starts automatically at the user's first Windows sign-in
after boot. A separately registered future-test version can run at the same time
under its own RuntimeRoot, profile, task, Tunnel ID, PID, health file, and public
route. It cannot stop or replace stable while it is being tested. The prior
proven stable is retained intact as the disabled archive/fallback. Current-user
DPAPI avoids a plaintext or machine-wide Runtime API key. It does not run before
any user has signed in. Task Scheduler restarts a selected daemon after non-zero
exits, and the launcher requires an exact binary hash plus a successful
control-plane poll before it is reported ready.

The v2.0.0 defaults are deliberately isolated under `tunnel-runtime-v200`,
`evidence_lane_v200_transport`, and `EvidenceLane-Tunnel-v200`. They do not
remove or overwrite the v1.3 or v1.4 runtime, profile, scheduled task, DPAPI
envelope, PID, health, or log files. Each install is registered in the
secret-free `EvidenceLanePV/tunnel-versions/registry.json`. The version manager
maintains explicit `stable`, `future-test`, and `archive/fallback` channels in an
append-only hash-chained history. Candidate readiness is proved before the
stable task is touched. Promotion additionally requires exact health, public
route, and host-proof receipt hashes. A failed future candidate is stopped while
stable remains untouched; rollback-after-interruption is not the safety model.

## One-time installation

Create one tunnel in the OpenAI Platform first. Run the installer from the
reviewed plugin checkout in a normal PowerShell window. Paste the resulting
`tunnel_...` identifier once, then enter a **Runtime API key**, never an Admin
key, at the masked prompt:

```powershell
& ".\plugins\evidence-lane-plugin\scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1"
```

Installation stages and registers v2.0 without stopping the active older
version. To reuse the current user's already encrypted Runtime key without ever
decrypting or printing it, pass the exact saved DPAPI envelope:

```powershell
& ".\plugins\evidence-lane-plugin\scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1" `
  -RuntimeKeyEnvelopeSource "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v130\secrets\control-plane-runtime-key.dpapi"
```

Never give a contributor the owner's Runtime API key or production OAuth
credential. A private tester receives a separately created Tunnel ID and
Runtime key, a unique `-RuntimeRoot`, `-ProfileName`, and `-TaskName`, and a
tester-only `-DataRoot` containing only the approved test projects. For example:

```powershell
& ".\plugins\evidence-lane-plugin\scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1" `
  -RuntimeRoot "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200-tester-01" `
  -ProfileName "evidence_lane_v200_transport_tester_01" `
  -TaskName "EvidenceLane-Tunnel-v200-Tester-01" `
  -DataRoot "$env:USERPROFILE\EvidenceLanePV\tester-01"
```

The installer requests that tester tunnel's own Runtime key through a masked
local prompt. The private tunnel remains read-only at the ChatGPT exposure
boundary. Owner-only production lifecycle authority is enforced separately by
the public OAuth resource server and must never be inferred from tunnel access.

The generated layer launcher sets `EVIDENCE_LANE_MCP_EXPOSURE_PROFILE` to
`CHATGPT_PRO_GOVERNED` for the registered ChatGPT layer currently served by the
transport. That profile exposes the complete 62-action catalog.
Exactly 21 annotated read operations execute for accepted-PV status, Entry/Exit
slips, ENV/UOP Flash, lane search/fetch, diffs, task backlog, and governed panels.
The other 41 lifecycle-write actions remain visible but are intercepted before
service invocation and return `UNAVAILABLE_ON_CHATGPT_PRO` with no mutation.
ChatGPT's native ENV/UOP and Project Mutation workflow remains a separate
host-owned capability; the MCP never claims to perform that mutation.

The installation marker and Status output report `project_binding` as
`NONE_TRANSPORT_ONLY`, the required `project_id` argument, the configured data
root, and `cross_project_fallback_allowed=false`. Those fields are routing proof;
they do not authorize a project, HIL decision, storage change, or lifecycle
mutation.

After installation, add or reconnect Evidence Lane once in ChatGPT using the
same Tunnel ID. Upload the shipped `assets/evidence-lane-icon.png` when the
ChatGPT development form requests the app icon. The server metadata reports
Evidence Lane `2.0.0`, the owned website, and the same public 256-by-256 icon.

Installation registers v2.0.0 as `future-test`. Without `-Activate`, it remains
staging-only. `-Activate` is permitted only when the caller also supplies the
three sealed promotion receipt hashes. The installer first runs
`VerifyCandidate`, which starts v2.0.0 without stopping stable, and then calls
`Promote`. Missing or invalid receipts block promotion and leave stable
authoritative.

## Operator commands

List every saved version and all three channels:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-versions\Manage-EvidenceLaneTunnelVersions.ps1" -Action List
```

Start and prove an isolated candidate without reinstalling it:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-versions\Manage-EvidenceLaneTunnelVersions.ps1" `
  -Action VerifyCandidate -Release "2.0.0"
```

Promote only after the exact candidate health, public route, and host proofs are
sealed:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-versions\Manage-EvidenceLaneTunnelVersions.ps1" `
  -Action Promote `
  -Release "2.0.0" `
  -HealthReceiptSha256 "<64-HEX-SHA256>" `
  -PublicRouteReceiptSha256 "<64-HEX-SHA256>" `
  -HostProofReceiptSha256 "<64-HEX-SHA256>"
```

`-Action Activate` may restart the current stable release or explicitly
reactivate the proven archive/fallback. It cannot promote a future-test release.
Saved v1.4.0 or v1.3.0 runtimes remain reusable, but a legacy archive without
sealed prior promotion receipts must be reverified and promoted with fresh
receipts rather than trusted by filename.

## Interaction and VM lifetime

The installer and SessionStart hook classify the host before requesting tunnel
setup:

- `HEADLESS_API` and `DIRECT_CLI_API` require no tunnel at the API layer,
  regardless of Pro, Plus, Business, Edu, Enterprise, or API billing. Durable
  local/mounted PV storage remains primary when present.
- `CODEX_APP_INTERACTIVE` on a local PC or persistent VM installs once per host
  and release. Windows logon management then keeps the transport available.
- `CODEX_APP_INTERACTIVE` on an ephemeral VM installs once for that VM. The
  Runtime key stays only in that VM's current-user DPAPI profile, and both the
  key envelope and tunnel lifetime end with the VM. The installer requires the
  current VM instance identity, stores only its SHA-256, refuses a durable prior
  key envelope, and keeps its tunnel-version registry under the VM-local runtime
  root even when PV state uses a separate durable mount.

If the pinned tunnel client is absent, the installer can acquire it only from a
configured credential-free HTTPS URI and only when its SHA-256 matches. It then
guides first-time Tunnel ID/key entry locally. The plugin never places a secret
in a prompt, receipt, task panel, database, Git file, or log. A missing or
mismatched marker produces visible onboarding; startup does not silently claim
tunnel health or mutate the tunnel.

Start or confirm only the v2.0 task directly:

This starts the v2.0.0 scheduled copy of the host-neutral secure transport; it
does not make the tunnel a Codex lifecycle route or a ChatGPT identity.

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200\Manage-EvidenceLaneTunnel.ps1" -Action Start
```

Read status without exposing the key:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200\Manage-EvidenceLaneTunnel.ps1" -Action Status
```

Repair a stopped or unhealthy task:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200\Manage-EvidenceLaneTunnel.ps1" -Action Repair
```

Stop v2.0 while preserving all files for later fallback:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200\Manage-EvidenceLaneTunnel.ps1" -Action Stop
```

Remove the exact installer-owned scheduled task, profile, DPAPI envelope, and
runtime only after reviewing the bound path:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200\Manage-EvidenceLaneTunnel.ps1" -Action Remove -ConfirmRemoval
```

Removal fails closed unless the target is inside the current user's
`EvidenceLanePV` directory and its installation marker binds the exact runtime
root and scheduled-task name.

The status contract is `PASS` only when the scheduled task exists, the pinned
binary hash matches, its PID is live, and `tunnel-client health` observes a
successful control-plane poll, and the generated profile points to the exact
governed child launcher. The scripts never print or write the plaintext
Runtime API key. Task Scheduler restarts it after sign-in and `Repair` recovers
an unhealthy verified instance without creating a second live daemon.

`DELTA079B_WINDOWS_TUNNEL_PERSISTENCE_RECEIPT.json` is point-in-time
installation evidence, not a permanent claim that its recorded PID or local
health port remains current. Its top-level `receipt_sha256` seals canonical JSON
with only that field removed. Live readiness must always be obtained from the
Status command; the v1.3 receipt-seal regression test rejects a stale seal.

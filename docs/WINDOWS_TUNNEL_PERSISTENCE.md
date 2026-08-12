# Persistent Windows tunnel and two-slot recovery

Evidence Lane 2.0 keeps the Codex lifecycle on the package-local native MCP.
The Windows tunnel is a separate, version-bound interactive-host support
channel. Tunnel health never substitutes for the native 62-tool catalog,
project/session binding, accepted pointer, or HIL proof.

The installer pins the OpenAI `tunnel-client` v0.0.10 binary by SHA-256, asks
for the user's Tunnel ID once, encrypts the Runtime API key with current-user
Windows DPAPI, and registers an exact scheduled task. It never prints or stores
the plaintext key in source, prompts, receipts, Plan rows, SQLite, Git, or logs.

## Host routing

| Host profile | PV storage | Tunnel rule |
| --- | --- | --- |
| Interactive Codex on a local PC or persistent VM | Durable local SQLite | Install once; the selected slot starts at Windows sign-in |
| Interactive Codex on an ephemeral VM | Durable mount or explicit transactional connector | Install for that VM lifetime only |
| Codex CLI or headless API on a local/persistent host | Durable local SQLite when available | Not required at the API layer |
| Headless API on an ephemeral VM | Durable mount or explicit transactional connector | Not required at the API layer |

Account tier and API billing do not select storage or tunnel routing. Every
project-scoped native call still supplies the exact `project_id`; cross-project
fallback is forbidden.

## First-time setup

From a reviewed checkout, run:

```powershell
& ".\plugins\evidence-lane-plugin\scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1" `
  -SlotRole stable-build `
  -InteractionProfile CODEX_APP_INTERACTIVE `
  -HostLifetime Persistent `
  -Activate
```

Paste the `tunnel_...` ID and the user's own Runtime API key at the masked
prompts. Never use an Admin key or another user's credential. For an ephemeral
VM, select the ephemeral host lifetime and supply that VM's exact instance ID;
the DPAPI envelope and tunnel runtime end with that VM.

Verify the installed runtime without exposing credentials:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200-stable-build\Manage-EvidenceLaneTunnel.ps1" `
  -Action Status `
  -RuntimeRoot "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200-stable-build" `
  -ProfileName evidence_lane_v200_stable_build_transport `
  -TaskName EvidenceLane-Tunnel-v200-stable-build
```

`PASS` requires the scheduled task, exact client path and SHA-256, one live PID,
and a successful control-plane poll. `Repair` may restart the same verified
runtime. `Stop` preserves it for later reuse. `Remove -ConfirmRemoval` is
allowed only for an installer-marked runtime inside the user's
`EvidenceLanePV` directory.

## Exact two-slot law after PV11 acceptance

The live Codex registry and live cache contain exactly two Evidence Lane slots
only after the user supplies exact standalone `APPROVE` and native Fuse accepts
PV11:

1. `stable-build` — enabled; receives each verified unique v2 build.
2. `fallback` — disabled; the byte-exact accepted PV11 package. It is installed,
   marker-verified, and stopped, so it is ready without running a second MCP
   server or tunnel.

“Prewarmed” means installed, fully verified, and stopped. It does not mean both
tunnels run. At most one Evidence Lane plugin/MCP and one matching tunnel may be
active. Older live registrations and caches are removed through supported Codex
plugin commands only. Immutable Git history, accepted PVs, packages, receipts,
Deltas, and evidence remain preserved outside the live cache.

The two-slot registry is keyed by exact plugin selector, build identity, package
SHA-256, installation receipt, cache root, tunnel marker, task name, and profile
name. It is not keyed only by semantic version, because stable-build and the
PV11 fallback are both product version 2.0.0.

## Deterministic failover operator

`scripts/codex_release/Switch-EvidenceLaneCodexSlot.ps1` composes the saved
tunnel managers with `Restart-EvidenceLaneCodex.ps1`.

Failover may be prepared in either of two ways:

- an explicit operator action with `-ConfirmExplicitOperator`; or
- a sealed stable-health receipt proving at least three consecutive failures
  across at least 30 seconds and at least two distinct probe types.

One transient error cannot trigger an automatic switch. Returning to
stable-build requires a sealed repair receipt proving the package, installed
bytes, native catalog, and clean CI.

The switch order is fixed:

1. validate the exact two-slot registry, config, package/install receipts, and
   tunnel markers;
2. stop the source tunnel and prove zero active tunnels;
3. start the target tunnel and prove it is the sole ready tunnel;
4. atomically enable only the target plugin and target MCP section;
5. prepare the controlled restart against the target installation receipt;
6. restart Codex at the exact `codex://threads/<task-id>` deep link;
7. after restart, prove the native catalog and exact project/session binding.

If any step before restart fails, the operator restores the original config and
source tunnel and writes a secret-free failure receipt. It never creates or
accepts a candidate, moves a pointer, or infers HIL.

Prepare an explicit fallback switch (paths and SHA values are examples only):

```powershell
& ".\plugins\evidence-lane-plugin\scripts\codex_release\Switch-EvidenceLaneCodexSlot.ps1" `
  -Action Prepare `
  -TargetSlot fallback `
  -Registry "<sealed-two-slot-registry.json>" `
  -RegistrySha256 "<64-HEX-SHA256>" `
  -ProjectId "<project-id>" `
  -EvidenceSessionId "<session-id>" `
  -TaskId "<codex-thread-uuid>" `
  -HostSessionId "<host-session-id>" `
  -Reason EXPLICIT_OPERATOR_FAILOVER `
  -TargetProcessId <root-codex-process-id> `
  -ConfirmExplicitOperator
```

Inspect the returned preparation receipt, then run `-Action Switch` with its
exact path/SHA and `-ConfirmSwitch`. The operator does not accept secrets on the
command line. Post-restart native proof remains mandatory before the target is
called healthy.

Historical version-manager events remain immutable evidence. The earlier
three-channel stable/future-test/archive model is not executable live-slot
authority after the PV11 two-slot registry is materialized.

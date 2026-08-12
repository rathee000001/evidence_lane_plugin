# Evidence Lane plugin 2.0.0

Version 2.0.0 is the stable-build Codex slot. It provides a
package-local native MCP server, 62 canonical actions (21 read-only and 41
write-capable), 15 governed skills, four registered hook events, local durable
project storage, persistent Plan/Delta continuity, and an exact six-way HIL.

Candidate creation, remote Git push, package installation, and pointer movement
are separate governed operations. None of them implies acceptance. Only exact
case-sensitive `APPROVE` at the correct HIL can authorize Fuse.

## Codex host and storage matrix

| Execution profile | Primary PV storage | Evidence Lane tunnel |
| --- | --- | --- |
| Interactive Codex app on a local or persistent host | Durable local SQLite | One-time versioned Windows channel; separate from the native Codex lifecycle |
| Codex CLI or headless API on a local/persistent host | Durable local SQLite when available | Not required |
| Headless API on an ephemeral VM with durable mount | Durable mounted SQLite | Not required |
| Headless API on an ephemeral VM without durable mount | Explicit transactional durable connector | Not required |
| Interactive Codex app on an ephemeral VM | Durable mount or explicit transactional connector | One setup per VM lifetime; never reused by a replacement VM |

Account tier and API billing do not choose the storage route. Runtime state is
project-scoped and remains separate from any optional artifact mirror.

## First-time Windows tunnel setup

From a reviewed source checkout, run the supported installer in PowerShell:

```powershell
& ".\plugins\evidence-lane-plugin\scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1" `
  -SlotRole stable-build `
  -InteractionProfile CODEX_APP_INTERACTIVE `
  -HostLifetime Persistent `
  -Activate
```

Paste the `tunnel_...` ID and then the user's own Runtime API key at the masked
prompt. The key is never printed or stored as plaintext; only a current-user
DPAPI envelope is retained. The installer registers the versioned Windows
sign-in task but never activates the disabled fallback slot. After
governed activation, prove readiness with:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200-stable-build\Manage-EvidenceLaneTunnel.ps1" `
  -Action Status `
  -RuntimeRoot "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v200-stable-build" `
  -ProfileName evidence_lane_v200_stable_build_transport `
  -TaskName EvidenceLane-Tunnel-v200-stable-build
```

The result is acceptable only when it reports `status = PASS`. Codex lifecycle
proof still comes from the package-local native `mcp__evidence_lane__*` route;
the tunnel is a separate transport channel. Headless API and direct CLI/API
profiles do not require this tunnel. See the
[complete setup, activation, repair, and removal guide](../../docs/WINDOWS_TUNNEL_PERSISTENCE.md).

## Package map

- `.codex-plugin/plugin.json` — Codex product and host metadata.
- `.mcp.json` — package-local native MCP launch contract.
- `src/evidence_lane_plugin/` — lifecycle engine and native server.
- `skills/` — fifteen governed skills.
- `hooks/` — four registered events and four handlers across five package files.
- `scripts/codex-release-channel.json` — v2 release and Git policy.
- `scripts/codex_release/` — deterministic build/install, controlled restart,
  two-slot failover, and installed-package acceptance checks.

The Codex archive excludes site source, evidence directories, generated app
namespaces, and host-connection metadata.

## Install, activate, and verify

1. Build the archive from one exact source commit and verify the source,
   commit, tree, package, catalog, skill, hook, and secret seals.
2. Run `scripts/codex_release/install_codex_stable.py` without activation to
   stage the local marketplace, then with activation to use Codex's supported
   plugin commands. The installer never writes the generated cache directly.
3. Run `scripts/codex_release/accept_codex_stable.py` before restart. A
   pre-restart receipt proves bytes only; it is not installed-host HIL.
4. Run `scripts/codex_release/Restart-EvidenceLaneCodex.ps1` with the exact
   project, Evidence Lane session, Codex task UUID, host session, installation
   receipt, and verified root `ChatGPT (Beta)` Codex process. The helper
   activates only `OpenAI.CodexBeta_2p2nqsd0c76g0!App` and passes it the same
   task's `codex://threads/<task-id>` deep link. It does not use another task,
   modify the task's native workspace binding, or implement Codex's Changes UI.
5. After restart, verify native route identity, 62/21/41 counts, all 15 skills,
   hook execution, icon, project/runtime panels, persistent task/change display,
   native Git workspace and Changes surface, and local durable storage from the
   installed package.
6. Stop at the explicit six-way HIL.

## Stable-build and accepted-PV11 fallback

After exact standalone `APPROVE` and native Fuse accepts PV11, the supported
live installation is normalized to exactly two slots: the enabled mutable
`stable-build` slot and a disabled byte-frozen `fallback` slot containing the
exact accepted PV11 package. “Prewarmed” means the fallback plugin and tunnel
are installed and verified but stopped; two MCP servers or two tunnels never
run together.

`scripts/codex_release/Switch-EvidenceLaneCodexSlot.ps1` accepts either an
explicit operator failover or a sealed multi-probe stable failure. It rejects a
single transient error, stops the source tunnel before starting the target,
enables only the matching plugin/MCP, and delegates the exact-task relaunch to
`Restart-EvidenceLaneCodex.ps1`. A repaired stable build must pass package,
installed-byte, native-catalog, and clean-CI checks before the reverse switch.
Any pre-restart failure restores the original slot. Native catalog and exact
project/session proof are mandatory again after restart.

## Persistent Plan and change display

The task panel and linked change status are one durable pair. `SessionStart`
rehydrates the exact task binding, `UserPromptSubmit` binds the visible turn,
`PostToolUse` reprojects the active task/Delta display after relevant native
actions, and `Stop` preserves the exit boundary without inventing a decision.

The change display reports the active task, linked Delta IDs, source/install/
runtime version, activation and Refresh state, catalog counts, hook inventory,
and skill inventory. Codex owns the final UI placement, so visible placement and
the Evidence Lane icon remain installed-host HIL observations.

## Git boundary

The configured non-default test branch may use standing project authorization
for exact fast-forward pushes through the native prepare/execute route. Each
push still seals the project, branch, commit, tree, remote, and action ID. This
does not authorize force-push, default-branch mutation, merge, publication,
candidate acceptance, pointer movement, or Fuse.

## Release boundary

Historical 1.x commits, packages, PVs, and receipts remain immutable evidence;
they are not extra live slots. A changed v2 source tree receives a fresh
collision-free build identity, a new deterministic package, one coherent CI
cycle, a supported reinstall/restart, and a new installed-host HIL.

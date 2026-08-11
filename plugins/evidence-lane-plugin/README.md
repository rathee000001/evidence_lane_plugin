# Evidence Lane plugin 2.0.0

Version 2.0.0 is the stable Codex slot. It provides a
package-local native MCP server, 62 canonical actions (21 read-only and 41
write-capable), 15 governed skills, four registered hook events, local durable
project storage, persistent Plan/Delta continuity, and an exact six-way HIL.

Candidate creation, remote Git push, package installation, and pointer movement
are separate governed operations. None of them implies acceptance. Only exact
case-sensitive `APPROVE` at the correct HIL can authorize Fuse.

## Codex host and storage matrix

| Execution profile | Primary PV storage | Evidence Lane tunnel |
| --- | --- | --- |
| Interactive Codex app on a local or persistent host | Durable local SQLite | Not required |
| Codex CLI or headless API on a local/persistent host | Durable local SQLite when available | Not required |
| Headless API on an ephemeral VM with durable mount | Durable mounted SQLite | Not required |
| Headless API on an ephemeral VM without durable mount | Explicit transactional durable connector | Not required |
| Interactive Codex app on an ephemeral VM | Durable mount or explicit transactional connector | Not supplied by this package |

Account tier and API billing do not choose the storage route. Runtime state is
project-scoped and remains separate from any optional artifact mirror.

## Package map

- `.codex-plugin/plugin.json` — Codex product and host metadata.
- `.mcp.json` — package-local native MCP launch contract.
- `src/evidence_lane_plugin/` — lifecycle engine and native server.
- `skills/` — fifteen governed skills.
- `hooks/` — four registered events and four handlers across five package files.
- `scripts/codex-release-channel.json` — v2 release and Git policy.
- `scripts/codex_release/` — deterministic build/install, controlled restart,
  and installed-package acceptance checks.

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
   receipt, and verified root Codex process. The helper relaunches the same task
   through its `codex://threads/<task-id>` deep link.
5. After restart, verify native route identity, 62/21/41 counts, all 15 skills,
   hook execution, icon, project/runtime panels, persistent task/change display,
   and local durable storage from the installed package.
6. Stop at the explicit six-way HIL.

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

Historical 1.5.0 commits, packages, caches, PVs, and receipts remain immutable
archive evidence. A changed v2 source tree receives a fresh collision-free
build identity, a new deterministic package, one coherent CI cycle, a supported
reinstall/restart, and a new installed-host HIL.

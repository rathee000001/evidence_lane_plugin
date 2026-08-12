# Evidence Lane 2.0 local Codex installation and reload

Evidence Lane 2.0 uses Codex's supported local-marketplace path. It never edits
`~/.codex/plugins/cache` directly. Before PV11 acceptance, cleanup is deferred.
After exact standalone `APPROVE` and native Fuse, supported plugin management
normalizes the live registry/cache to the enabled stable-build slot and the
disabled byte-exact PV11 fallback slot; immutable evidence remains outside the
live cache.

The official Codex plugin authoring documentation describes local/repository
marketplaces, `codex plugin marketplace add`, `codex plugin add`, and a desktop
restart after a local plugin changes:
<https://developers.openai.com/codex/plugins/build?install-scope=user>.

## Sealed installation sequence

1. Build the deterministic non-lifecycle rehearsal archive and receipt with
   `scripts/build_release_candidate_rehearsal.py`.
2. Run `scripts/codex_release/install_codex_stable.py` first without
   `--activate`. It verifies the archive, exact v2 manifest, 62/21/41 catalog
   contract, fifteen skills, host separation, and prior-release retention. For
   an update, pass the exact prior host-loaded stable installation receipt and
   its SHA-256 as the comparison baseline; a staged-but-never-restarted cache
   can never silently become the baseline. Legacy receipts that counted hook
   files are enriched only from the one archived marketplace whose archive SHA
   and every hook/skill file hash reproduce that sealed receipt.
3. Rerun it with `--activate` and the exact Codex executable. The script stages
   `evidence-lane-v200-github`, invokes the supported marketplace/plugin CLI,
   disables other Evidence Lane selectors without deleting their caches, backs
   up `config.toml`, preserves the exact preflight surface-change display rather
   than comparing the staged package with itself, and emits an
   `INSTALLED_RESTART_REQUIRED` receipt.
4. Run `scripts/codex_release/accept_codex_stable.py` without a native-route
   receipt. It must return
   `PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED` after comparing
   every marketplace source byte, the exact Codex-generated command-to-skill
   migration, the enabled selector, the actual AST catalog, hooks, skills,
   release policy, and the sealed installation-surface diff. No other cache
   extras are accepted.
5. Run `Restart-EvidenceLaneCodex.ps1 -Action Prepare` with the exact project,
   Evidence Lane session, Codex task, host session, installation receipt, and
   root Codex desktop process ID. Prepare also writes one sealed exact-thread
   binding under the durable v2 installation root; this lets the task use a
   separate Codex task-shell workspace without falling back from CWD or title.
6. Inspect the preparation receipt. Invoke `-Action Restart` only with its exact
   SHA-256 and `-ConfirmRestart`. The hidden helper stops only that exact root
   process and opens the exact `codex://threads/<task-id>` route in the packaged
   desktop app.
7. The SessionStart or first eligible PostToolUse hook verifies that exact task
   receipt and seals the Codex thread-to-governed-session alias. It then
   rebinds the persistent task panel and linked change display. It does not call State Travel or
   `session_resume`, does not infer HIL, and does not claim a hot reload.
8. Supply a fresh native-route receipt to the installed acceptance checker. It
   must return `POST_RESTART_INSTALLED_PACKAGE_VERIFIED_READY_FOR_HIL`; a
   pre-restart receipt is never final installed-host proof.

Every installation update repeats package verification, host installation,
restart, native catalog readback, focused tests, and final installed-package
HIL. A source-tree test alone is not installed-package proof.

## Two-slot recovery

The packaged `Switch-EvidenceLaneCodexSlot.ps1` operator is inactive until a
sealed post-Fuse two-slot registry proves both installed slots. On explicit
operator failover or a sealed multi-probe stable failure it stops the stable
tunnel, starts and verifies the fallback tunnel, enables only the fallback
plugin/MCP, and invokes the same exact-task restart helper. Returning to stable
requires a sealed repair proof. A single transient error is rejected. Any
failure before restart restores the source slot. After restart, native catalog
and project/session binding must be proved again.

## Persistent Plan and change display

The full sealed display is regenerated at SessionStart, PREPARE, eligible
PostToolUse events, and COMMIT. PostToolUse covers ongoing-Goal Plan/Delta and
source-change updates even when Codex does not produce a fresh
UserPromptSubmit event. At these boundaries the hook emits a bounded
`systemMessage` warning containing the active Plan position and task ID, the
latest linked Delta identity, the linked-Delta set seal, source-change counts,
the full-display seal, source/install/runtime versions, activation and Refresh
state, matching tunnel channel, exact catalog counts, and added/changed/removed
hook and skill names from the sealed installation-surface diff. Raw Delta text,
private research questions, private reasoning, and changed-path names are
excluded from that warning.

The installed inventory reports hook concepts separately: four registered
events and handlers, five package files including `hooks.json`, and the exact
event names. It does not label a configuration file as an additional hook.
The skill count remains fifteen until a genuinely distinct workflow is added;
version changes alone do not manufacture a new skill.

Each installation keeps an immutable archive-hash receipt and refreshes only
the plugin-owned derived `CURRENT_INSTALLATION.json` pointer. Prior marketplace
bytes are archived rather than deleted. If the current package inventory does
not match that sealed installation receipt, startup reports the receipt as
unavailable or stale rather than fabricating a change list.

Codex owns the warning's exact visual placement. Evidence Lane requests the
near-composer system-message surface and proves the emitted contract, but it
does not claim to edit or persist the prompt composer. Exact placement above
the prompt bar remains an installed-host HIL observation.

## Refresh behavior

Codex freezes plugin capabilities for a running task. Evidence Lane therefore
does not claim that changing marketplace files refreshes the current process.
The installer emits a restart boundary. After restart, every new or reopened
task gets the new hooks, skill inventory, native MCP snapshot, active Plan row,
and linked Delta/change display from the installed v2 package.

The restart helper preserves source, Git, PVs, candidate state, pointer state,
and HIL state. It only changes the desktop process. If the exact receipt,
process, task binding, app identity, or installed version differs, it stops
before process termination.

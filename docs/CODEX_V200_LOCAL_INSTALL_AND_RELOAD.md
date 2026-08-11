# Evidence Lane 2.0 local Codex installation and reload

Evidence Lane 2.0 uses Codex's supported local-marketplace path. It never edits
`~/.codex/plugins/cache` directly and never treats the immutable 1.5 package as
a working directory. The v1.5 cache and marketplace remain archive evidence;
only their enabled configuration is switched off after the v2 install succeeds.

The official Codex plugin authoring documentation describes local/repository
marketplaces, `codex plugin marketplace add`, `codex plugin add`, and a desktop
restart after a local plugin changes:
<https://developers.openai.com/codex/plugins/build?install-scope=user>.

## Sealed installation sequence

1. Build the deterministic non-lifecycle rehearsal archive and receipt with
   `scripts/build_release_candidate_rehearsal.py`.
2. Run `scripts/codex_release/install_codex_stable.py` first without
   `--activate`. It verifies the archive, exact v2 manifest, 62/21/41 catalog
   contract, fifteen skills, host separation, and prior-release retention.
3. Rerun it with `--activate` and the exact Codex executable. The script stages
   `evidence-lane-v200-github`, invokes the supported marketplace/plugin CLI,
   disables other Evidence Lane selectors without deleting their caches, backs
   up `config.toml`, and emits an `INSTALLED_RESTART_REQUIRED` receipt.
4. Run `scripts/codex_release/accept_codex_stable.py` without a native-route
   receipt. It must return
   `PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED` after comparing
   marketplace/cache bytes, the enabled selector, the actual AST catalog,
   hooks, skills, release policy, and the sealed installation-surface diff.
5. Run `Restart-EvidenceLaneCodex.ps1 -Action Prepare` with the exact project,
   Evidence Lane session, Codex task, host session, installation receipt, and
   root Codex desktop process ID.
6. Inspect the preparation receipt. Invoke `-Action Restart` only with its exact
   SHA-256 and `-ConfirmRestart`. The hidden helper stops only that exact root
   process and relaunches the packaged desktop app.
7. Open the same Codex task. The SessionStart hook rebinds the persistent task
   panel and linked change display. It does not call State Travel or
   `session_resume`, does not infer HIL, and does not claim a hot reload.
8. Supply a fresh native-route receipt to the installed acceptance checker. It
   must return `POST_RESTART_INSTALLED_PACKAGE_VERIFIED_READY_FOR_HIL`; a
   pre-restart receipt is never final installed-host proof.

Every installation update repeats package verification, host installation,
restart, native catalog readback, focused tests, and final installed-package
HIL. A source-tree test alone is not installed-package proof.

## Persistent Plan and change display

The full sealed display is regenerated at SessionStart, PREPARE, and COMMIT. At
prompt submission and response completion, the hook also emits a bounded
`systemMessage` warning containing the active Plan position and task ID, the
latest linked Delta identity, the linked-Delta set seal, source-change counts,
the full-display seal, source/install/runtime versions, activation and Refresh
state, matching tunnel channel, exact catalog counts, and added/changed/removed
hook and skill names from the sealed installation-surface diff. Raw Delta text,
private research questions, private reasoning, and changed-path names are
excluded from that warning.

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

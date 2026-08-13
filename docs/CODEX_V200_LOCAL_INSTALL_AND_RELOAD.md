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
   `scripts/build_release_candidate_rehearsal.py`. This artifact is for local
   staging and tests only; its boundary explicitly forbids live activation.
2. Run `scripts/codex_release/install_codex_stable.py` first without
   `--activate`. It verifies the archive, exact v2 manifest, 62/21/41 catalog
   contract, fifteen skills, host separation, and prior-release retention. For
   an update, pass the exact prior host-loaded stable installation receipt and
   its SHA-256 as the comparison baseline; a staged-but-never-restarted cache
   can never silently become the baseline. Legacy receipts that counted hook
   files are enriched only from the one archived marketplace whose archive SHA
   and every hook/skill file hash reproduce that sealed receipt.
3. Commit the exact reviewed source on the one governed non-protected branch,
   push it through native governed remote Git, wait for every required GitHub
   CI check to pass at that exact SHA, and package the exact commit with
   `scripts/codex_release/build_codex_exact_commit_package.py`. Join that
   package receipt, the native remote-Git receipt, and the exact-head CI receipt
   with `scripts/codex_release/seal_codex_git_ci_release_authority.py`. The
   joiner seals those facts in an
   `evidence-lane.codex-git-ci-release-authority.v1` receipt bound to the
   archive and package receipt. Then rerun the installer with `--activate
   --trust-sealed-hooks`, `--release-authority-receipt`,
   `--release-authority-receipt-sha256`, the exact Codex executable, and
   `--hook-cwd` naming the governed project. The script stages the
   persistent stable v2 marketplace, invokes the supported marketplace/plugin
   CLI, removes obsolete disabled registrations, reinstalls the same stable
   selector, and backs up `config.toml`. A build hash is receipt identity, not a
   new marketplace, plugin, UI card, or cache slot. It then uses Codex's
   supported `hooks/list` and
   `config/batchWrite` APIs to trust only the four current hashes belonging to
   that exact installed selector. Any missing, extra, modified, disabled, or
   still-untrusted hook fails closed. Before `INSTALLED_RESTART_REQUIRED` is
   emitted, it bootstraps the installed cache, imports the native MCP runtime,
   prewarms native dependencies, and seals
   `runtime_ready_before_task_reopen=true`. A local rehearsal, incomplete CI,
   or failed prewarm cannot reopen the task.
   `Update-EvidenceLaneCodexStableAndResume.ps1` binds this gated installer path
   to the existing stable selector and reopens only the exact task after the
   installed runtime is ready. It never creates a build-named selector and
   never activates fallback implicitly.
4. Run `scripts/codex_release/accept_codex_stable.py` without a native-route
   receipt. It must return
   `PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED` after comparing
   every marketplace source byte, the exact Codex-generated command-to-skill
   migration, the enabled selector, the actual AST catalog, hooks, skills,
   release policy, and the sealed installation-surface diff. No other cache
   extras are accepted.
5. Run `Restart-EvidenceLaneCodex.ps1 -Action Prepare` with the exact project,
   Evidence Lane session, Codex task, host session, installation receipt, and
   root `ChatGPT (Beta)` Codex process ID. Prepare rejects the non-beta app and
   validates the exact `OpenAI.CodexBeta_2p2nqsd0c76g0!App` registration.
   Prepare also writes one sealed exact-thread binding under the durable v2
   installation root; this lets the task retain its existing native Git
   workspace without falling back from CWD or title.
6. Inspect the preparation receipt. Invoke `-Action Restart` only with its exact
   SHA-256 and `-ConfirmRestart`. The hidden helper stops only that exact root
   process, activates the exact Beta AppUserModelID, and passes it the exact
   `codex://threads/<task-id>` route. A new Beta root must be observed before a
   success receipt is written; otherwise a failure receipt is written.
7. The SessionStart or first eligible PostToolUse hook verifies that exact task
   receipt and seals the Codex thread-to-governed-session alias plus bounded
   lifecycle status. Hooks do not query PV authority, carry the full Plan Lane,
   or call host behavior. The active Evidence Lane skill then calls native
   `pv_status`, `pv_task_backlog`, and bounded `pv_query`, validates the Plan
   Lane, and calls host `update_plan` with the exact full panel. Codex separately
   restores its task-owned Git workspace and native Changes surface; Evidence
   Lane does not implement or overwrite that UI. It does not call State Travel
   or `session_resume`, does not infer HIL, and does not claim a hot reload.
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

## Windows reboot recovery for active Goals

Use `scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1 -Action
Register` after the exact task-binding receipt exists. Registration is general
to all governed Codex Goal tasks under the current Windows user; it is not tied
to this repository, one test project, or one stable build. The manager installs
one durable copy under
`EvidenceLanePV/installations/codex-v200/goal-recovery`, registers one current-
user `AtLogOn` task, and stores one sealed binding per exact Codex task UUID.

At logon the manager verifies the binding, two-slot invariant, persisted task,
and active Goal read-only, then requests the exact task deep link once for that
Windows boot. It does not call `thread/resume` as a second writer, start a turn,
submit a prompt, invoke State Travel, change source/Git, move the pointer, infer
HIL, or activate the fallback. The recovery receipt therefore says
`HOST_CONTINUATION_PENDING`: reopening a task is not proof that Codex already
ran the next model turn.

Run `-Action Status` for a non-mutating inventory. Run `-Action Unregister
-TaskId <uuid>` when a Goal ends; this preserves binding history and disables
the scheduled task only when no active Goal bindings remain.

## Persistent Plan and change display

The full sealed display is regenerated at SessionStart, PREPARE, eligible
PostToolUse events, and COMMIT. PostToolUse covers ongoing-Goal Plan/Delta and
source-change updates even when Codex does not produce a fresh
UserPromptSubmit event. At these boundaries the hook emits only a bounded
`systemMessage` warning containing the active Plan position and task ID, the
latest linked Delta identity, the linked-Delta set seal, source-change counts,
the full-display seal, source/install/runtime versions, activation and Refresh
state, matching tunnel channel, exact catalog counts, and added/changed/removed
hook and skill names from the sealed installation-surface diff. Raw Delta text,
private research questions, private reasoning, and changed-path names are
excluded from that warning.

Behavior remains skill-owned. After every visible prompt, Goal continuation,
correction, or steer, the active skill consumes the PREPARE receipt, performs
the real native `pv_status`, `pv_task_backlog`, and bounded `pv_query` reads,
then redraws all canonical Plan rows through host `update_plan`. Hook-side
SQLite lookup, a lifecycle warning, or an embedded full-row payload is not a
substitute and is prohibited.

All eight registered lifecycle events resolve one shared durable Evidence Lane
authority through six command handlers:
the explicitly configured `EVIDENCE_LANE_DATA_ROOT`, otherwise the user-owned
`~/EvidenceLanePV`. Codex injects `PLUGIN_DATA` separately for each plugin
selector; that directory is installation-private and is never project, session,
PV, PromptIndex, ChatLineage, Plan, or Delta authority. A stable or fallback
selector therefore cannot silently create a parallel Evidence Lane state tree.

The installed inventory reports hook concepts separately: eight registered
events, six command handlers, seven package files including `hooks.json`, and
the exact event names. It does not label a configuration file as an additional
hook.
The skill count remains fifteen until a genuinely distinct workflow is added;
version changes alone do not manufacture a new skill.

Installation and runtime activation are separate facts. An active governed
session means prompt/response capture is configured; `runtime_activation_status`
reports capture as actually active only when the current installation receipt
also proves the exact selector's eight trusted hook hashes. A changed build must
reuse that selector; selector growth or missing hook proof is a failure.

Each installation keeps an immutable archive-hash receipt and refreshes only
the plugin-owned derived `CURRENT_INSTALLATION.json` pointer. Prior build
identity remains in immutable receipts rather than live Codex registrations or
cache slots. If the current package inventory does
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
task gets the new hooks, skill inventory, and native MCP snapshot from the
installed v2 package. The skill—not SessionStart—then reads native PV authority
and restores the active Plan row plus linked Delta/change display.

The restart helper preserves source, Git, PVs, candidate state, pointer state,
and HIL state. It only changes the desktop process. If the exact receipt,
process, task binding, app identity, or installed version differs, it stops
before process termination.

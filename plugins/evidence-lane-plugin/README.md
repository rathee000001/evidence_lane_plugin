# Evidence Lane plugin 2.2.0

Version 2.2.0 is the current pre-HIL Codex source release on the governed v2.2
branch. Accepted Project Truth and the GitLane base remain PV12/2.1.0. Direct
host evidence showed the disabled fallback at historical PV11/main 2.0.0; this
source-only row neither repairs nor activates either installed slot. The source provides a
package-local native MCP server, 83 canonical actions (26 read-only and 57
write-capable), 17 governed skills, eight registered lifecycle events, local durable
project storage, persistent Plan/Delta continuity, and an exact six-way HIL.
Agent Learning is a separate project-scoped authority, not another name for
Project Truth or Canon. It seals evidence-backed candidates, records lifecycle
and decision events append-only, and moves only its own pointer after an exact
Learning HIL decision. Rejection, failure, expiry, revocation, supersession,
and rollback retain immutable history. Project Truth conflicts suppress a
lesson with a receipt; no Learning action can create a Project candidate,
invoke Project HIL, or move the accepted Project PV.
Canon is independently implemented as a typed project/task graph and bounded
input authority. Exact expected contracts auto-admit; undefined or incompatible
packets stop only the receiving top-level task at its three-way `ACCEPT`,
`REJECT`, or `MORE_RESEARCH` Canon Input HIL. Cross-task and cross-project
edges bind exact UUIDs/deep links, return contracts, replay identities, and
source PV seals. They never propagate source-write, Project/Learning HIL,
candidate, Fuse, or pointer authority. Subagents require current user authority
and cannot own HIL. See
[the Canon task graph contract](../../docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md).
The installed runtime prewarm and release validators bind the same governed
console resource, `ui://evidence-lane/governed-console-v5.html`, and fail closed
if that identity drifts.
The generated Python environment is a lock-digest/Python-ABI keyed projection
under `EvidenceLanePV/runtime/codex`, not inside Codex's reconstructable plugin
cache. The exact active stable or fallback slot remains source authority.

Hooks transport lifecycle only: SessionStart, UserPromptSubmit, PreToolUse,
PostToolUse, PreCompact, PostCompact, Stop, and best-effort SessionEnd. The
active skill owns native PV reads, classification, behavior, and the complete
Plan/CURRENT CHANGE projection. PermissionRequest remains unregistered unless
the host capability is positively proven; subagent hook events are out of scope.

Bounded repository search is package-owned for every governed project. The
Windows x86-64 package carries hash-pinned `ripgrep` 15.2.0 and `fzf` 0.74.2
executables plus their upstream license files. Runtime selection is strictly
package-local verified binary, then an explicitly configured absolute host
binary with an exact supplied SHA-256, then a deterministic Python fallback.
PATH lookup, shell execution, and download during MCP handshake are forbidden.
Every invocation is noninteractive, size/time bounded, secret-path filtered,
redacted, and receipt-backed; these helpers have no source-write, Git,
lifecycle, candidate, HIL, or pointer authority.

The private internal Codex SDK is the full engine-and-contract layer, not a
reduced retrieval wrapper. Its provider-neutral ABI keeps Project Truth, Canon
Input, AI/Agent Learning, ChatLineage, host-entry continuity, lifecycle/hooks,
Plan/Delta/tasks, source/lane retrieval, ENV/UOP plus Formula/PCM/MBA,
storage/connectors, HIL/candidate/pointer, and provider/host adapters in
independent namespaces and replay ledgers. Every call binds the exact
project/session/task/pointer/lineage/ENV-UOP/model/host/write scope. Unsupported
host operations fail explicitly, and Project Truth and Learning retrieval stay
separate. See [the internal SDK contract](../../docs/INTERNAL_CODEX_SDK.md).

The provider-neutral GitHub App boundary is also compiled pre-HIL: a packaged
least-privilege manifest schema, signature-before-parse webhook verifier,
replay-safe installation-token broker, check-run receipt mapper, and signed
tester-artifact entitlement flow. The deterministic adapter uses fixtures only;
it neither registers or installs an app nor stores credentials, grants private
development-repository access, distributes to external testers, publishes, or
promotes any Evidence Lane authority. See
[the pre-HIL GitHub App contract](../../docs/GITHUB_APP_PRE_HIL_CONTRACT.md).

Candidate creation, remote Git push, package installation, and pointer movement
are separate governed operations. None of them implies acceptance. Only exact
case-sensitive `APPROVE` at the correct HIL can authorize Fuse.

The current maintainer Row196 route binds the CI-green, Git-triggered-preview
commit from `agent/evi-v220-systemwide-release-hil-v2.2.0`. The immediate
Row197/PV13 install-HIL route installs that exact 2.2 package in the enabled
Stable selector and requires installed readback of 83 actions (26 read/57
write), 17 skills, eight hook events, and the migrated command surface before
the PV13 HIL is presented. It cannot merge main or change the disabled fallback.
That plugin release/install cadence is not part of an ordinary downstream
user's project PV workflow.

Only the human command `MARK GOAL COMPLETE` may complete a governed Goal, with
`COMPLETE_THIS_TASK_AND_STATE_TRAVEL` or `COMPLETE_FULLY`. HIL, candidate,
tests, Plan/task state, automation, pause, and stall have no Goal-completion
authority. Version-bound Goal-recovery helpers and required Stable tunnels are
user support surfaces; the release updater is maintainer-only. Older helper and
tunnel versions remain retained and disabled with one active version.

## Codex host and storage matrix

| Execution profile | Primary PV storage | Evidence Lane tunnel |
| --- | --- | --- |
| Interactive Codex app on a local or persistent host with the active `CODEX` surface proven | Durable local SQLite | Not required; package-local native MCP is the lifecycle route |
| Codex CLI or headless API on a local/persistent host | Durable local SQLite when available | Not required |
| Headless API on an ephemeral VM with durable mount | Durable mounted SQLite | Not required |
| Headless API on an ephemeral VM without durable mount | Explicit transactional durable connector | Not required |
| Interactive Codex app on an ephemeral VM | Durable mount or explicit transactional connector | One setup per VM lifetime; never reused by a replacement VM |

When storage is not durable, governed work starts only after one expiring,
secret-safe host-entry envelope is claimed exactly once by the intended task
and host binding. It carries hashes and receipts, not raw paths, credentials,
or private reasoning, and cannot promote Project Truth, accept Canon or Agent
Learning, replay HIL, or move the accepted pointer. Exact retries reuse the
prior receipt. A missing connector, stale generation, different worktree,
unauthorized candidate overlay, expired envelope, or different consumer fails
closed. Durable local Codex continues directly from local SQLite without this
envelope.

Account tier and API billing do not choose the storage route. Runtime state is
project-scoped and remains separate from any optional artifact mirror.

## Bounded Windows tunnel setup

Run this only after runtime classification explicitly selects the separate
interactive ephemeral-VM support channel. Stable/current and Beta desktop
containers are both multi-surface; Evidence Lane governs only a proven
`active_surface=CODEX`. A local durable Codex surface does not use a tunnel.

From a reviewed source checkout, run the supported installer in PowerShell for
the exact ephemeral VM:

```powershell
& ".\plugins\evidence-lane-plugin\scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1" `
  -SlotRole stable-build `
  -InteractionProfile CODEX_APP_INTERACTIVE `
  -HostLifetime Ephemeral `
  -VmInstanceId "<exact-vm-instance-id>" `
  -Activate
```

Paste the `tunnel_...` ID and then the user's own Runtime API key at the masked
prompt. The key is never printed or stored as plaintext; only a current-user
DPAPI envelope is retained. The installer registers the versioned Windows
sign-in task but never activates the disabled fallback slot. After
governed activation, prove readiness with:

```powershell
& "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v220-stable-build\Manage-EvidenceLaneTunnel.ps1" `
  -Action Status `
  -RuntimeRoot "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v220-stable-build" `
  -ProfileName evidence_lane_v220_stable_build_transport `
  -TaskName EvidenceLane-Tunnel-v220-stable-build `
  -ReleaseToken v220
```

The result is acceptable only when it reports `status = PASS`. Codex lifecycle
proof still comes from the package-local native `mcp__evidence_lane__*` route;
the tunnel is a separate transport channel. Local durable desktop, headless API,
and direct CLI/API profiles do not require this tunnel. See the
[complete setup, activation, repair, and removal guide](../../docs/WINDOWS_TUNNEL_PERSISTENCE.md).

## Package map

- `.codex-plugin/plugin.json` — Codex product and host metadata.
- `.mcp.json` — package-local native MCP launch contract.
- `src/evidence_lane_plugin/` — lifecycle engine and native server.
- `skills/` — seventeen governed skills.
- `hooks/` — eight registered events and sealed Windows/Python dispatch wrappers across nine package files.
- `toolchains/` — the governed `ripgrep`/`fzf` dependency manifest,
  hash-pinned Windows binaries, upstream licenses, and deterministic fallback
  contract used by all projects.
- `scripts/codex-release-channel.json` — v2 release and Git policy.
- `scripts/codex_release/build_codex_exact_commit_package.py` — read-only
  exact-commit package export that excludes dirty and untracked checkout bytes.
- `scripts/codex_release/seal_codex_git_ci_release_authority.py` — read-only
  join of the exact package, native governed push, successful exact-head CI,
  and the Git-integrated Vercel branch preview for that same commit.
- `scripts/codex_release/Update-EvidenceLaneCodexStableAndResume.ps1` — one
  canonical Git stable-slot update, installed-runtime prewarm, recovery-manager
  rebind, and exact-task reopen in the same ChatGPT stable or ChatGPT Beta
  desktop channel. Both desktop channels expose ChatGPT and Codex surfaces;
  Evidence Lane governs the Codex surface only.
- `scripts/codex_release/` — controlled restart, two-slot failover, Goal
  recovery, and installed-package acceptance checks.

The Codex archive excludes site source, evidence directories, generated app
namespaces, and host-connection metadata.

The packaged
`scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1` is the single
Windows-logon recovery manager for exact governed Codex Goal tasks.

## Install, activate, and verify

1. Build the archive from one exact source commit and verify the source,
   commit, tree, package, catalog, skill, hook, and secret seals.
2. A non-lifecycle local rehearsal may be staged and tested but cannot be
   activated. Push the exact governed branch through native remote Git, wait
   for the existing governed Python CI, CodeQL, and preview-build workflows at
   that same commit, wait for its Git-integrated Vercel branch preview, build
   the exact-commit package with `build_codex_exact_commit_package.py`, and seal
   the Git/CI/Vercel release-authority receipt with
   `seal_codex_git_ci_release_authority.py`.
3. Run `scripts/codex_release/install_codex_stable.py` without activation for a
   local rehearsal only. Activation uses Codex's Git marketplace at the exact
   successful commit and requires `--activate --trust-sealed-hooks`,
   `--release-authority-receipt`,
   `--release-authority-receipt-sha256`, the exact Codex executable, and the
   governed `--hook-cwd`. The installer uses
   Codex's supported plugin commands, `hooks/list`, and `config/batchWrite`; it
   trusts only the exact installed selector's eight current hook hashes and
   never writes the generated cache directly. The one permitted legacy update
   migrates the old mutable selector to
   `evidence-lane-plugin@evidence-lane-github`; every later update reinstalls
   that same persistent selector. The build hash is sealed in the receipt and
   never creates another plugin card or cache slot. Obsolete mutable selectors
   are removed only after the new route is installed, prewarmed, and read back.
   Installation fails
   closed if the hook inventory or post-write trust readback is not exact. It
   bootstraps and probes the durable derived native runtime before any task
   reopen; a warm protocol launch must not invoke pip again.
4. Run `scripts/codex_release/accept_codex_stable.py` before restart. A
   pre-restart receipt proves bytes only; it is not installed-host HIL.
5. Run `scripts/codex_release/Restart-EvidenceLaneCodex.ps1` with the exact
   project, Evidence Lane session, Codex task UUID, host session, installation
   receipt, and verified root Codex process. The helper accepts only the exact
   allowlisted stable `OpenAI.Codex_2p2nqsd0c76g0!App` or Beta
   `OpenAI.CodexBeta_2p2nqsd0c76g0!App` identity, reopens the same one that was
   bound at preparation, and passes it the same task's
   `codex://threads/<task-id>` deep link. It does not use another task,
   modify the task's native workspace binding, or implement Codex's Changes UI.
6. After restart, verify native route identity, 83/26/57 counts, all 17 skills,
   hook execution, icon, project/runtime panels, persistent task/change display,
   native Git workspace and Changes surface, and local durable storage from the
   installed package.
7. Stop at the explicit six-way HIL.

Hook command files own only validation, secret redaction, bounds,
deduplication identity, and transport-envelope sealing. The installed
lifecycle skill runtime consumes those exact envelopes and owns sealed
Entry/PREPARE/COMMIT and other lifecycle actions. The active skill then calls
native `pv_status`, `pv_task_backlog`, and bounded `pv_query` after every prompt
or steer and restores the full canonical Step Task List with host
`update_plan`. Hook adapters never embed that full panel or instruct the host
behavior tool. Every skill-owned lifecycle consumer uses the explicit
`EVIDENCE_LANE_DATA_ROOT` when configured and otherwise the user-owned durable
`~/EvidenceLanePV`; Codex-injected `PLUGIN_DATA` is selector-private
installation storage and cannot become project, PV, session, PromptIndex, or
ChatLineage authority.

### Project capture routes

Every governed project binds one capture route before its first lineage write.
`GOVERNED_PROJECT_FULL` retains the complete secret-redacted visible prompt,
steer, response, operational, source-link, and COMMIT stream with deterministic
chunks and FTS. `ENV_BUILDER_SPARSE` retains only accepted Deltas, hard gates,
schema decisions, receipts, governed artifacts, and valid lifecycle Exit Slips;
other visible units become hash-bound exclusion receipts and their raw text does
not enter JSONL, chunks, or FTS. The binding is project-scoped and immutable,
route or project ambiguity fails before ingestion, and a `source_project_id`
remains provenance rather than permission to cross the active project boundary.

## Stable-build and fallback identity boundary

The supported live topology is exactly two slots: an enabled mutable
`stable-build` slot and a disabled byte-frozen `fallback` slot. Their identities
must come from exact package, registry, cache, selector, and native readback—not
from this README. Direct host evidence showed fallback still at PV11/2.0 while
the accepted/base GitLane release had advanced to PV12/2.1; the later governed
split-brain row must reconcile that state. The pre-HIL 2.2 source does not
install, activate, or relabel either slot. “Prewarmed” means a fallback package
and tunnel are installed and verified but stopped; two MCP servers or two
tunnels never run together. Later stable builds update the same stable selector
in place; only the package/receipt identity changes.

`scripts/codex_release/Switch-EvidenceLaneCodexSlot.ps1` accepts either an
explicit operator failover or a sealed multi-probe stable failure. It rejects a
single transient error, stops the source tunnel before starting the target,
enables only the matching plugin/MCP, and delegates the exact-task relaunch to
`Restart-EvidenceLaneCodex.ps1`. A repaired stable build must pass package,
installed-byte, native-catalog, and clean-CI checks before the reverse switch.
Any pre-restart failure restores the original slot. Native catalog and exact
project/session proof are mandatory again after restart.

## Windows reboot recovery for active Goals

`Manage-EvidenceLaneCodexGoalRecovery.ps1` is one Windows-user manager for all
Evidence-Lane-governed Codex Goal tasks on that machine. `Register` consumes an
exact task-binding receipt, verifies the persisted active Goal through
`thread/read` plus `thread/goal/get`, prewarms the canonical plugin/MCP/resource
catalog in one hidden isolated official Codex app-server, seals only the
objective hash, and records
   the active Plan task, exact stable/fallback identities, and exact stable-or-
   Beta Codex host identity. One `AtLogOn` scheduled task enumerates those
   bindings and opens each exact `codex://threads/<task-id>` route in its bound
   host app at most once per Windows boot.

Recovery never calls `turn/start`, injects or submits a prompt, replays State
Travel, changes candidate/HIL/pointer/Git state, or enables the fallback.
Opening the task requests host continuation; the receipt does not claim that a
new model turn ran until Codex itself continues the persisted Goal. Windows does
not expose the Codex app-server daemon control plane, so the isolated prewarm is
reported separately from the live desktop process; the exact task deep link is
the primary no-kill reattach and one controlled app restart remains a bounded
fallback, never a loop. Every plugin-owned Python/PowerShell helper and tunnel
background launch uses `CREATE_NO_WINDOW` or `-WindowStyle Hidden`; an initial
host-owned MCP spawn is reported as `HOST_CAPABILITY_UNAVAILABLE` when the host
does not expose its launch flags. `Unregister`
closes the binding while preserving history and disables the scheduled task
when no active Goal bindings remain.

## Persistent Plan and change display

The task panel and linked change status are one durable pair. `SessionStart`
rehydrates the exact task binding, `UserPromptSubmit` binds the visible turn,
`PostToolUse` reprojects the active task/Delta display after relevant native
actions, and `Stop` preserves the exit boundary without inventing a decision.

These are two different laws. A successful `pv_plan_steer_delta` is followed by
the existing `PostToolUse` projection of the same Plan/Current Change panel; it
does not prove that the steer was captured before reasoning. Codex routes every
pending `TurnInput::UserInput` through `UserPromptSubmit` before model input,
including `turn/steer`; a Goal continuation travels through the same pending-
input dispatcher. The PREPARE adapter therefore ignores caller `source`,
`is_steer`, and `is_goal` claims, derives first prompt versus same-turn steer
from the sealed turn ledger, and recognizes a Goal only from the native
`<codex_internal_context source="goal">` marker. Each input needs its own sealed
PREPARE receipt. After a steer appends a Delta, `PostToolUse` separately
refreshes the same persistent Goal step list and CURRENT CHANGE panel.

Moving from the active row to its queued successor is a separate, fail-closed
checkpoint. The current run must contain one sealed `task.test.output` or
`task.build.output` event whose checks exactly equal the active row's native
acceptance contract. The checkpoint binds that event to the exact task,
session, accepted pointer, and installed plugin, marks only the active Plan row
done, and activates only the requested queued successor. It never creates a
candidate, infers HIL, or moves the accepted PV pointer; missing or mismatched
evidence leaves the active row unchanged.

The skill classifies a request before Plan mutation. A question, explanation,
small read-only ask, or bounded artifact readback is an ordinary task and does
not append a Plan Delta. Only a change to the active Goal's executable contract,
dependency, acceptance, stop, release route, or HIL path is a Plan steer. That
steer links to the existing logical row when possible and refreshes the complete
panel once.

Delivery commits are dependency-coherent integration bundles rather than one
commit/CI/preview/package/install cycle per Delta row. Row acceptance remains
individual, together with PREPARE/retrieval, native PV reads, classification,
visible ChatLineage, lifecycle transition, and panel refresh. One logical bundle
joins related changes for cross-Delta testing, and its verification matrix maps
every included task to changed surfaces, tests, remote/installed checks, outcome,
and failure owner. Any included-row failure fails the bundle closed. The bundle
then synchronizes the root README and affected repository-level evidence before
the governed Git and installed-host gates. At that boundary,
`scripts/sync_website_plan_projection.py` regenerates the sealed public
Plan/Delta snapshot and public plugin metadata directly from a passing native
`PLAN_LANE`; the same command with `--check` must report no drift before push.
The TypeScript execution and ledger views consume that snapshot rather than
duplicating row data. A feature-branch preview may render the commit-bound
snapshot, but production publication remains post-HIL. Local packages are
rehearsal-only; the single stable selector updates once at the bundle boundary
from the exact Git commit package, after all configured checks and exact-SHA
preview proof.

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

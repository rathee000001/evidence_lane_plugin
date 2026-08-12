# Evidence Lane v2 PV11 fallback versus stable regression forensic

Date: 2026-08-12
Project: `test-codex-evidence-lane-plugin`
Active Plan row: Row 164 / `EL-CODEX-TURN_PREPARE_CAPTURE-PROPOSAL-04`

## Scope and boundary

This audit compares the disabled accepted-PV11 fallback installation with the
enabled mutable stable installation and the Task 1 host workflow. It is a
source and installed-byte forensic, not proof that the corrected package is
already active. No candidate, HIL decision, pointer movement, Refresh, Fuse,
deployment, remote push, fallback activation, or new stable selector occurred
during the audit.

## Verified observations

- The live registry still has exactly two Evidence Lane selectors: one enabled
  stable selector and one disabled fallback selector. The enabled selector is
  `evidence-lane-plugin@evidence-lane-v200-task2-build-015ff4c0`; the fallback is
  `evidence-lane-plugin@evidence-lane-pv11-fallback`; it remains installed,
  disabled, and byte-frozen. No tunnel was activated by this audit.
- The current stable installation was activated from a receipt with schema
  `evidence-lane.non-lifecycle-local-package-rehearsal.v1.receipt` and boundary
  `NON_LIFECYCLE_LOCAL_PACKAGE_REHEARSAL`. That receipt explicitly recorded
  `git_invoked=false`, no candidate, and no pointer movement. It was valid for
  local package rehearsal, but not sufficient authority for live activation.
  The activated archive SHA-256 was
  `BA3C073EA3CA30876B942EE910B71C7D5312AC5C7F6F5870AC0B63FD3D15F1B0`;
  the durable rehearsal receipt is under
  `%USERPROFILE%\EvidenceLanePV\installations\codex-v200\row164-stable-update-v5`.
- The stable and fallback copies retain identical product metadata, MCP launch
  metadata, hook registration, PostToolUse handler, and icon bytes. The icon is
  present at `assets/evidence-lane-icon.png` with SHA-256
  `5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3DEF87B8A129C4FA`.
- Excluding generated caches, the fallback contained 144 files and the stable
  source contained 146: 128 unchanged, 16 changed, two added, and none removed.
  The two additions were the general Goal-recovery manager and same-slot stable
  update helper. The blank icon/panel therefore was not caused by deleting the
  brand asset or removing the plugin manifest.
- The installer replaced the stable cache and its private runtime, then the
  helper reopened the exact task before the new runtime had been bootstrapped
  and probed. The first MCP handshake absorbed dependency/bootstrap work and
  timed out. Codex then kept the task's frozen zero-tool catalog snapshot.
- With the native catalog absent, Codex could not attach the native icon,
  resources, project panel, or PV read actions. The missing UI was an
  attachment/catalog failure, not an asset failure.
- A bounded installed-cache readiness probe now constructs the full 62-tool,
  21-read native server in 3.4 seconds with catalog SHA-256
  `831407A86654507C88A096AD8C4457A00EEB6FAF698EF2B70C6C41793DF4777B`
  and resource `ui://evidence-lane/governed-console-v3.html`. This proves the
  enabled cache is warm enough for a host reload; it is not a substitute for
  native task attachment or PV readback.
- PREPARE also performed direct internal SQLite retrieval and returned a
  retrieval outcome. That did not invoke native `pv_query`, did not appear as
  `Pv query` in Codex Sources, and duplicated behavior inside the lifecycle
  hook path.
- The accepted source boundary retained commit
  `6de57e792022e12a58ccc4ff48ba47bd4c95f9d3` and tree
  `5209163e15e346494fee9edadb058ace835d98c8`; the later observed `main` head was
  `8cf535ddc8b55f41913083b4683fdbe3d6163e50`. This audit did not treat that
  historical accepted boundary as authority to activate new dirty bytes.
- A later attempted repair mixed ownership again by placing a complete host
  Step Task List and a `CALL_UPDATE_PLAN...` instruction inside SessionStart
  and PostToolUse payloads. That was also wrong: hooks own lifecycle; skills own
  behavior.
- The stricter PREPARE binding check exposed one separate lifecycle metadata
  defect: a newly claimed queued task updated `active_backlog_task_id` but could
  retain the prior task's `DONE` status. Plan Lane correctly marked the new row
  `ACTIVE`, so PREPARE failed closed on the disagreement. Normal task claim now
  sets both the task ID and `active_backlog_task_status=ACTIVE` atomically.

## Causal finding

The regression was a chain, not one deleted feature:

1. A non-promotable dirty/local rehearsal was accepted for live activation.
2. The same stable selector was reinstalled, but its runtime was cold.
3. The exact task reopened before bootstrap and native import/prewarm completed.
4. The first native handshake timed out and Codex froze an empty catalog for
   that task.
5. Native PV reads, icon/resource attachment, and project panel disappeared.
6. Internal hook retrieval was mistaken for the missing native behavior reads.
7. A repair then incorrectly pushed host plan behavior into lifecycle hooks.

The selector count was not the immediate cause in this task: the current live
registry remained at two selectors. Historical build-specific registrations
were a separate earlier bloat defect and must stay prevented by same-selector
updates.

## Corrected ownership law

- `UserPromptSubmit`, `SessionStart`, `PostToolUse`, and `Stop` hooks perform
  lifecycle capture, redaction, Entry/PREPARE/COMMIT, bounded status, and sealed
  lifecycle-event receipts only.
- PREPARE now seals `PENDING_NATIVE_SKILL_QUERY`,
  `hook_lookup_performed=false`, and
  `native_behavior_query_satisfied=false`. It performs no accepted-PV or
  ChatLineage search on behalf of the behavior layer.
- For every visible prompt, Goal continuation, correction, or steer, the active
  Evidence Lane skill must consume PREPARE and call the installed native route
  in this order: `pv_status`, `pv_task_backlog`, and one bounded relevant
  `pv_query`.
- The skill validates the complete Plan Lane and then calls host `update_plan`.
  Every label is only
  `Row <canonical row> / <task ID> — <exact description>`; linked Delta JSON
  remains in native authority.
- After `pv_plan_steer_delta`, the skill repeats the three native reads and the
  complete host panel refresh. A lifecycle receipt or direct SQLite lookup is
  never equivalent proof.
- If the native route or host plan tool is absent, the behavior layer fails
  closed. It does not fabricate PV reads or silently reconstruct the panel from
  local files.

## Corrected stable delivery law

- A local rehearsal may be staged and tested but cannot activate the stable
  selector.
- Activation requires a sealed exact clean commit and tree on the one governed
  non-protected branch, an executed native governed remote push, remote head
  parity, and all required GitHub CI checks successful at that exact SHA.
- The archive, local package receipt, commit/tree, native push receipt, the
  existing governed Python CI/CodeQL/preview-build results, and the
  Git-integrated Vercel branch preview are joined by
  `evidence-lane.codex-git-ci-vercel-release-authority.v2`.
- `build_codex_exact_commit_package.py` exports only the named Git commit and
  excludes dirty and untracked checkout bytes.
- `seal_codex_git_ci_release_authority.py` joins that exact package with the
  native governed push and successful required GitHub checks at the same head.
- The one legacy migration replaces the build-specific mutable selector with
  canonical `evidence-lane-plugin@evidence-lane-github` only after the exact
  Git route is installed, prewarmed, and read back; every later update reuses
  that canonical selector. The disabled fallback remains byte-frozen. The
  installer bootstraps the installed private runtime, imports the native MCP,
  prewarms native dependencies, reverifies the exact icon, removes obsolete
  mutable registrations through supported Codex commands only after new-route
  proof, and seals `runtime_ready_before_task_reopen=true` before reopening.
- Stable-update restart and Windows-logon recovery are separate helpers. The
  update helper reopens the exact task in the same allowlisted stable-or-Beta
  Codex app and rebinds the general logon recovery manager. The general manager
  never installs a plugin, starts a tunnel, submits a prompt, or mutates the
  Evidence Lane lifecycle.
- A failed prewarm does not activate fallback implicitly and does not create a
  third selector.
- The prewarm and installed-acceptance guards now bind the actual governed
  console resource `ui://evidence-lane/governed-console-v3.html`; the obsolete
  `governed-panel.html` expectation was removed before activation.

## Current proof and remaining proof

Source-level proof now passes:

- turn-control, task-advance, session, failover, installer, rehearsal,
  exact-package, and release-policy suites: 68 passed;
- native MCP route and prompt-index suite: 25 passed;
- total unique relevant regression tests: 93 passed;
- focused release/resource regression after correcting the console URI:
  41 passed;
- Python compilation, release-JSON parsing, four PowerShell helper parser
  checks, hook-behavior scan, and `git diff --check`: pass.

Installed-host proof is still pending. The current task's native MCP catalog is
absent, so native `pv_status`, `pv_task_backlog`, `pv_query`, and the exact panel
refresh cannot truthfully be demonstrated here yet. The next governed sequence
is:

1. finish review and focused source verification;
2. create one exact branch commit;
3. push through native governed remote Git;
4. wait for the existing commit-triggered governed Python CI, CodeQL, and
   preview-build workflows at that SHA;
5. verify the Git-integrated Vercel branch preview at the same SHA and build and
   seal the exact-commit archive plus release authority;
6. migrate once to the canonical Git stable selector, prewarm it, clean
   obsolete mutable slots only after proof, and reopen this exact
   task;
7. prove the native 62/21/41 catalog, 15 skills, icon/resources, project panel,
   one visible `pv_query` per prompt or steer, and the complete persistent Step
   Task List.

A fresh exact-task restart has been prepared but not executed. Preparation
receipt SHA-256:
`856595C65BBCB6C5A092C4D855729BA5E7D714E2970ED7BFE3A5B5A5DC00C05F`;
task-binding receipt SHA-256:
`358359454962219CFDECD96CE5C880F333B610C685A959A7367D2A097B77BDB0`.
The separate `-ConfirmRestart` gate remains unsatisfied.

## Verdict

`FIX-THEN-PURSUE`, high confidence. The useful v2 baseline remains intact, and
the regression has a specific recoverable delivery/ownership cause. Confidence
would decrease if the exact-commit installed package still loses the catalog
after prewarm, or if a fresh-task Sources trace shows PREPARE without the three
skill-owned native reads and full `update_plan` refresh.

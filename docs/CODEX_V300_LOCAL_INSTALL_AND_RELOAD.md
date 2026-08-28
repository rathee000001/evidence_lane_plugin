<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v3 -->

# Evidence Lane 3.0.0 local install and exact-task reload

This is the current maintainer-only local-update route. It installs one coherent
package into the hidden Codex plugin layer. It never creates a plugin runtime in
the source workspace or Project/PV root, never queries accepted state, and never
uses a generic plugin-add/cache-backup attempt as a speculative first step.

## Three separate locations

Evidence Lane keeps these identities independent:

1. **Workspace** — selected by the user when the Codex task is created. This is
   where the user wants work performed.
2. **Project/PV root** — selected or created during the project's first Source
   Intake registration. It is external to the workspace and reused by every
   later task for the same project.
3. **Hidden plugin runtime** — derived from Codex's installed plugin/cache
   identity. The runtime, dependencies, tunnel, maintainer helpers, models,
   licenses, and receipts live here.

None of these paths is hardcoded. State Travel reuses the registered project and
workspace binding; an unrelated initial workflow may register a different
project root and workspace.

## Package boundary

The local archive is generated through the Plugin Creator development flow and
must contain the current package roots: `.codex-plugin`, `authorities`,
`env`, `hooks`, `manifests`, `mcp`, `schemas`, `scripts`, `sdk`,
`skills`, `src`, installed smoke `tests`, `toolchains`, `tunnel`, and `uop`.
Every declared member is hash-bound by
`manifests/executable-surface-registry.v1.json`.

Repository-only site sources, PoCs, developer test suites, `.next`,
`node_modules`, virtual environments, caches, build outputs, and local evidence
are excluded. The package must not mix historical and current generated files.

## Ordered local-update transaction

1. Run the deterministic source and package-parity checks.
2. Use Plugin Creator to validate and pack the exact current plugin root.
3. Assign a fresh cachebuster package version and materialize only the existing
   local-testing marketplace slot.
4. Verify every staged member, manifest, catalog, dependency lock, model/tool
   receipt, and hidden-runtime target before switching the slot.
5. Prewarm the derived hidden runtime from the hash-locked dependency and native
   toolchain manifests. Normal MCP startup must not invoke package installation.
6. Produce the pre-restart installed-package receipt. This proves bytes and
   readiness only; it is not installed-host proof and cannot infer HIL.
7. Seal the exact install/task/channel restart preparation without inspecting,
   rewriting, or draining Codex turns. A stale in-progress turn is a host fault,
   never a lifecycle repair target.
8. Persist and visibly complete the current response. Only after that terminal
   boundary may the maintainer use a local dumb same-app/same-task helper to
   close and reopen the exact selected Codex channel. The helper is not shipped
   and contains no drain, install, tunnel, Plan, Goal, State Travel, focus, or
   fallback logic.
9. After the same task reopens, verify the installed package and hidden runtime
   through native catalog, skill, hook, ENV/UOP, SDK, schema, tunnel,
   and project/session readback.
10. Relock the canonical compact Step Task List and unchanged Changes surface
    before resuming governed work.

## Restart is terminal-safe and maintainer-local

No drain utility is part of the current route. The package may seal an exact
restart preparation receipt, but it cannot stop an active response, repair host
history, or manufacture a terminal event. A maintainer-local dumb helper may
act only after the response is terminal, accepts one exact Codex app identity
and task deep link, and cannot select another task or workspace.

State Travel never invokes either helper. A fresh State Travel destination uses
native task/session attachment and Plan acceptance; it performs no install or
restart.

## Tunnel and dependency provisioning

The tunnel is installed into the hidden plugin runtime, not the user's
workspace. It is enabled only for a measured host-tool gap. On first use, if no
version-matched tunnel configuration exists, the host may open a one-time
interactive terminal that helps the user create and paste the required API key.
The secret is stored with Windows DPAPI, never in project data, Git, prompts,
receipts, or process arguments. After configuration the tunnel runs hidden,
survives Windows sign-in under its exact versioned contract, and prewarms the
runtime dependencies and toolchain it owns.

Users receive one current plugin version and at most one matching active tunnel.
There is no separate governed-user recovery helper. Maintainer install and dumb
restart helpers remain local maintenance tools and are never shipped as plugin
runtime or staged as user-facing executable behavior.

## Hook activation

Local installation never claims hook success from source tests. Hooks remain
OFF until the restarted installed host proves each registered event class and
its ordered subhandlers against the exact installed hashes and timing laws.
Passing handlers may then be trusted and enabled through native
`hooks/list`, `config/read`, and compare-and-swap `config/batchWrite`. A failing
handler stays disabled without disabling unrelated passing handlers.

`PreCompact` must seal before compaction; `PostCompact` must rehydrate after
compaction. `PreToolUse` reentrancy and fail-closed behavior are tested as a
distinct boundary.

## Failure behavior

Any source/package hash drift, incomplete dependency provision, hidden-runtime
path escape, catalog/schema mismatch, stale in-progress turn, wrong app/task
identity, failed hook timing, or post-restart attachment mismatch fails closed.
The local-testing slot may not mutate the main Git release slot, Project/PV
pointer, candidate, HIL state, Plan, Goal identity, or dirty workspace bytes.

## Evidence required before continuing

- exact package version, archive hash, and executable-surface manifest;
- derived catalog counts and complete action/skill/hook inventories, with no separate command surface;
- hidden runtime root, dependency/tool/model/license receipt hashes;
- local-slot materialization and pre-restart acceptance receipts;
- terminal-safe exact task/channel restart-preparation receipt;
- maintainer-local dumb helper same-app/same-task restart receipt, when used;
- post-restart native catalog and project/session/runtime attachment proof;
- installed ENV/UOP execution and SDK routing proof; and
- canonical Step/Changes relock proof.

Only the later governed branch/main and HIL routes can authorize Git promotion,
candidate acceptance, pointer movement, or accepted ZIP rotation.

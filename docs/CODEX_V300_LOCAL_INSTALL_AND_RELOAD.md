<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v3 -->

# Evidence Lane 3.0.0 local install and exact-task reload

<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->
## Current backend contract

This public document is refreshed from the same source graph used by the installable plugin package.

- Plugin package: `3.0.0+codex.20260828064341`.
- Native MCP: **91 actions** (**30 read / 61 write**).
- Native skills: **26 governed skills**; the separate command layer is absent.
- Hooks: **11 events / 44 ordered handler actions**.
- SDK: internal action SDK and outer routing SDK remain distinct; public action count **91**.
- ENV/UOP: separate executable authorities with **7 ENV members / 5 UOP members**.
- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.
- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.

Exact backend bindings:
  - `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` — `42A726CD910A27EF9B8987907F02D127789857C8B04E1E214A91D1F74D151A4B`
  - `plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json` — `B571AF9EC31691C96DB0B3845ED0B7A6700D1C84A2578ABA9A2EA594982AF045`
  - `plugins/evidence-lane-plugin/skills/skill-surface-registry.v1.json` — `38B1F95B8160E037B43B209A6D6047BF8BCA4D2599182C2F20E4606B6CBDF3A5`
  - `plugins/evidence-lane-plugin/hooks/hooks.json` — `C37DB05DD4701087EAD0BD31203C843AAFA79ED39A081F2E9DFF313A77631EEF`
  - `plugins/evidence-lane-plugin/sdk/sdk-manifest.v1.json` — `5BD21AEB96D7E41209E3D059D8A5296D851BDED1D453D6EF486C0CD50D745245`
  - `plugins/evidence-lane-plugin/mcp/mcp-manifest.v1.json` — `E9E402C2F20B2BBE63B6BF91613B1C97E85E615F982D52CF6D020408251AFAFB`
  - `plugins/evidence-lane-plugin/env/authority-manifest.v1.json` — `E4F283EC16F86995E2937288DD8A8E5623007351CBB1CA3FD01FDA5C7363B6C1`
  - `plugins/evidence-lane-plugin/uop/authority-manifest.v1.json` — `BBA3CDAE9CC0FF981E5C6E19F83FBBCE6EB2ED8167CDBB2E9D1C557FA03CA57C`
  - `plugins/evidence-lane-plugin/toolchains/TOOLCHAIN_EXECUTION_MATRIX.md` — `E5379D7C4B17BC9293F332216581D60F88ADF73A4B7B361D84D09B47FC4EA66F`
<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->


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

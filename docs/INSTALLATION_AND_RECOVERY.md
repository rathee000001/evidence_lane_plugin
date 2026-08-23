<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / 2026-08-23 -->

# Installation and recovery

Evidence Lane installation is an exact-commit transaction. A local working
tree, package rehearsal, passing unit test, or cached marketplace entry is not
an installed-release claim.

## User installation

Users install one reviewed Evidence Lane release through the supported Codex
plugin marketplace route. Installation must bind and read back the same source
commit, tree, deterministic package, catalog, skills, hooks, icon, and runtime
identity. Secrets are not stored in Git, receipts, task panels, or project
SQLite.

## Maintainer verification sequence

1. Build a deterministic package from the exact commit.
2. Verify source, commit, tree, package, dependency, catalog, skill, hook, and
   secret seals.
3. Stage and activate the intended maintainer slot.
4. Run the pre-restart installed-package acceptance check.
5. Prepare one restart request bound to the exact Codex app, task UUID, deep
   link, project/session, and installed identity.
6. The helper closes that exact app once and immediately reopens the same task
   maximized; it does not install the package or wait for an arbitrary timer.
7. Verify native MCP, hooks, project/runtime panels, icon, task-owned workspace,
   Changes UI, helper/tunnel identity, and durable store after restart.
8. Continue the active Plan row. Stop at HIL only when the Plan reaches it.

## Helper boundary

The governed-user Goal Recovery helper and maintainer restart/update helper are
different products. The user helper preserves multiple exact project/task
bindings and reopens only the task for the app from which it was invoked. It
does not install, switch slots, invoke HIL, mutate Git, or move a pointer.

The maintainer helper runs only after the package transaction and pre-restart
verification are complete. It must not create duplicate scheduled tasks, use a
stale helper version, launch visible terminals, or open another Codex app.

## Recovery slots

Maintainer Local, Stable Recovery, and Git Release roles remain isolated. If
the local development package breaks, a sealed multi-probe failure may select
the exact reviewed recovery package. One transient error cannot. Switching
stops the old version-bound support processes, activates the target, restarts
the exact app/task once, and requires post-restart native readback.

At a governed release boundary, the selected slots may become byte-identical
to the accepted Git package. Historical packages remain immutable provenance;
they are not kept as active prehistory fallbacks.

## Build locally

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m pytest -q
```

Focused release checks live under `tests/` and the reusable GitHub action at
`.github/actions/evidence-lane-ci`.

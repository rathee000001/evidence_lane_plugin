<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / 2026-08-23 -->

# Evidence Lane user helper guide

The user Goal Recovery helper preserves a governed Codex task binding across a
normal Windows sign-in. It is separate from the maintainer release updater and
does not install, upgrade, switch, or publish Evidence Lane.

## User-owned helper

The packaged user entry point is:

`plugins/evidence-lane-plugin/scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1`

Supported actions are `Probe`, `Register`, `RecoverNow`, `RecoverAtLogon`,
`Status`, and `Unregister`. A registration binds the exact release, project ID,
task/thread ID, task deep link, and installed package identity. Titles and the
current working directory are not identity.

One Windows user may retain multiple project bindings. Each binding must remain
project-scoped and independently inspectable. Recovery may request the exact
`codex://threads/<task-id>` deep link; it must not select a task merely because
its title looks similar.

## One-time setup and verification

1. Verify the intended installed Evidence Lane release and exact task binding.
2. Run `Register` once for that project/build binding.
3. Run `Status` and retain the returned task name, project ID, task ID, deep
   link, script hash, and scheduled-task state.
4. Use `RecoverNow` only for a bounded recovery test; use `RecoverAtLogon` for
   the registered sign-in behavior.
5. Use `Unregister` for that exact binding when recovery is no longer wanted.

All helper-launched PowerShell, Python, and Codex child processes must be
hidden/no-window. A visible flashing terminal is a defect, not a normal health
signal.

## Hard boundaries

- The helper has no HIL, Fuse, pointer, Git, deployment, slot-switch, or
  installation authority.
- It must not navigate or restart Codex while a State Travel lease is active.
- It must not fabricate focus, Plan restoration, or Goal continuation merely
  because a deep link was requested.
- The right-side Plan and Changes surfaces require their own host-observed
  receipts after recovery.
- The current pre-HIL 2.2 source still requires live proof of exact-build
  idempotency, shared State Travel interlock, and multi-project recovery before
  those behaviors can be claimed as installed-host facts.

Repository tests and maintainer CI/release evidence are development inputs.
They are not part of the downstream user workflow and the Codex package archive
must exclude a `tests/` directory.

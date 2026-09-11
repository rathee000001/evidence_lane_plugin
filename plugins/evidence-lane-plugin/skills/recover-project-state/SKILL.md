---
name: recover-project-state
description: "Back up separate project lanes coherently, restore selected Git history to a fresh folder, or recover after client loss. Use for explicit recovery; accepted-PV rollback is removed."
---

# Recover project state

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

For an uncertain job, use `job_recovery_inspect` to read its
effect heads. `job_reconcile_effect` requires the exact effect digest, current
Plan revision in both request and envelope, and a granted admin connection.
It reuses a committed execution confirmation or observes the prepared local
file's exact before/after hashes. For an unconfirmed Git effect, select
`observation_route=git_readback`: the owning Git route checks the exact fetch,
fast-forward, clone, checkout or consumed push state with bounded read-only
commands. Push readback needs a current destination read grant and, for a remote
service, network access and its recorded authentication provider. It never
rewrites source files or replays an effect. Conflicting or incomplete state
remains uncertain; matching current state does not identify its historical actor.
After all effects are reconciled, the job stays failed and requires a fresh
Plan; this operation never declares task or Goal success.

Use `project_recovery_inspect` and the exact recovery records
before acting. `project_backup` creates a coherent snapshot of separate lanes;
it includes Root PV, all initialized lane databases, registered content,
schema history and historical natural views. `project_backup_verify` verifies
the selected backup. Offline database recovery requires a stopped Engine and
a fresh state root. Use the configured plugin Python with the packaged
`scripts/run_recovery.py` and exact runtime-root, project-id, backup-root,
manifest-digest and destination-root arguments. The Python distribution also
provides `evidence-lane-recovery`. Read back the
recovery record, reconnect, and bind the new Plan to its recovery digest.
A Git source restore uses
`git_restore_preview`, then the exact approved `git_restore` request into a
fresh folder. Preserve the original source root and dirty bytes.
Reconnect after the source binding changes. Use `restoration_reindex` with the
exact restore digest to register and index the fresh source's members and
extraction coverage, then refresh the Plan with that source restore digest.
Resolve reported extraction gaps; a parsed-source graph does not establish
complete semantics for every language or replace later sector operations.
After uncertainty, use `git_restore_reconcile`, `restoration_read` or the
specific abandonment/continuation recovery action. Do not replay mutations,
overwrite the live checkout, revive PV rollback or treat an artifact upload as
transactional live state. Keep recorded gaps and user decisions explicit.

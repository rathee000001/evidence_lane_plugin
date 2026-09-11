---
name: refresh-project-evidence
description: "Refresh selected sources, lane views and changed authority references after a steer or verified work. Use for explicit evidence refresh and stale view repair."
---

# Refresh project evidence

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Identify the exact changed sources, authorities and stale lane
views from current evidence. Read the owning current inventories, then inspect
each exact baseline with `source_snapshot_state`. Register the current selected
source bytes through Source Intake, preserving the recorded directory selection
and explicit lane overrides. A Sources registration alone does not reparse files.
Call `source_prepare_refresh` with the new route selection and exact
`baseline_snapshots`. Select whole affected scopes; an unselected still-present
file cannot disappear through retirement. Omit `selection` for deletion-only
work. Respect complete-file, byte, parser and task budgets. The preparation owns
initial versus expected-snapshot bindings, keeps unchanged scope lineage, and
adds verified retirement tasks when source group membership changes. Inspect
the returned tasks and `source_preparation_read` before Plan adoption.
Use the Plan steer workflow to adopt the complete prepared group unchanged,
preserving the completed prefix. Call `source_materialize` with that preparation,
current Plan revision and document digest; inspect `source_materialization_read`
through terminal coverage. Earlier verified tasks supply retirement replacements;
never invent future snapshot IDs, inject task arguments or skip a failed child.
For separately planned `source_snapshot_retire`, select only absent sources or
explicit current replacements. Retired selectors leave current readers and views
while historical snapshots, files and lineage heads remain readable. Reappearance
binds that retained head and publishes a new generation. GitHub Code requires the
exact current Sources `git_snapshot_id`, clean commit and tree; local absence is
not committed deletion, and working-copy bytes are recorded separately from Git
blobs. Deletion-only completion reports refreshed without materialized files.
Use `lane_view_refresh` only for an admitted current view contract.
Select only the consuming lane's MMD, DOT or navigation pointer. Source export
is the default. For native DOT validation, include DOT and select
`dot_validation=native`; it requires a supported configured client profile and
the verified shared Graphviz binary. A native failure does not downgrade to
source-only validation. Existing exports retain that choice on refresh; an
adopted source-change task must also permit `Graphviz_dot` when preserving a
native-validated export. Admission checks the selected export, native client
profile, exact task tools and required workers before any source effect. A changed
export selection invalidates that admission. An unchanged view reuses its verified
snapshot and original worker evidence; `reused_snapshot` does not report a new
render. Damaged derived files require an explicit refresh. Runtime graph analysis uses bounded Rustworkx. Static
package graphs exclude runtime observations. None of these receipts attests a
native Codex task merely from its configured profile.
Preserve each lane's natural files, pointer meaning and needed MMD/DOT formats.
For a semantic change use the Plan steer workflow first. Verified Delta exit
records Learning and bounded Memory references atomically through their owners.
It does not automatically reparse every source or render every lane view. Refresh
does not recreate Overlay, produce accepted-PV archives or request PV HIL.

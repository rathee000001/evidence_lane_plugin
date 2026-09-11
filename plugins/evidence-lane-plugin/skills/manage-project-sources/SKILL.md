---
name: manage-project-sources
description: "Classify, register, inspect and refresh ordered project sources with exact attribution. Use for files, selected SQLite, source identity, source graphs and authorized Git history."
---

# Manage project sources

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Preserve the user's ordered sources. Use `source_classify`
to inspect the current sector routing, then `source_register` for an authorized
registration. Local source access needs current path grants; URL metadata is
not permission to fetch. A code-mode or explicit sector choice must be a
retained lane. Never classify an authority as a sector or assert ownership
from file existence.

For explicit source-to-sector choices, `lane_configure_routes` consumes 1–100
exact overrides in the same attributed registration. It does not arm a global
next-job setting. Pass `parent_route_id` to inherit only surviving exact source
pointers; explicit overrides win, omitted sources stay out of the new receipt.
`source_routes_read` verifies a selected immutable routing receipt. A repeated
request ID returns its original registration observation and cannot register
changed bytes; use a new request for a refresh.

Directory registration records the same selection used by classification:
tracked and safe untracked Git files, or the bounded filesystem selection.
Verification and export refresh reuse that recorded method. A changed or
unavailable method requires a new observation. Runtime/cache trees, including
`.work`, stay outside new automatic selections. Historical source objects keep
their original capture policy. A narrow Code consumer checks only its selected
input scopes and does not traverse ignored trees again.

Use `source_prepare_tasks` with an exact route and selected occurrence ordinals
to expand directories through the retained per-file classifier and compile the
complete bounded selection into sector-owned Plan tasks. Exact project-relative
file overrides take precedence; registered file occurrences retain their lane.
Supply required Git checkpoints and parser choices in `lane_options`. The action
records child source hashes and parent-member bindings in Sources and returns
task definitions; it does not change the Plan, start jobs or prove tool readiness.
Adopt those tasks through normal `plan_create` or attributed `plan_refresh`, then
use `delta_enter_planned`. Each owner still verifies its parser result and current
source bytes. Preparation fails on an incomplete or oversized selection instead
of silently omitting files. Code groups use at most 32 exact paths and 128 scopes
per lane; the actual Plan byte limit also applies. `source_preparation_read`
retrieves historical preparation evidence, not a fresh filesystem observation.

For a complete prepared group, use `source_materialize` with its preparation ID,
current Plan revision and document digest. The engine executes only those next
contiguous Plan tasks, one normal Delta at a time, with their original grants,
tools and verifiers. It stops at a steer, pause, drain, failed child or budget.
`source_materialization_read` reports owned progress and historical outcomes.
Completion requires every selected original file in its current owning snapshot
and a fresh complete parent-selection check. Parser fidelity remains the exact
prepared options. Truncated current-owner readers cannot certify full coverage.
An unowned group is never restarted by a query or repeated request. Reconcile
its normal Plan jobs first, then use `source_materialization_reconcile` to close
that group as blocked. Continuing changed work requires normal Plan refresh and
an explicit new preparation/run; verified history is preserved.

For a registry-declared source consumer, bind `delta_enter.source_route` to its
`route_id` and exact `occurrence_ordinals`. The Plan still supplies the operation,
tools and path permissions. Selected registered inputs must match that sector
and their frozen bytes, within 512 files and 32 MiB. Source routing does not
attest a parser's complete dependency closure. Unsupported consumers fail
visibly; inspect the action's `source_routes` contract. Custom schema versions
remain a separate operation.

Use the exact registered batch for selected SQLite inspection, source identity,
crosswalks, graph/diff/impact work and optional Git history. Choose only the
needed `source_*` operation and bounded limits. `source_verify` checks current
bytes; `source_read` reads bounded registered metadata. Custom schema actions
must target retained sectors. Keep ChatLineage capture separate. Record changed
source evidence through the owning refresh/register operation, preserving prior
identity and provenance.

Read only the reference for the selected source or requested operation:

- [Code](references/code.md)
- [Word documents](references/documents.md)
- [Excel and structured data](references/spreadsheets-data.md)
- [PowerPoint presentations](references/presentations.md)
- [Tableau](references/tableau.md)
- [Power BI](references/power-bi.md)
- [PDF and OCR](references/pdf-ocr.md)
- [Images and media](references/media.md)
- [Research, Artifacts and Custom data](references/research-artifacts-custom.md)

Office coverage is Word, PowerPoint and Excel only. Storage services are separate.

---
name: run-project-lifecycle
description: "Coordinate project entry, planning, bounded implementation, evidence refresh and session closure across the owning first-class workflows. Use for the complete Evidence Lane project lifecycle."
---

# Run project lifecycle

Read [shared boundaries](references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Route project entry through Boot, planning through Plan,
ordered sources through Source Intake, and implementation through Build and
the owning lane workflows. Keep each first-class skill separate and inspect
its live actions instead of creating a generic replacement workflow.
Use `lineage_read` for bounded visible project events and `lineage_record` for
explicit attributed visible records. Native hook delivery has separate
provenance; manual records never impersonate host hook observations.
Respect the immutable capture route reported by `storage_status`. Sparse
capture keeps prompt/steer identities and hashes while omitting their text
from ChatLineage files and search. For `task_classify`, `steer_preview` or
`steer_submit` on an omitted source, supply the exact visible `source_text`;
the engine checks its redacted hash in memory. A missing or different source
cannot be classified. Keep interpretation/focus and receipts concise rather
than copying the omitted conversation into them. Mode selections retain the
verified source hash. Typed receipt/control reports keep their attribution;
an accepted/status/capture-kind claim does not authorize work or verify a Delta.
Prompt/steer recording also creates a source-bound prompt index in the same
ChatLineage commit. Use `prompt_index_status` with the exact reported session
when paging by prompt index. Use `task_classify` for explicit intent, focus,
owning lanes and a next action; this interpretation does not change Plan or
authorize effects. `steer_submit` commits the same source-bound classification
with its queued steer; the checkpoint coordinator applies a later Plan revision.
For repository enrollment, selected-branch synchronization or an authorized
branch push, read [Git operations](references/git.md). This conditional route
keeps source writes, authentication, preparation and effect recovery attributed.
Visible hook turns bind captured input, tool observations and visible responses
to the same engine session and reported turn. Missing input/response evidence
or changed Plan context remains a gap; a Stop event is not Goal completion.
For compaction, inspect `session_context` and the sealed pre/post references.
The 8 KiB context carries bounded task and separate lane references. An existing
Project Memory index contributes up to four exact locator references through
its own checkpoint and rehydrate APIs; raw Memory content is not replayed.
An altered session, Plan, source-bound mode or relevant lane head requires
fresh owning reads. Never reconstruct authority from a conversation summary.
Handle queries, steers, verified exits, State Travel, recovery and closure
through their exact owners. Preserve project isolation, one writer and truthful
status through the complete lifecycle.

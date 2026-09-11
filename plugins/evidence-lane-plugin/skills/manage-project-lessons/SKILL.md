---
name: manage-project-lessons
description: "Inspect and revoke project-isolated procedural lessons recorded after verified work, or record an explicit host-memory provenance reference without creating a lesson. Use for lesson retrieval, correction or provenance."
---

# Manage project lessons

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Use `learning_read` to retrieve project-isolated lessons with
their verified Delta-exit provenance. Apply only relevant, current lessons.
For an explicitly selected host-memory reference, use
`learning_record_host_memory_import` with its source and context hashes and the
exact current Plan task contract. An optional Delta job must match that task.
This records a Receipts-owned provenance reference and a Project Memory link;
it neither reads host memory nor creates Learning. Source hashes remain
caller-reported. Keep raw memory text, credentials and private reasoning out.
Use `learning_revoke` when authorized to withdraw a particular lesson, retaining
its history and evidence. New procedural lessons are recorded by the verified
Delta-exit owner. Do not fabricate an accepted lesson from a host-memory note,
manually promote an unverified result, or introduce a separate Learning HIL.

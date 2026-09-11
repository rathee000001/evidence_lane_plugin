---
name: manage-project-memory
description: "Ingest, query and checkpoint bounded project memory with source attribution. Use for project recall while keeping host recall, instructions, task exchanges and procedural lessons separate."
---

# Manage project memory

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Use `memory_read` for bounded FTS/BM25 retrieval and cite its
registered locators and evidence. For one authorized relationship, use
`project_memory_record_link` with two bounded locator bodies, a supported edge
type and an evidence reference that exists in its owning lane. The engine
derives both endpoint identities and records the link atomically. Use
`memory_ingest` for bounded batches of locators and edges with known endpoint
IDs. Both actions verify references against their separate project lanes;
labels and relationship semantics remain attributed agent reports. Reuse a
request ID only for the exact same action, client and content; replay returns
its original Memory head. A new request may reuse existing locator/edge IDs.
Verified Delta exit automatically indexes its Plan task, receipt, Learning
version and addressed result, with three evidence links attributed to the engine.
This bounded update commits with completion; it copies no source payload and
does not require a second Memory write from the agent.
No source payloads are copied. `memory_checkpoint` pins the current task/Plan references;
`memory_rehydrate` reports compatibility and changes for a selected checkpoint.
Keep host MEMORY.md, instructions, Canon, ChatLineage and Learning separate.
Neither a remembered fact nor a checkpoint proves current native attachment.

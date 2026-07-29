---
description: Read the immutable chat-lineage lane
argument-hint: <project_id> [query-or-path]
---

# Evidence Lane chat lineage

Resolve `chat_lineage` through `lane_catalog`, call `lane_status`, then use
`lane_search` or `lane_fetch` for `$ARGUMENTS`. Preserve raw visible
prompt/response provenance and hash-chain status. Never expose private reasoning
or append through this read command.

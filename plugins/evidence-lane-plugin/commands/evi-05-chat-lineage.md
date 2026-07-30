---
description: Read the immutable Chat Lineage source lane
argument-hint: <project_id> [query-or-path]
---

# /evi-05-chat-lineage

Resolve canonical lane `chat_lineage` through `lane_catalog`, call
`lane_status`, then use bounded `lane_search` or `lane_fetch`. Preserve exact
visible prompt/response provenance and hash-chain status. Never expose private
reasoning or append through this read command.

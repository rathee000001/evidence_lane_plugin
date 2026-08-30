---
name: evi-instructions
description: Resolve and use the separate AGENTS.md instruction chain and host conversation MEMORY.md recall arm under ENV/UOP without merging either into Project Memory, AI Learning, Canon, or Project Truth.
---

# Evidence Lane Instructions and Host Recall

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/references/shared-boundaries.md`. The lifecycle controller
owns the complete registry-derived entry, in-Delta query, and exit sequence; this skill
owns the two host-facing arms only.

The resolved project/worktree `AGENTS.md` chain is instruction authority. Host
conversation `MEMORY.md` is nonauthoritative helpful recall. They are separate
from each other and from Project Memory DB, AI Learning, Canon, sector lanes,
Project Universe, connector brain, Project Overlay, Plan, and ChatLineage.

1. Call `pv_status` and `pv_task_backlog` for exact project/session/task state.
2. Call one bounded live-root `pv_query` and `search`. Consume the resolved
   `AGENTS.md` source-chain hashes and the host `MEMORY.md` locator/hash as two
   distinct result arms. Never concatenate or silently promote them.
3. Apply `AGENTS.md` instructions by their actual directory scope and
   precedence. A stale or missing instruction file must be reported truthfully;
   it is not reconstructed from chat.
4. Use host `MEMORY.md` only as recall that can guide a bounded verification.
   It cannot establish current runtime, Plan, Goal, HIL, pointer, candidate, or
   installed-version truth.
5. Importing a host-memory fact into AI Learning requires the separate explicit
   `learning_record_host_memory_import` provenance route. This skill performs no
   automatic import or Memory write.
6. Delta entry automatically consumes both arms through the lifecycle query;
   in-Delta work may query them again when scope changes. Delta exit records the
   current source-chain/locator hashes without rewriting host-owned files.

Never query accepted ZIP storage, load raw chat history, create a candidate,
infer HIL, move a pointer, or merge authority roles.

## MCP routing contract

Read `../evi/references/mcp-tool-routing.v1.json` and use the ordered
`evi-instructions` read route. Missing or ambiguous current tools fail closed;
there is no hook-only or historical-file fallback.
`MCP_ROUTING_FAIL_CLOSED` applies to every call.

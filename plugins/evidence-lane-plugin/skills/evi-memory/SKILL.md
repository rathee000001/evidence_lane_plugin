---
name: evi-memory
description: Query and link the independent Project Memory DB through bounded FTS5/BM25 locators without merging Project Truth, AI Learning, Canon, AGENTS.md, or host MEMORY.md.
---

# Evidence Lane Project Memory

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its hook/skill ownership
contract. This skill owns Project Memory behavior; hooks provide lifecycle
receipts only.

Project Memory is its own project-scoped SQLite/FTS5 authority. It links bounded
locators from Plan, all 18 sectors and Project Engulf, AI Learning, Canon,
Project Universe, connector brain, receipts, and compaction continuity. It does
not replace or merge those authorities. Resolved `AGENTS.md` and host
conversation `MEMORY.md` remain separate instruction/recall arms.

1. Call `pv_status`, `pv_task_backlog`, and a bounded live-root `pv_query`.
2. Call `project_memory_query` with exact project/session/request identity and a
   bounded query. Suppress stale active-task locators and refresh once through
   the governed Learning -> Canon -> Memory -> Universe sequence when required.
3. Call `project_memory_record_link` only for one typed content-addressed edge.
   Store locators and hashes, never raw source payloads or private reasoning.
4. On Delta exit, Memory refresh is owned by adaptive Delta exit after changed
   lanes, Learning, and Canon. Prompt queries do not substitute for that write.
5. PreCompact/PostCompact checkpointing may seal and rehydrate only the bounded
   current Memory head and locators; it never reconstructs authority from chat.

Ordinary reads use the live root. Never query the rotating accepted ZIP, create
a candidate, infer HIL, move the accepted pointer, or treat Memory as Project
Truth.

## MCP routing contract

Read `../evi/references/mcp-tool-routing.v1.json` and use the ordered
`evi-memory` route. Missing or ambiguous current tools fail closed; never fall
into AI Learning aliases. `MCP_ROUTING_FAIL_CLOSED` applies to every call; the
installed registry contains no Learning-as-Memory compatibility actions.

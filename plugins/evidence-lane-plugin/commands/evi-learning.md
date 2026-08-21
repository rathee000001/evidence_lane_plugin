---
description: Query or extend the bounded project-isolated Agent Learning and cross-sector memory authority.
---

# Evidence Lane Agent Learning

Apply the installed `evi-learning` skill and its fail-closed MCP routing
contract. This command is a host convenience surface, not another authority.

For recall, use `learning_memory_query` or `learning_retrieve` with an exact
project, timestamp, sector/scope filter, and result limit. Return only the
bounded FTS5/BM25 slice; never return a full SQLite database, Markdown corpus,
Plan, PV, ChatLineage, or private reasoning.

For a new cross-sector relationship, use `learning_memory_record_link` with two
typed content-addressed locators and one governed edge. For host memory, use
`learning_record_host_memory_import` only after explicit user or workflow
authority and require the exact source locator, record hash, task, Delta, PV,
actor, purpose, and unchanged Project Truth pointer.

Every write remains project-isolated and append-only. It creates no candidate,
invokes no HIL, moves no Project or Learning pointer, and performs no Git,
install, deployment, or automatic host-memory scan. Candidate sealing and the
separate Learning decision remain explicit later actions.

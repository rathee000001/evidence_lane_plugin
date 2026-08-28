---
name: evi-universe
description: Inspect the linked Project Universe and connector-brain integrity graph through the live ENV/UOP-governed query route without merging authority roles.
---

# Evidence Lane Project Universe

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its hook/skill ownership
contract. This skill owns the Project Universe read workflow; adaptive Delta
exit owns Universe refresh.

Project Universe and connector brain are linked operational graphs derived from
the live project sectors and exact task/source/connector identities. They are
not separate replacements for the six-way working arms and do not replace
Plan, Project Memory, Canon, AI Learning, or lanes.

1. Call `pv_status` and `pv_task_backlog` for exact current identity.
2. Call one bounded live-root `pv_query` and `search`; consume only the returned
   Project Universe locator slice, Universe graph hash, connector-brain
   integrity slice, and connector graph hash.
3. A stale/no-hit Universe or connector-brain arm may refresh once only through the governed
   Learning -> Canon -> Memory -> Universe sequence. Continuing no-hit is valid
   and the live sector slice remains available.
4. Universe refresh occurs at adaptive Delta exit, not from this read skill and
   not from a hook.

Never query accepted ZIP storage, load either full graph into model context,
create a candidate, infer HIL, move a pointer, or merge authority roles.

## MCP routing contract

Read `../evi/references/mcp-tool-routing.v1.json` and use the ordered
`evi-universe` route. Missing or ambiguous tools fail closed.
`MCP_ROUTING_FAIL_CLOSED` applies to every call.

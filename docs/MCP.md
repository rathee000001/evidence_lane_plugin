<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / 2026-08-23 -->

# Evidence Lane 3.0.0 native MCP

Evidence Lane exposes one package-local native MCP server named
`evidence-lane`. The canonical namespace is `mcp__evidence_lane__`; a
collision-safe host display suffix does not change the canonical tool
identity.

The 3.0.0 catalog contains exactly 88 canonical actions: 27 read-only and 61
write-capable. Read operations inspect accepted PVs, Plan/Delta state, source
and lane evidence, receipts, panels, storage, and runtime identity. Write
operations are individually governed and cannot inherit permission from a
read, a website, a connector, a Git credential, or another authority arm.

Lifecycle writes remain fail-closed. Candidate creation does not accept a
candidate. HIL display does not decide HIL. Fuse requires exact matching
approval. Git push, install, State Travel, storage selection, Canon admission,
Learning decisions, and pointer movement retain separate contracts and
receipts.

| Surface | Exact value | Failure boundary |
| --- | --- | --- |
| Server | `evidence-lane` | Only the package-local native route proves Codex lifecycle execution. |
| Namespace | `mcp__evidence_lane__*` | Generated display aliases are not independent authority. |
| Read-only actions | 27 | A read never grants a later write. |
| Write-capable actions | 61 | Every call revalidates its own state and authority contract. |
| Total actions | 88 | Catalog visibility never implies host support or permission. |
| Console resource | `ui://evidence-lane/governed-console-v6.html` | Rendering is read-only and cannot decide HIL. |
| Durable default | Project-scoped local SQLite | Connectors and artifact mirrors remain separate. |

The server uses durable project-scoped SQLite on proven persistent Codex hosts.
Generated namespaces, direct-stdio aliases, website routes, tunnels, storage
connectors, and additional plugins cannot substitute for native lifecycle
proof.

The 3.0 action additions are split by authority, not hidden behind a generic
SDK endpoint. Canon and Agent Learning account for 21 separately governed
actions:

- Canon Input exposes 16 named actions: three reads and thirteen writes for
  contract/envelope/decision/task-graph/backfire/result/continuity behavior.
- Agent Learning exposes five named actions: two reads and three writes for
  isolated retrieval, candidate sealing, Learning HIL, and revocation.

All 21 actions route through the complete provider-neutral internal SDK arm and
its exact live project/session/task/pointer/ENV-UOP/profile binding. The SDK is
the full engine plus contracts and adapters; it is not counted as a skill or a
generic public authority-merging action. A missing host task-dispatch seam
returns `HOST_CAPABILITY_UNAVAILABLE`.

See the [root architecture](../ARCHITECTURE.md),
[skills contract](SKILLS.md), and [hooks contract](HOOKS.md).

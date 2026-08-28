<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Evidence Lane 3.0.0 native MCP

<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->
## Current backend contract

This public document is refreshed from the same source graph used by the installable plugin package.

- Plugin package: `3.0.0+codex.20260828064341`.
- Native MCP: **91 actions** (**30 read / 61 write**).
- Native skills: **26 governed skills**; the separate command layer is absent.
- Hooks: **11 events / 44 ordered handler actions**.
- SDK: internal action SDK and outer routing SDK remain distinct; public action count **91**.
- ENV/UOP: separate executable authorities with **7 ENV members / 5 UOP members**.
- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.
- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.

Exact backend bindings:
  - `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` — `42A726CD910A27EF9B8987907F02D127789857C8B04E1E214A91D1F74D151A4B`
  - `plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json` — `B571AF9EC31691C96DB0B3845ED0B7A6700D1C84A2578ABA9A2EA594982AF045`
  - `plugins/evidence-lane-plugin/skills/skill-surface-registry.v1.json` — `38B1F95B8160E037B43B209A6D6047BF8BCA4D2599182C2F20E4606B6CBDF3A5`
  - `plugins/evidence-lane-plugin/hooks/hooks.json` — `C37DB05DD4701087EAD0BD31203C843AAFA79ED39A081F2E9DFF313A77631EEF`
  - `plugins/evidence-lane-plugin/sdk/sdk-manifest.v1.json` — `5BD21AEB96D7E41209E3D059D8A5296D851BDED1D453D6EF486C0CD50D745245`
  - `plugins/evidence-lane-plugin/mcp/mcp-manifest.v1.json` — `E9E402C2F20B2BBE63B6BF91613B1C97E85E615F982D52CF6D020408251AFAFB`
  - `plugins/evidence-lane-plugin/env/authority-manifest.v1.json` — `E4F283EC16F86995E2937288DD8A8E5623007351CBB1CA3FD01FDA5C7363B6C1`
  - `plugins/evidence-lane-plugin/uop/authority-manifest.v1.json` — `BBA3CDAE9CC0FF981E5C6E19F83FBBCE6EB2ED8167CDBB2E9D1C557FA03CA57C`
  - `plugins/evidence-lane-plugin/toolchains/TOOLCHAIN_EXECUTION_MATRIX.md` — `E5379D7C4B17BC9293F332216581D60F88ADF73A4B7B361D84D09B47FC4EA66F`
<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->


Evidence Lane exposes one package-local native MCP server named
`evidence-lane`. The canonical namespace is `mcp__evidence_lane__`; a
collision-safe host display suffix does not change the canonical tool
identity.

The 3.0.0 catalog contains exactly 91 canonical actions: 30 read-only and 61
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
| Read-only actions | 30 | A read never grants a later write. |
| Write-capable actions | 61 | Every call revalidates its own state and authority contract. |
| Total actions | 91 | Catalog visibility never implies host support or permission. |
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

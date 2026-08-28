<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Canon task graph and Canon Input HIL

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


Canon is a project-isolated coordination authority for typed communication
between persistent governed tasks. It is not Project Truth, Agent Learning,
ChatLineage, a shared mutable memory, or permission to edit another task.

## Authority boundary

Every envelope binds exact source and destination project IDs, task UUIDs and
deep links, lane and Delta IDs, accepted source PV/generation/manifest/package,
schema and payload hashes, expected contract, evidence references, expiry,
revision, replay identity, and ordered route trace. Runtime validation rejects
secret-bearing payloads and every action that could write source, write Git,
build or promote a candidate, decide another HIL, move a pointer, Fuse, install,
or deploy.

The durable project-local authority is `canon/canon-input.sqlite`, accompanied
by immutable contract, packet, edge, receipt, and State Travel continuity JSON.
Project Truth and Agent Learning identities are sampled before and after every
Canon write. Drift fails closed.

The operational ledger remains the sole decision authority. A separate
content-addressed consequence projection may be refreshed through the private
SDK operation `canon_input:bootstrap_consequence_graph`. It binds the exact
project layout, all 18 sector references, accepted PV/generation/manifest,
active Plan task, Plan/steer database, Learning ledger, Canon ledger, host task
UUID/deep link, and ChatLineage head. Each input fingerprint produces one
immutable SQLite, Mermaid, DOT, manifest, and receipt bundle under
`canon/consequence-graphs/`; `canon/consequence-graph-current.json` points to
the verified current bundle. Exact replay reuses the bundle without reporting
a write effect.

The projection records task dependencies, steer links, Learning provenance,
typed Canon handoffs, result returns, backfires, sector bounds, and the active
host-task relationship. It is a bounded graph index, not another decision
store: refresh cannot admit Canon input, accept Learning, create a Project
candidate, invoke any HIL, infer ordinary approval, or move either pointer.
`canon_graph` returns its counts and hashes only; raw Plan/Learning rows and the
full graph are not loaded into model context.

## Admission state machine

An envelope starts at `PROPOSED`, then the receiving project records
`RECEIVED` and `VALIDATED`.

- An exact active expected-contract match becomes `EXPECTED_ADMITTED` without
  a human prompt.
- An undefined or incompatible packet becomes `PENDING_HIL` in the receiving
  top-level task only.
- That receiver may issue exactly `ACCEPT`, `REJECT`, or `MORE_RESEARCH`.
- `ACCEPT` admits only that immutable packet revision as bounded Canon input.
- `REJECT` preserves the packet, reason, receipt, and source-notification need.
- `MORE_RESEARCH` preserves a bounded field request and requires a new linked
  revision. The predecessor becomes `SUPERSEDED`; it is never rewritten.

An exact replay returns the prior receipt. A changed actor, reason, request,
timestamp, packet hash, or revision is not an idempotent replay.

## Linked task graph

Typed edges support upstream, downstream, and lateral task links, including
fan-out and fan-in. Edges bind the exact source/destination tasks, contracts,
dependencies, paths, tools, execution scope, return contract, expiry, and host
write receipt. Cycles and self-links fail closed.

Top-level destinations may own their own independent HIL. Subagents never own
HIL, cannot receive lifecycle/pointer/Fuse/Git tools, and require current user
authority before launch. Every governed read-write edge additionally requires
an external host task contract and exact bounded paths. Canon itself never
grants that write authority.

The provider-neutral dispatcher calls a supported host create-task operation
once and then seals the returned task UUID/deep link. If a provider cannot
programmatically create the task, it reports `HOST_CAPABILITY_UNAVAILABLE`;
the engine does not fabricate a destination or bind by title/CWD. The local SDK
therefore exposes the full dispatch contract while truthfully leaving that one
provider-owned operation unavailable.

## Results, backfire, and State Travel

A destination binds the immutable received edge before sealing a typed
`TASK_RESULT` envelope to the source. No local Project HIL, Learning HIL,
pointer state, or source-write authority propagates with the result.

Backfire is conditional: it is allowed only when admitted input exposes a
bounded upstream failure, missing source information, new source requirement,
or linked-task input need. It seals one correction packet to the exact
recipient, requires a newer revision, deduplicates identical requests, rejects
changed bytes under the same dedup identity, blocks route cycles, and never
automatically retries. Expected responses auto-admit; undefined or incompatible
responses stop at the exact recipient's three-way Canon Input HIL.

State Travel may carry pending Canon locators and hashes as continuity context.
It cannot carry or replay a Canon decision, mutate Canon state, or move the
Project Truth pointer. Consumption is bound exactly once to the destination
task/deep link/host-session tuple.

## Schemas and proof

The package contains:

- `schemas/canon-envelope.schema.json`
- `schemas/canon-expected-contract.schema.json`
- `schemas/canon-task-edge.schema.json`
- `schemas/canon/canon-consequence-graph.v1.sql`

`tests/test_canon_task_graph.py` covers expected and undefined admission,
receiver ownership, all three decisions, replay conflict, revision and
supersession, cross-project dispatch/result return, subagent denial, cycles,
backfire deduplication, secret and authority escalation rejection, and
State Travel's no-decision boundary.
`tests/test_canon_consequence_graph.py` covers exact project/pointer/task
binding, all-sector projection, Plan/Learning edges, immutable replay, SDK
ownership, and fail-closed mismatch handling.

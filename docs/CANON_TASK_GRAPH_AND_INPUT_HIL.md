# Canon task graph and Canon Input HIL

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

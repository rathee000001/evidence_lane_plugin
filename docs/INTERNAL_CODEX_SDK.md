# Internal Codex SDK and headless adapter

The Evidence Lane SDK is the complete private Codex-layer engine contract. It
is not a reduced retrieval client, a public package, or a new authority. The
top-level `InternalEvidenceLaneSDK` validates and routes calls to independently
namespaced modules; each module retains its own schema, storage, permissions,
pointer rules, replay ledger, tests, and receipts.

## Authority modules

| Module | Authority boundary |
| --- | --- |
| `project_truth` | Accepted PV reads and immutable Project Truth retrieval |
| `canon_input` | Full typed Canon contract/packet/revision store, inbox and graph queries, content-addressed consequence projection, three-way receiver-owned Canon HIL, result/backfire routing, and State Travel continuity |
| `agent_learning` | Project-isolated AI/Agent Learning candidates, retrieval, and Learning HIL |
| `project_memory` | Independent content-addressed locator/edge authority, bounded FTS5/BM25 query, and compaction checkpoint/rehydration continuity |
| `chat_lineage` | Secret-redacted visible lifecycle history and hash chain |
| `host_entry_continuity` | Exact Exit-to-Entry continuity without Project promotion |
| `lifecycle_hooks` | Boot/resume, lifecycle transport, visible events, and exit sealing |
| `plan_delta_tasks` | Canonical Plan Lane, Delta classification, task state, and host projection |
| `source_lane_retrieval` | Bounded source intake and all-lane search/fetch/query |
| `env_uop_operator_runtime` | ENV/UOP identity plus Formula/PCM/MBA compilation and routing |
| `storage_connectors` | Local durability, separate storage connectors, and governed plugin routing |
| `hil_candidate_pointer` | Candidate, six-way Project HIL, Fuse, and pointer-only Rollback contracts |
| `provider_host_adapters` | Codex/headless capability negotiation and provider-specific mapping |

The ABI catalogs the complete layer even when a particular host implements
only a subset. An absent adapter operation returns
`HOST_CAPABILITY_UNAVAILABLE`; the router does not invent an emulation or use a
different authority arm as fallback.

## Exact call binding

Every invocation binds the project, governed session, active Delta task,
accepted PV and pointer generation, manifest, ChatLineage head, separate ENV
and UOP authorities, derived projection and Flash receipt, model/submodel,
reasoning effort/speed, host and host session, and exact write grants. A model,
pointer, task, lineage, Flash, namespace, or write-scope mismatch fails before
the provider handler runs.

Each authority module persists idempotency receipts in its own SQLite replay
ledger. A restart, compaction, or cache loss can reuse the same request only
when the complete request and binding hashes match. Reusing an ID with changed
input fails as `SDK_REPLAY_CONFLICT`. Parallel projects cannot share replay
state.

## Retrieval and effects

Project Truth and Agent Learning retrieval return two named slices. Their hits
are never concatenated, re-ranked, or promoted by the SDK. Canon Input and
Agent Learning cannot move the Project Truth pointer. Canon is a bounded
cross-task coordination authority; Learning is the separate AI/Agent Learning
arm. Project Memory is a third independent retrieval arm: it stores only
content-addressed locators and relationships, labels every source authority,
and cannot promote Project Truth or Learning. The legacy public action names
`learning_memory_query` and `learning_memory_record_link` route to this SDK arm
without changing the public action count. Formula Engine compiles and routes
operators but is not the learner.

All payloads and results are size-bounded and secret-rejected before replay.
Calls use bounded timeouts and cooperative cancellation checkpoints. Write
operations require an exact `module:operation` grant, and a module may report
an effect only for the authority explicitly owned by that operation.

The companion GitHub App production-delivery seam is similarly bounded but is
not a Project Truth authority. `ProductionDeliveryIdentity` binds the exact
installation capability, repository ref, green Actions head, package, installed
branch-commit recovery slot, mutable local slot, and unchanged main-merge
fallback. The public
`scripts/codex_release/seal_github_app_production_delivery.py` adapter seals
those non-secret identities after the external operations occur. It cannot
create a commit, push a ref, install a package, infer HIL, or move a pointer.

Repository mutation is owned by the distinct `GitHubAppExactCommitPushRoute`.
It requires the canonical Evidence Lane bot for author and committer, preserves
ordered merge parents, recreates exact Git objects through one short-lived App
installation token, and fast-forwards only the exact governed feature branch.
Its receipt is not an acceptance, HIL, pointer, install, or publication event.

The current local service adapter exposes grounded read surfaces first. The ABI
retains the full engine contract. Its Canon arm now binds the complete local
engine: inspect, inbox, graph, expected-contract registration, envelope sealing,
classification/receive, exact decision and supersession, source/destination
edge binding, conditional backfire, result sealing, and State Travel continuity.
The private `bootstrap_consequence_graph` write derives every input from the
validated SDK binding and live project authority; payload overrides are
forbidden. It materializes one immutable graph bundle per authority fingerprint
and reports only a Canon-owned effect on first creation. `graph` adds the
bounded current consequence summary without returning raw authority rows.
`dispatch_linked_task` remains a provider-host operation: the ABI declares it,
but the local service adapter reports it unavailable until the host supplies an
exact programmatic create-task seam. It never invents a task or substitutes a
manual/title/CWD binding.

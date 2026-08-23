<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Project Memory

Evidence Lane Memory is the queryable cross-sector recall plane of one project.
It connects project evidence without merging the independent authorities that
produced it. A model context window, transcript, Markdown dump, host memory,
cache, or compacted summary is not the project database.

## Memory versus other authorities

| Surface | Owns | Does not own |
| --- | --- | --- |
| Project Memory | Bounded locators, cross-sector relationships, retrieval context, compaction continuity | Acceptance, HIL, task order, source writes |
| Project Truth | Accepted project evidence, PVs, candidate and pointer lifecycle | Host recall or Learning lessons |
| ChatLineage | Visible prompt, steer, response, entry, exit, and event history | Project acceptance |
| Canon Input | Typed task-to-task packets and graph edges | Project or Learning promotion |
| AI Learning | Evidence-backed reusable lessons and its separate pointer | Project Truth |
| Host memory | Optional generated recall owned by the host | Any Evidence Lane authority |

The Plan runtime records Memory SQLite as
`SEPARATE_FROM_AI_LEARNING_AND_PROJECT_TRUTH`. That separation is a contract,
not a presentation choice.

The first-class authority lives under the governed project root at `memory/`:

- `memory.sqlite` owns locators, typed edges, immutable migration receipts,
  content-addressed heads, compaction checkpoints, and rehydration receipts;
- `memory.json`, `memory.mmd`, and `memory.dot` are hash-bound projections;
- `memory.tools.json` declares bounded query and compaction behavior;
- `head.json` and `memory.manifest.json` bind the active head and every
  projection member; and
- `checkpoints/` and `rehydration/` contain immutable continuity receipts.

The historical `memory_locator`, `memory_edge`, and FTS tables inside Agent
Learning v2 remain readable only as an immutable migration source. Migration
copies their original content-addressed IDs and records source-ledger
provenance; it does not delete or rewrite them. New locator and edge writes are
owned by Project Memory.

## Storage and retrieval

Each fired lane keeps its own SQLite, schema, hashes, FTS5 records, BM25 search,
MMD, DOT topology, tools manifest, and pointer/receipt contracts. Memory queries
those lanes through stable IDs and bounded locators. It does not load the full
PV, Plan, ChatLineage, or lane database into model context.

Memory retrieval must therefore return:

- the exact project and authority head;
- bounded result rows and FTS locators;
- source, schema, and content hashes;
- graph relationships needed for the current task;
- exclusions, conflicts, and no-hit state; and
- explicit proof that no authority or pointer was promoted.

The existing public action names `learning_memory_query` and
`learning_memory_record_link` are compatibility names. They route to the
independent `project_memory` SDK arm (`query` and `record_link`) and do not make
Agent Learning the Memory owner. This preserves the 88-action public catalog
while correcting authority ownership.

Parallel lane and cross-lane queries may run when the route contract permits
them. Cross-project queries require separate exact project bindings; there is
no implicit default project or cross-project fallback.

## Compaction continuity

`PreCompact` seals the visible task, Plan row, authority heads, active source,
and bounded continuation locators before host compaction. `PostCompact`
rehydrates only that verified continuation slice. Neither hook controls when
the host compacts, and neither may reconstruct the project from chat history,
replay HIL, change task order, or load a full SQLite authority into context.
The same checkpoint and rehydration operations can run explicitly while hooks
remain disabled; automatic transport begins only after each hook is separately
verified and enabled.

This preserves the same state across context windows:

```text
active task + Plan row + exact authority heads + bounded locators
                              |
                         PreCompact seal
                              |
                         host compaction
                              |
                        PostCompact verify
                              |
                    same bounded working state
```

## Learning and Canon linkage

Memory can expose a bounded Canon packet, ChatLineage event, accepted project
fact, or Learning lesson to the current task. The consumer sees the authority
label and provenance for every result. Memory does not rerank separate
authorities into one unlabeled corpus and cannot turn retrieval into approval.

At a Delta checkpoint, linked ChatLineage and test evidence may support an AI
Learning candidate. At a Project HIL, accepted Learning and Canon remain
separate inputs; only the exact Project decision can move the Project pointer.

## Schema evolution

A lane schema is a first-use baseline, not a permanent ceiling. Additive rows,
columns, tables, sheets, registries, and relationships may be introduced by a
governed schema-evolution receipt when a project requires them. Destructive or
ambiguous rewrites fail closed. Existing records, hashes, accepted Git/local
code evidence, and historical receipts remain immutable.

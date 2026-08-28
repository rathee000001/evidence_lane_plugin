<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Project Memory

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

The current public action names are `project_memory_query` and
`project_memory_record_link`. They route to the independent `project_memory`
SDK arm (`query` and `record_link`) and do not make Agent Learning the Memory
owner. Superseded Learning-as-Memory compatibility actions are absent from the
installed runtime; immutable historical evidence is retained outside the live
dispatch surface and is never an executable fallback.

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

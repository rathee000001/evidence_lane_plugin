<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# AI Learning

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


Evidence Lane AI Learning is a project-scoped authority for reusable lessons.
It is separate from Project Truth, Canon Input, Project Memory, ChatLineage,
host memory, Plan, and the accepted Project PV pointer. Brain scaling means bounded indexed
retrieval and composition; it does not mean autonomous training.

## What Learning can record

An evidence-backed Learning candidate may describe a procedural lesson,
failure-avoidance rule, relationship, tool route, host compatibility fact, or
other reusable project behavior. Every candidate binds:

- exact project, task, Delta, and PV context;
- lesson type, tier, scope, and temporal validity;
- evidence and counterevidence references;
- outcome, calibrated confidence, contradictions, and supersession;
- privacy class and exact ChatLineage head; and
- a stable identity and content hash.

Actor/model evidence remains in linked ChatLineage rather than being copied
into the lesson.

## Independent lifecycle

The Learning family has five public actions:

1. `learning_inspect`
2. `learning_retrieve`
3. `learning_seal_candidate`
4. `learning_decide_candidate`
5. `learning_revoke`

The provider-neutral internal SDK also exposes
`bootstrap_verified_history`. It is deliberately not another public MCP
action: it deterministically seals unaccepted candidates from approved
historical Plan outcomes and exact verified forward-Delta checkpoints. Rows
that are merely DONE, ambiguous, dropped, superseded, or unverified are
excluded. Replays reuse candidate identities and the immutable bootstrap
receipt; Project Truth, both HIL surfaces, and both pointers remain untouched.

Sealing creates `PENDING_LEARNING_HIL`. It creates no Project candidate and
moves no pointer. Learning owns its own six-way decision surface:
`APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`, pointer-only Learning
rollback, `REJECT`, and `FAIL`. A Learning decision may move only the Learning
pointer. It never authorizes Project Fuse, Git, installation, deployment,
Canon acceptance, or State Travel.

Accepted, rejected, revoked, expired, rolled-back, and superseded lessons stay
immutable in history. Retrieval excludes ineligible lessons with an explicit
reason rather than deleting them.

## SQLite and bounded retrieval

The project `learning/` authority stores immutable candidates, append-only
events, decision receipts, pointer generations, canonical JSON artifacts, and
an FTS5 projection. Retrieval uses BM25 and returns only the requested bounded
slice. The full Learning ledger is never loaded into model context or silently
concatenated with Project Truth.

No hit is a valid governed result. A Project-Truth conflict suppresses the
lesson rather than changing accepted project evidence.

## Host-memory boundary

Codex or ChatGPT host memory is optional generated recall, not Evidence Lane
authority. It is never scanned or promoted automatically. A host-memory fact
may enter Learning evidence only through an immutable
`host-memory-import://<receipt-sha256>` provenance receipt binding the exact
project/task/Delta/PV, source-record hash, context hash, purpose, actor, times,
and unchanged Project Truth pointer. Raw memory text, secrets, and private
reasoning are not persisted by that receipt.

Hooks may transport visible lifecycle events but cannot import, decide, or
promote Learning. The Formula Engine may compile and route ENV/UOP operators;
it is not the learner.

## Cross-sector role

ChatLineage supplies visible event provenance. Canon may provide bounded input.
Memory may retrieve linked project facts. AI Learning may then propose a lesson
whose evidence points back to those immutable records. The authorities remain
separate throughout that flow and keep separate schemas, pointers, receipts,
failure states, and HIL decisions.

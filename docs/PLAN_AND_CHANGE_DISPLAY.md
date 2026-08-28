<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Plan and change display

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


The native Plan Lane is the sole authority for task order, dependencies,
status, completed history, queued HIL rows, and the physically final HIL. The
Codex Step Task List and Changes panel are bounded host projections of that
authority; they are not a second Plan database.

## One active row

Exactly one executable row may be `in_progress`. Rows advance only after the
current row's exact acceptance evidence passes. A transition changes Plan state
only; it does not create a candidate, infer HIL, Fuse a PV, or move the accepted
pointer.

Linked steers append immutable corrections to their existing task. Unrelated
work receives one complete new Delta row at the governed insertion point.
Completed rows move into Plan history and are never deleted or silently
reconstructed.

## Host 1+9 projection

The persistent host list shows:

1. one compact progress/header tracker; and
2. the active Delta plus its next eight executable rows.

Each Delta uses at most three compact lines: stable task ID and status;
class/group/dependency/graph pointer; and a human-readable outcome with its FTS
locator. Git appears only on the row where Git actually runs.

Native Codex marks a row complete while the same batch remains visible. The
list rehydrates only when the current 1+9 membership changes, the tenth visible
step finishes, or host UI loss is detected. It must not shift one row after
every completion. If a new steer changes the current queue, the Plan and Goal
order update atomically before implementation resumes.

## Bounded retrieval

The full Plan stays in durable SQLite outside model context. Reads use exact
task IDs or bounded FTS5/BM25 windows. Writes return compact receipts containing
counts, hashes, the active row, and host-window effect—not the full backlog.

The change display may show active task/Delta identity, source and installed
package identity, activation/Refresh state, tool/skill/hook counts, and exact
receipts. It does not own Codex's native file-change UI or invent host placement.

## Plan acceptance and HIL

The native “Implement this plan” action accepts only the proposed Codex Plan
projection. It is not Evidence Lane HIL and cannot approve a candidate. Goal
activation resumes the exact accepted Plan row; it must not reorder back to a
previous task.

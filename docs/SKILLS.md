<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Evidence Lane 3.0.0 skills

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


The current Codex package derives 25 governed skills from the installed skill
folders and routing workflows; this is an exact current inventory, not a
permanent ceiling. Skills are the behavior
and sequencing layer: they seal prompt intake, request native reads, classify the
task and authority boundary, restore the Plan projection, enforce candidate and
HIL order, and explain the next permitted action. A skill never becomes
Project Truth merely because it is installed.

The six primary controls are Boot, Rollback, Build, Refresh, Mode, and Source
Intake. State Travel, storage, plugin administration, Exit Boot, Plan pairing,
and the lifecycle contract are explicit conditional or administrative
surfaces. The root `/evi` router shows the six controls in stable order and
does not silently select State Travel.

Skills and hooks are deliberately separate. Hooks carry lifecycle events;
skills own the governed decision procedure. Skills and MCP are also separate:
the skill asks for an operation, while the package-local native MCP returns the
authority-backed receipt.

| Skill | Class | Primary | Exact responsibility |
| --- | --- | --- | --- |
| `evi` | Router | No | Present the six controls and conditional State Travel. |
| `evi-boot` | Lifecycle | Yes | Verify runtime, host/storage matrix, model-plus-Effort compatibility, Flash, session, and accepted pointer. |
| `evi-rollback` | Lifecycle | Yes | Select Plan-stamped logical full-PV/sub-PV state; keep hard restore separate. |
| `evi-build` | Lifecycle | Yes | Bootstrap PV0 or construct and present an unaccepted dual Project/Learning full-PV HIL. |
| `evi-fuse` | HIL decision/promotion | No | Govern separate Project/Learning decisions; only exact dual approval may promote. |
| `evi-refresh` | Lifecycle | Yes | Route the three distinct steer, Delta-exit, and HIL-Overlay refresh workflows. |
| `evi-mode` | Mode | Yes | Apply ordered mode/operator intersections. |
| `evi-source-intake` | Source | Yes | Route bounded sources across all canonical lanes. |
| `evi-state-travel` | Continuity | No | Run native session resume plus the six-field direct exact-work route; compatibility prepare/resume routes are absent. |
| `evi-canon` | Task coordination | No | Govern task-to-task Canon, linked task/subagent dispatch, three-way Canon HIL, backfire, results, and State Travel graph continuity. |
| `evi-learning` | AI learning | No | Govern project-isolated Learning retrieval, candidates, Learning HIL, and revocation without changing Project Truth. |
| `evi-memory` | Project memory | No | Query and link the independent Project Memory FTS5/BM25 locator graph. |
| `evi-instructions` | Instructions/recall | No | Resolve AGENTS.md scope and host MEMORY.md recall without merging them. |
| `evi-universe` | Linked graph | No | Query Project Universe and connector-brain integrity through bounded live-root reads. |
| `evi-storage` | Storage | No | Inspect or select eligible persistence. |
| `evi-plugin` | Administration | No | Govern the additional-plugin/toolchain catalog. |
| `evi-additional-plugin` | Connector | No | Add one bounded connector/toolchain grant. |
| `evi-drop-additional-plugin` | Connector | No | Revoke one exact grant without deleting history. |
| `evi-exit-boot` | Session | No | Close one governed session while retaining installation and evidence. |
| `evi-formula` | ENV/UOP execution | No | Compile and route one bounded, effect-checked Formula Engine request. |
| `evi-brain-scaling` | Bounded context | No | Select deterministic hash-addressed indexed slices within token and item budgets. |
| `evi-project-recipe` | Source orchestration | No | Compile the exact project-type Source Intake and lane execution recipe. |
| `evi-toolchain` | Conditional tooling | No | Resolve the exact Codex-host toolchain and eligible fallbacks for one lane/action. |
| `evi-bigger-universe` | Cross-project federation | No | Register and explicitly link hash-only project mini-brains without merging Project Truth. |
| `evidence-lane-code-lifecycle` | Code lifecycle | No | Enforce the full one-writer build, test, package, and HIL law. |

See the packaged skill sources under
[`plugins/evidence-lane-plugin/skills/`](../plugins/evidence-lane-plugin/skills/)
and the [native MCP contract](MCP.md).

<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Evidence Lane 3.0.0 skills

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

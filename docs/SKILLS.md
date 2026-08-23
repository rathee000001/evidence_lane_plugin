<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Evidence Lane 3.0.0 skills

The Codex package contains exactly 17 governed skills. Skills are the behavior
and sequencing layer: they perform PREPARE, request native reads, classify the
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
| `evi-boot` | Lifecycle | Yes | Verify runtime, Flash, host, storage, session, and accepted pointer. |
| `evi-rollback` | Lifecycle | Yes | Move only the accepted pointer among immutable PVs. |
| `evi-build` | Lifecycle | Yes | Seal an unaccepted candidate and present six-way HIL. |
| `evi-refresh` | Lifecycle | Yes | Rebuild changed evidence without erasing prior chunks. |
| `evi-mode` | Mode | Yes | Apply ordered mode/operator intersections. |
| `evi-source-intake` | Source | Yes | Route bounded sources across all canonical lanes. |
| `evi-state-travel` | Continuity | No | Prepare or resume exact unfinished work in a fresh bound task. |
| `evi-canon` | Task coordination | No | Govern task-to-task Canon, linked task/subagent dispatch, three-way Canon HIL, backfire, results, and State Travel graph continuity. |
| `evi-learning` | AI learning | No | Govern project-isolated Learning retrieval, candidates, Learning HIL, and revocation without changing Project Truth. |
| `evi-storage` | Storage | No | Inspect or select eligible persistence. |
| `evi-change-storage-connector` | Storage | No | Change one explicit storage connector compatibly. |
| `evi-plugin` | Administration | No | Govern the additional-plugin/toolchain catalog. |
| `evi-additional-plugin` | Connector | No | Add one bounded connector/toolchain grant. |
| `evi-drop-additional-plugin` | Connector | No | Revoke one exact grant without deleting history. |
| `evi-exit-boot` | Session | No | Close one governed session while retaining installation and evidence. |
| `evidence-lane-code-lifecycle` | Code lifecycle | No | Enforce the full one-writer build, test, package, and HIL law. |

See the packaged skill sources under
[`plugins/evidence-lane-plugin/skills/`](../plugins/evidence-lane-plugin/skills/)
and the [native MCP contract](MCP.md).

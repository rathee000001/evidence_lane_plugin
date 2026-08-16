# Evidence Lane command and control routes

This document is the Git-tracked authority for the public Evidence Lane command
surface. Commands select governed workflows; native MCP actions execute the
bounded operations underneath them. Discoverability is not approval.

## Primary controls

Root `/evi` presents exactly six everyday controls, in this order:

| Command | Skill owner | Purpose |
| --- | --- | --- |
| `/evi-boot` | `evi-boot` | Verify the package, ENV/UOP Flash, host, storage, project session, and accepted pointer. |
| `/evi-rollback` | `evi-rollback` | Move only among immutable accepted pointers through the governed rollback contract. |
| `/evi-build` | `evi-build` | Seal an unaccepted candidate and stop at its exact six-way HIL. |
| `/evi-refresh` | `evi-refresh` | Recompute changed evidence while preserving content-addressed history. |
| `/evi-mode` | `evi-mode` | Apply an ordered mode/operator intersection without changing lifecycle authority. |
| `/evi-source-intake` | `evi-source-intake` | Classify and route bounded sources across the canonical lanes. |

Each control has one deterministic direct map to its existing owning skill.
The exact slash command and a conservative, unambiguous ordinary-language
intent select the same skill; route selection itself executes no lifecycle
action, and the selected skill must still run its native gates. Ambiguous
multi-control wording fails closed. Host Plan/Step Task List activation is a
separate host-surface operation and never aliases `/evi-refresh`; Refresh means
the governed changed-evidence/PV lifecycle only.

## Conditional and administrative routes

| Command | Skill owner | Boundary |
| --- | --- | --- |
| `/evi-state-travel` | `evi-state-travel` | Exact unfinished-work transfer between bound Codex tasks; not a seventh primary control. |
| `/evi-plan` | `source-command-evi-plan` | Host Plan projection/verification sidecar; it does not write a second Plan authority. |
| `/evi-canon` | `evi-canon` | Typed, bounded task-to-task or subagent exchange with independent Canon state and decisions. |
| `/evi-learning` | `evi-learning` | Project-isolated AI Learning retrieval, candidate, decision, and revocation. |
| `/evi-storage` | `evi-storage` | Inspect or select eligible project-scoped persistence. |
| `/evi-change-storage-connector` | `evi-change-storage-connector` | Compatibility route for one explicit storage-connector change. |
| `/evi-plugin` | `evi-plugin` | Inspect and govern the additional plugin/toolchain catalog. |
| `/evi-additional-plugin` | `evi-additional-plugin` | Add one purpose-, role-, scope-, and expiry-bound grant. |
| `/evi-drop-additional-plugin` | `evi-drop-additional-plugin` | Revoke one exact active grant without erasing history. |
| `/evi-exit-boot` | `evi-exit-boot` | Close one governed session without uninstalling the plugin or deleting evidence. |

The package currently contains one migrated command compatibility document,
`commands/evi-plan.md`. The other public names are owned by their installed
skills. File count, skill count, command count, and native MCP action count are
different inventories and must not be substituted for one another.

## Authority rules

- A command may prepare, inspect, classify, or invoke only its declared native
  route.
- Canon Input, AI Learning, Project Truth, ChatLineage, and host-entry
  continuity keep separate pointers and receipts.
- A successful command, test, CI run, preview, install, or continued
  conversation does not approve a candidate.
- Only the exact HIL contract for the displayed candidate may authorize Fuse.
- If the required native route or host capability is unavailable, the command
  fails closed and records that limitation instead of improvising a fallback.

See [SKILLS.md](SKILLS.md), [MCP.md](MCP.md), and [HOOKS.md](HOOKS.md) for the
separate governance, execution, and lifecycle-transport inventories.

---
name: select-project-storage
description: "Register or inspect persistent local project state and its separate lane databases. Use for local project root selection."
---

# Select project storage

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read `storage_status` for distinct source, state, engine and
plugin roots, the current local-persistence observation and each published lane
reference. For authorized creation or
registration, call `project_register` with exact user-selected absolute roots
through the owner-granted native channel. A new project requires a separate
external state root and source root; preserve existing bytes and identities.
The selected state root is the complete PV root. The registration workflow
classifies the explicit source first, creates that one source lane as a direct
child, records its governed Source Intake, then initializes Plan, ChatLineage,
task exchange, Project Memory, Learning, Sources, Sessions, Receipts and
Universe as direct lane children in their declared order. It creates no
`authorities` or `sectors` wrapper and does not create unrelated sector lanes.
For a local code project, Code is schema-ready and source-registered during
registration; the content index remains pending until an admitted Plan task
runs the Code indexing operation. Do not claim an empty lane shell is an index.
Set its `display_name`, `sensitivity` label and `capture_route` when creating
it. The default is full visible capture and a PRIVATE metadata label; this
label does not claim encryption. Registration choices are immutable. Existing
roots report whether these choices were bound; opening them does not invent
a selection or rewrite their state. `ENV_BUILDER_SPARSE` omits conversational
payloads while preserving exact hashes, attributed controls and governed receipts.
Use the Boot workflow to select the registered project. The engine owner's
`storage-policy.json` must declare the selected local state root as persistent;
an undeclared root fails Boot/Resume. A path alone is not durability proof.
There is no storage connector, VM/sandbox route, remote primary selection or
cloud-drive primary. Evidence Lane supports persistent local Windows Codex
Desktop Stable and Beta hosts.

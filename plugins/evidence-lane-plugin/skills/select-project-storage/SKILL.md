---
name: select-project-storage
description: "Inspect or explicitly select durable project state and its separate lane databases. Use for storage routing and project root selection."
---

# Select project storage

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read `storage_connector_inspect` for the hash-verified selection
ledger and current backend evidence. `storage_connector_select` records AUTO,
LOCAL_SQLITE or CONFIGURED_DURABLE_CONNECTOR with the exact current event digest,
reason and `SELECT_STORAGE:<MODE>[:<connector_id>]` confirmation from the authorized
intent. The configured connector ID is the authenticated remote engine server ID.
A saved preference does not redirect a connection or migrate project state.
Boot/Resume enforces the policy on the connected backend. Local storage requires
an explicit owner declaration for the state root in the engine's startup
`storage-policy.json`; no declaration means unverified. Remote storage requires
the existing scoped HTTPS configuration and current restart-recovery probe.
Physical disk lifetime remains storage-administrator-declared. Blob mirrors are never primary.
Read `storage_status` for distinct source, state, engine and
plugin roots and each published lane reference. For authorized creation or
registration, call `project_register` with exact user-selected absolute roots
through the owner-granted native channel. A new project requires a separate
external state root and source root; preserve existing bytes and identities.
Set its `display_name`, `sensitivity` label and `capture_route` when creating
it. The default is full visible capture and a PRIVATE metadata label; this
label does not claim encryption. Registration choices are immutable. Existing
roots report whether these choices were bound; opening them does not invent
a selection or rewrite their state. `ENV_BUILDER_SPARSE` omits conversational
payloads while preserving exact hashes, attributed controls and governed receipts.
Use the Boot workflow to select the registered project. A filesystem path is
not proof of physical durability. An ephemeral VM needs the verified remote
transactional route, not a cloud-drive artifact carrier or copied SQLite file.

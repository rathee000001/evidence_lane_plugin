---
name: evi-change-storage-connector
description: Compatibility sidecar for project-scoped Evidence Lane storage inspection and selection.
---

# Change Storage Connector

Follow the complete `evi-storage` contract. Call `storage_connector_inspect`
first. Only after the exact `SELECT_STORAGE:<MODE>[:connector-id]` token may
`storage_connector_select` run once. Store no credentials, fail closed when the
selected capability is unavailable, and keep Google Drive an optional sealed-
artifact mirror rather than primary runtime authority. Use those canonical bare
tool names only; never copy a connector display namespace into the contract.

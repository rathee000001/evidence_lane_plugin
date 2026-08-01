---
name: evi-change-storage-connector
description: Compatibility sidecar for project-scoped Evidence Lane storage inspection and selection.
---

# Change Storage Connector

Follow the complete `evi-storage` contract. Inspect first, require the exact
`SELECT_STORAGE:<MODE>[:connector-id]` token, store no credentials, fail closed
when the selected capability is unavailable, and keep Google Drive an optional
sealed-artifact mirror rather than primary runtime authority.

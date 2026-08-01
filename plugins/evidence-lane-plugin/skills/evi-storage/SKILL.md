---
name: evi-storage
description: Inspect or select Evidence Lane primary storage routing with an append-only, secret-free receipt.
---

# Evidence Lane Storage Sidecar

This is an administrative sidecar, not a seventh primary `/evi` control.

1. Call `storage_connector_inspect` before any selection.
2. `AUTO` selects durable local SQLite on Codex desktop/CLI and requires a
   configured transactional connector on an ephemeral server.
3. `LOCAL_SQLITE` fails closed unless the MCP server has durable local storage.
4. `CONFIGURED_DURABLE_CONNECTOR` requires a connector ID and a runtime-capable
   configured service; environment variable names may be governed, but secret
   values are never persisted.
5. Require exactly `SELECT_STORAGE:<MODE>` or, for a durable connector,
   `SELECT_STORAGE:CONFIGURED_DURABLE_CONNECTOR:<connector-id>`.
6. Re-inspect after selection. Never describe Google Drive as primary runtime
   storage; it is an optional sealed-artifact mirror.

---
name: evi-drop-additional-plugin
description: Revoke one active Evidence Lane plugin grant while preserving its append-only history.
---

# Drop Additional Plugin

Inspect the catalog, identify exactly one active grant, and call
`connector_plugin_drop` only with case-sensitive `DROP:<plugin-id>`. Preserve
the registration and event history. Do not uninstall Evidence Lane, detach
Boot, accept a candidate, or move a pointer.

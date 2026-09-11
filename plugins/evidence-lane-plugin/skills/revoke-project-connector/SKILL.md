---
name: revoke-project-connector
description: "Revoke one Evidence Lane project connector grant while preserving its recorded history. Use for explicit withdrawal of connector access."
---

# Revoke project connector

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read `connector_read` to identify the exact
current connector, then call `connector_revoke` under existing authorization.
Read back its revocation and retain its historical receipts. Do not delete
project data, revoke a different connector or infer that revocation uninstalls
the external provider or the shared local toolchain.

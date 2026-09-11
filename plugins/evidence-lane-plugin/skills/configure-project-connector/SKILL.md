---
name: configure-project-connector
description: "Configure an Evidence Lane project connector and its bounded grant. Use to add or change connector access within that project."
---

# Configure project connector

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read the selected project's connector state, then
use `connector_configure` for one concrete authorized connector definition.
Specify its purpose, role schema, actions, physical lanes, read/write roots,
exact external resource IDs, host, backend ID and exact backend version,
configuration environment names and expiry. Select the backend and role from
the operation's registered adapter binding; saving configuration cannot load
code or create a new adapter. Pass the expected prior
version for an update. Do not put credential values in arguments or receipts.
Read back the registered version and report backend configuration/readiness
separately. This does not grant new project administration or install tooling.

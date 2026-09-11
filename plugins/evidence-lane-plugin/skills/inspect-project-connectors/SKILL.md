---
name: inspect-project-connectors
description: "Inspect connector grants and their runtime readiness within an Evidence Lane project. Use for connector orientation; configuration and revocation have separate workflows."
---

# Inspect project connectors

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Use `connector_read` to inspect current project connector
registrations, grant versions, exact registered backend bindings and expiry.
Its lane choices are the physical authority/sector lanes; action profiles are
listed separately. Use `toolchain_resolve` with the selected owning action and
its typed arguments to check grant, target scope and backend readiness. Separate
external service clients and their explicit configuration from the Engine's
own lane tools and compute providers. Use `$configure-project-connector` for an
authorized addition/change and `$revoke-project-connector` for revocation.
A saved registration is not measured backend execution readiness.

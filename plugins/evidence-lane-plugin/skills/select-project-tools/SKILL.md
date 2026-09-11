---
name: select-project-tools
description: "Inspect and select lane operation tools, ordered fallbacks and measured compute readiness. Use for toolchain resolution or project accelerator configuration."
---

# Select project tools

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Use `toolchain_catalog` for the complete retained shared
installation requirements and each registered operation's adapter routes.
Use `toolchain_resolve` with the exact authorized action and, when checking
target scope, its typed arguments to inspect primary and ordered fallback
readiness without executing or installing anything. Optional adapters require
the exact live project grant, role schema, backend version and scoped targets.
Delta admission pins that binding; a grant change stops queued work. The engine
rechecks grants before each worker/effect boundary and validates role output.
Use `runtime_status` for measured tools, providers, worker state and job health.
Read `accelerator_read` before an authorized
`accelerator_configure` change with its exact revision. Select the lane
operation's primary and ordered fallback tools from current contracts and
recheck readiness, grant, device and budgets before execution.
Distinguish internal code, local dependencies/models, native host tools and
configured external services. The Windows bundle is shared once; the reduced
Mac route cannot claim that bundle. Missing providers remain unavailable.
The engine selects an equivalent fallback before invocation only. A failed or
uncertain operation is not automatically retried. SDK responses and Delta
results include adapter execution evidence and observed dependencies; package
presence is not proof that the dependency ran. An operation with no equivalent
alternate reports that explicitly. Call its normal owning workflow to execute.

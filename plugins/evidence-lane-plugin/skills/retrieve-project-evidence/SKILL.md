---
name: retrieve-project-evidence
description: "Select attributed project evidence within explicit item and estimated-token budgets. Use to fit the relevant evidence into context without merging its owning stores."
---

# Retrieve project evidence

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Collect locators and hashes from the selected owning
authority, then call `brain_slice_select` with explicit priorities, estimated
token counts and item/token budgets. The deterministic selection preserves
authority separation and returns metadata only. Its token counts are caller
estimates; the action does not reverify source contents or measure model tokens.
Fetch selected evidence through its owning bounded read and retain attribution.
Do not treat slicing as training or copy multiple authorities into one store.

---
name: inspect-project-instructions
description: "Inspect the selected instruction chain and explicitly selected host recall without merging either into project evidence. Use for instruction provenance and recall boundaries."
---

# Inspect project instructions

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Use `instructions_inspect` for the selected source-root to
working-directory chain. It preserves override precedence and separates
instruction hashes, workspace recall and optional host recall. Global/host
inspection requires the local owner capability. Report absent or oversized
files accurately and do not scan sibling workspaces.
The result is observed file provenance, not proof of what Codex loaded. Read
applicable files through authorized host tools when their content is needed.
Do not write host/workspace memory without explicit user authorization or merge
these arms into any project authority.

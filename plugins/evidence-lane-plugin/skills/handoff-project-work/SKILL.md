---
name: handoff-project-work
description: "Transfer current project work between explicitly selected clients with exact ownership checks and attributed continuation context. Use for a requested task handoff."
---

# Handoff project work

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read `continuation_read` and `continuation_context` for the
current exact project participants and Plan contract. At a safe boundary use
`continuation_offer` for the explicitly selected destination. The destination
uses `continuation_accept` with the exact offer digest; cancellation uses its
own current contract. Preserve attribution and keep the source closeout-only
after transfer. Bind the resumed project session through the Boot skill.
The offer seals the complete bounded pending task exchange authority locator set for all
transferred participants. Changed task exchange authority activity requires a fresh offer.
`continuation_context` distinguishes that sealed context from current task exchange authority;
`continuation_read` exposes the complete checkpoint. No receiver decision is
replayed. Historical offers without this checkpoint must be cancelled and
replaced before transferring ownership.
Engine client ownership is not native task creation or attestation. Do not
infer an exact task/worktree handoff from a title, PID, client label or context
checkpoint. When the request requires native task identity or same-worktree
continuity, set `require_native_attestation=true` on the offer. An unavailable
attestation stops that transfer; do not turn the flag off to obtain success.
Do not restart the host or create a substitute task to bypass this boundary.

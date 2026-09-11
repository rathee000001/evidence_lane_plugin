---
name: link-project-evidence-network
description: "Register and link hash-only project summaries in a separate cross-project network without merging project data. Use only for an explicitly selected network."
---

# Link project evidence network

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Select a separate coordinator project and use
`project_evidence_network_create` for the named federation. The coordinator cannot be a
member. Its Universe SQLite owns federation metadata; each member retains its
own separate Universe, project evidence head coordinator, lane databases and files. Require coordinator
write permission and current read selections for every referenced member.
Inspect each member with `project_evidence_map_inspect`, then use `project_evidence_network_register`
with the exact snapshot hash and current registration version. The engine
derives and verifies the hashes, advances only changed lane references and
preserves historical mini-brain identities. Unchanged registration is a read.
Use `project_evidence_network_grant` for one explicit expiring relation between two
registered member references, then `project_evidence_network_link` with that grant ID
and named SHA-256 evidence only. A caller's arbitrary hash is not a grant.
`project_evidence_network_revoke` withdraws mutation authority and preserves history.
`project_evidence_network_read` returns bounded recorded references; its read does not
claim current member access. `project_evidence_network_verify` checks bounded stored
integrity. Discover the `universe.federation` lane view to preview/export the
hash-reference graph, including history when selected. Registration captures
one member's coherent snapshot; it does not promise simultaneous freshness
across projects. Never copy raw member payloads or merge Project Truth.

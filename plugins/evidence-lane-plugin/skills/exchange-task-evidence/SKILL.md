---
name: exchange-task-evidence
description: "Exchange typed requirements, evidence and results between explicitly selected project tasks while preserving sender and receiver ownership. Use for a governed task exchange."
---

# Exchange task evidence

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Read `canon_read` for the selected participants, contracts and
exchanges. Use `canon_inbox` for project inboxes or an exact receiver's state-filtered packet
metadata and attributed decision events. Follow both packet and event cursors;
raw payloads are excluded. `canon_inspect` checks bounded Canon storage,
schema identities, object digests and event history. Report its verified and
unverified scope; these reads do not attest native tasks or dispatch.
Use `canon_task_edge_register` to seal an owned task edge with exact destination,
contract hashes and bounded scope. `canon_task_edge_bind` belongs to the current
destination participant; another project's edge is read from its explicitly
authorized source project. `canon_graph` checks every recorded edge within its
budget, allows fan-in and fan-out, rejects cycles, and distinguishes missing local
returns from source-project state that was not read. It does not prove a complete
cross-project graph. Include `edge_id` to enforce its exact project/participant
route, payload schema, input contract and return contract. The destination must
bind its edge before receiving input or sealing a result. Graph binding does not
deliver packets, create a task, authorize a subagent, transfer approvals or grant
any scoped action or tool.
Use `canon_join` for an authorized participant, `canon_expect` for
the exact receiver-owned contract, and `canon_send` for a typed packet carrying
its source evidence. `canon_expect.sender_ids` selects local participants;
`sender_endpoints` pins foreign project/participant identities after authorized
reads. Set `canon_send.destination_project_id` for a foreign destination: this
only seals an immutable source outbox file. In the destination project use
`canon_receive` with the exact source project, exchange ID and envelope digest.
The receiver must own that participant and have source-project read permission.
Reception never writes the source. Keep sealed outbox state separate from inbox
admission. `canon_classify` previews local packets. In the receiver project,
`canon_packet_classify` previews an exact foreign outbox locator without copying
it or deciding input. Reception rechecks the current receiver contract inside
the destination transaction. Inbox `admission_at_receipt` preserves the initial
compatibility evidence even if the current contract later changes. An expected match
permits automatic admission only when that contract explicitly enables it.
Undefined or incompatible typed input remains pending. `canon_decide` follows
the receiver's current grant and the user's decision. Send
`incompatible_input_decision: "ACCEPT"` only for an explicit user acceptance of
that exact incompatible or undefined packet; preserve its mismatch reasons.
This Canon input decision is separate from removed Project/PV and Learning HIL.
Use `canon_task_result` with the exact bound edge, original input and typed
payload; it derives the destination and return contract. The same edge and
payload reuse the original result across request IDs. Different original inputs
under that key conflict; changing expiry cannot renew a sealed packet.
A foreign result still
needs `canon_receive` and receiver admission in its source project. The graph
counts only that locally admitted exact result. No native task or human decision
attestation is supplied by the engine-client identity.
Use `canon_backfire` only for an upstream execution failure, missing source
information, a new source requirement, or linked-task input that needs action.
Pin a currently admitted input and its envelope digest, the exact recipient,
requested contract and newer packet revision, evidence, dependencies, return
route, return contract and expiry. The recipient may differ from the input's
sender. Preserve the inherited trace; repeated endpoints are blocked. The same
recipient, contract and revision reuse the original proposal only for identical
content. No automatic retry is permitted. A declared third-party return route
must have a current receiver-owned contract and authorized project reads; its
eventual reply still needs separate reception and admission. `canon_send` cannot
bypass the typed backfire operation. Generic clarification remains available.
Use `canon_supersede` to replace decided input with an exact newer admitted
exchange on the same route. Pin both envelope digests and the original state
version. Corrections record their successor link on admission; pending input
does not displace an earlier decision. Preserve
corrections, backfire requests, returns and their attribution. Canon exchange
does not merge project truth, transfer task ownership or replace State Travel.

# Host Storage, ENV Continuity, and Mode Execution

This contract keeps three authorities separate: the client surface, the MCP
transport, and the durable Evidence Lane runtime. A chat or desktop window is
never itself the database authority.

## Host storage matrix

| Host profile | Live primary runtime | Google Drive policy |
| --- | --- | --- |
| Stable Codex PC, laptop, CLI, or permanent VM | Durable local SQLite | Not selected |
| ChatGPT connected to a durable mounted or local MCP server | MCP server mounted or local SQLite | Forbidden for ChatGPT runtime |
| Ephemeral Codex VM with an explicitly durable mount | Durable mount SQLite | Sealed Entry/Exit carrier allowed, never primary |
| Ephemeral Codex VM without a durable mount | Configured transactional runtime connector | Sealed Entry/Exit carrier allowed, never primary |
| ChatGPT MCP server without durable storage | Configured non-Drive transactional runtime connector | Forbidden for ChatGPT runtime |

Google Drive does not provide transactional sessions, backlog, ChatLineage,
candidate state, receipt state, and pointer compare-and-swap. It therefore
cannot be the live Evidence Lane runtime.

## Boot and Resume continuity

Every Boot or Resume produces a sealed
`evidence-lane.runtime-continuity.v1` receipt containing:

- canonical host and host-session binding;
- exact accepted PV, pointer generation, manifest hash, and package hash;
- selected primary storage route plus MCP read/write policy;
- locked ENV/UOP authority and Flash receipt hashes;
- explicit proof that ENV/UOP bytes are not embedded in a PV;
- explicit proof that the receipt moves no pointer and infers no HIL approval.

The same validated receipt is copied into Entry and Exit Slips. MCP reads target
the primary runtime. MCP mutations remain governed by ENV/UOP and one-writer
law.

### Accepted-authority evolution boundary

An accepted PV remains the immutable entry authority when later releases add
stricter topology or promotability rules. Boot, Resume, direct continuation,
State Travel, status, and rollback revalidate its exact bytes, manifest/package
hashes, pointer identity, and database integrity without retroactively
requalifying it as a new candidate. The continuity receipt reports whether that
accepted authority is promotable under current rules. A historical-schema
result is visible compatibility evidence, not a failure and not permission to
alter accepted bytes. The successor candidate must pass every current rule.

## Selected-mode execution

`mode_classify` accepts either an explicit plugin/API selection or deterministic
prompt inference. It preserves order, emits the selected lane formula, loop,
operator families, CI/CD requirement, and receipt, then stores a pointer-neutral
active binding. Task classification snapshots that binding. Candidate Entry and
Exit Slips and the HIL next-action contract receive the exact snapshot.

Code Mode is governed by:

`plan -> sandbox build -> test -> hash -> package`

It requires controlled CI/CD evidence and the PCM plus MBA operator groups. The
six HIL tokens remain exactly `APPROVE`, `APPROVE_WITH_DELTA`,
`MORE_RESEARCH`, `ROLLBACK`, `REJECT`, and `FAIL`; their accepted object, gate,
rollback target, and lane effect come from the selected lane. Non-Code modes do
not inherit Code Mode's CI/CD or HIL meanings.

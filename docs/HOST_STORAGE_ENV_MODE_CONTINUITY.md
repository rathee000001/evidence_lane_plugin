# Host Storage, ENV Continuity, and Mode Execution

This contract keeps three authorities separate: the client surface, the MCP
transport, and the durable Evidence Lane runtime. A chat or desktop window is
never itself the database authority.

## Independent host capability axes

Storage durability, interaction profile, VM lifetime, account tier, and API
billing are independent. Account tier and API billing never select storage and
never decide whether a tunnel is required.

Project research is independent of those host axes. When the active sealed Plan
and linked Delta set select memory plus learning, the private turn ledger seals a
deterministic memory-and-learning research question together with the redacted
visible prompt. Its retrieval key is the exact project, Evidence Lane session,
and task; it is not placed in shared FTS, shared telemetry, or public output.

## Host storage matrix

| Host profile | Live primary runtime | Google Drive policy |
| --- | --- | --- |
| Stable Codex PC, laptop, CLI, or permanent VM | Durable local SQLite | Not selected |
| ChatGPT connected to a durable mounted or local MCP server | MCP server mounted or local SQLite | Forbidden for ChatGPT runtime |
| Ephemeral Codex VM with an explicitly durable mount | Durable mount SQLite | Sealed Entry/Exit carrier allowed, never primary |
| Ephemeral Codex VM without a durable mount | Configured transactional runtime connector | Sealed Entry/Exit carrier allowed, never primary |
| ChatGPT MCP server without durable storage | Configured non-Drive transactional runtime connector | Forbidden for ChatGPT runtime |

| Interaction profile | VM lifetime | PV runtime | Tunnel |
| --- | --- | --- | --- |
| Headless API or direct CLI API | Local or persistent | Durable local SQLite when available | Not required at the API layer |
| Headless API or direct CLI API | Ephemeral with durable mount | Durable mount SQLite | Not required at the API layer |
| Headless API or direct CLI API | Ephemeral without durable mount | Configured durable transactional connector | Not required at the API layer |
| Interactive Codex app | Local or persistent | Durable local SQLite | One setup per persistent host and release |
| Interactive Codex app | Ephemeral VM | Durable mount or configured durable connector | One setup per VM; key and tunnel last only for that VM |

On an ephemeral interactive VM, the PV runtime and tunnel secret lifetime remain
separate even when the PV runtime uses a durable mount. The tunnel marker binds a
SHA-256 of the current VM instance identity, never the raw identity. Its DPAPI
envelope and version registry stay under the VM-local runtime root; the installer
will not import a key envelope from the durable PV store. A new VM must perform a
fresh local setup.

For headless API profiles, every invocation entry verifies locked ENV/UOP Flash,
loads the exact project state and prior accepted or pending Entry/Exit Slip from
the durable runtime, and returns a copyable
`PV_EXIT_SUGGESTED_NEXT_PROMPT`. The Exit Slip preserves the six-way HIL and
never auto-submits a choice. Ending the client process does not end the durable
project runtime.

Google Drive does not provide transactional sessions, backlog, ChatLineage,
candidate state, receipt state, and pointer compare-and-swap. It therefore
cannot be the live Evidence Lane runtime.

The MCP or tunnel is never a single-project binding. Runtime-global inspection
has no project route; every project-scoped tool requires an exact `project_id`
and resolves only beneath `<configured-store-root>/projects/<project_id>`. See
`PORTABLE_MULTI_PROJECT_ROUTING.md` for root precedence, collision rejection,
two-surface placement, and migration-safe receipt behavior.

## Boot and Resume continuity

Every Boot or Resume produces a sealed
`evidence-lane.runtime-continuity.v1` receipt containing:

- canonical host and host-session binding;
- exact accepted PV, pointer generation, manifest hash, and package hash;
- selected primary storage route plus MCP read/write policy;
- exact project ID, resolved store root, and isolated relative project route for
  newly created receipts;
- locked ENV/UOP authority and Flash receipt hashes;
- explicit proof that ENV/UOP bytes are not embedded in a PV;
- explicit proof that the receipt moves no pointer and infers no HIL approval.
- independent interaction/VM/tunnel fields and, for API profiles, proof of
  per-invocation Flash with no tunnel dependency;
- a stable `PV_EXIT_SUGGESTED_NEXT_PROMPT` label for copyable Exit-Slip
  continuation without HIL inference.

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

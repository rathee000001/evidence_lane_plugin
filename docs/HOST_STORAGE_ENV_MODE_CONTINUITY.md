# Host storage, ENV/UOP continuity, and governed learning

Evidence Lane keeps the Codex client, native MCP transport, durable project
runtime, Project Truth, and Agent Learning distinct. A desktop window, model
context, cache, or transcript is never the database authority.

## Independent capability axes

Storage durability, interaction profile, VM lifetime, account tier, and API
billing are independent. Account tier and billing never select storage. The v2
Codex plugin uses its local native MCP route and bundles no network tunnel.

| Codex profile | Live primary runtime | External network setup | Flash frequency |
| --- | --- | --- | --- |
| Desktop, local CLI, or persistent VM | Durable local SQLite | Not required for native MCP | Every Boot or Resume |
| Headless API with durable local storage | Durable local SQLite | Not required at API layer | Every invocation entry |
| Ephemeral VM with durable mount | Durable mount SQLite | Not required for native MCP | Every Boot, Resume, or API entry |
| Ephemeral VM without durable mount | Configured transactional connector | Not required for native MCP | Every Boot, Resume, or API entry |

Google Drive can carry sealed Entry/Exit artifacts but cannot provide the
transactional sessions, backlog, Chat Lineage, candidate state, receipt state,
or pointer compare-and-swap required by the live runtime.

Every project-scoped tool requires an exact `project_id` and resolves only
beneath `<configured-store-root>/projects/<project_id>`. There is no implicit
default project or cross-project fallback.

## Boot and Resume continuity

Every Boot or Resume produces a sealed
`evidence-lane.runtime-continuity.v1` receipt containing:

- canonical Codex host and host-session binding;
- exact accepted PV, pointer generation, manifest hash, and package hash;
- selected storage route plus native MCP read/write policy;
- exact project ID, resolved store root, and isolated project route;
- locked ENV/UOP authority and Flash receipt hashes;
- proof that ENV/UOP bytes are not embedded in a PV;
- proof that the receipt moves no pointer and infers no HIL approval;
- invocation profile and the stable `PV_EXIT_SUGGESTED_NEXT_PROMPT` label.

Headless API entry rechecks the same locked Flash, loads the exact durable
project state and accepted or pending Entry/Exit Slip, and returns a copyable
next prompt. Ending a client process does not end the durable project runtime.

An accepted PV remains immutable when later releases add stricter topology or
promotability rules. New candidates must pass current rules; old accepted bytes
are not silently rewritten or requalified.

## Project Truth and Agent Learning

The learning arm is already governed by the same ENV/UOP execution boundary,
but it is not a second name for Project Truth.

- Project Truth owns accepted source claims, PVs, pointers, Deltas, task state,
  and human-ratified project evidence.
- Agent Learning owns approved semantic, episodic, and procedural lessons.
- The two planes may share content hashes, provenance vocabulary, retrieval
  primitives, and human-decision vocabulary.
- They must keep distinct schemas, namespaces, identities, write paths, query
  results, promotion authorities, tests, receipts, failure states, and HIL.
- Execution may propose a learning candidate but cannot write durable accepted
  learning directly.
- A learning candidate records source events, outcome, scope, lesson type,
  confidence, uncertainty, counterevidence, contradiction/supersession,
  expiry/revalidation, actor/model identity, and content hash.
- Only exact lane-specific HIL promotes learning. Accepted learning never
  rewrites Project Truth, an accepted pointer, or a source claim.

The private memory-and-learning research question is keyed to the exact project,
session, and active task. It is not placed in shared FTS, shared telemetry, or
public output. Goal-accounted token metrics may use a common schema while each
project's research question remains private to that project.

## Selected-mode execution

`mode_classify` stores a pointer-neutral active binding containing the selected
lane formula, loop, operator families, CI/CD requirement, and receipt. Task
classification, candidate Entry/Exit Slips, and HIL receive that exact snapshot.

Code Mode is:

`plan -> sandbox build -> test -> hash -> package`

It requires controlled CI/CD evidence and the PCM plus MBA operator groups. The
six HIL tokens remain exactly `APPROVE`, `APPROVE_WITH_DELTA`,
`MORE_RESEARCH`, `ROLLBACK`, `REJECT`, and `FAIL`. Non-Code modes do not inherit
Code Mode's CI/CD or HIL meanings.

<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / 2026-08-23 -->

# Host storage, ENV/UOP continuity, and governed learning

This is the Git-tracked authority for the GitHub Pages host/operator story and
the current Vercel Operators projection. Both must expose the same 3.0.0
host/storage/operator matrix and must not imply that a website, hook, tunnel,
helper, account tier, or cached package can replace the exact installed native
route selected for the invoking task.

Evidence Lane keeps the Codex client, native MCP transport, durable project
runtime, Project Truth, and Agent Learning distinct. A desktop window, model
context, cache, or transcript is never the database authority.

## Independent capability axes

Storage durability, interaction profile, VM lifetime, account tier, API
billing, and host tool transport are independent. Account tier and billing
never select storage or tunnel routing. The runtime classifier measures whether
the interactive Codex host already exposes the required native MCP capabilities;
only a proven host-tool gap selects the version-bound support tunnel.

| Codex profile | Live primary runtime | External network setup | Flash frequency |
| --- | --- | --- | --- |
| Desktop or local CLI on a durable local host with native MCP available | Durable local SQLite | Not required; use native MCP | Every Boot or supported task reattachment |
| Desktop or local CLI on a durable local host with a proven host-tool gap | Durable local SQLite | Version-bound hidden tunnel, once per persistent host and release | Every Boot or supported task reattachment |
| Durable remote Codex workspace | Durable remote filesystem/SQLite or an explicitly configured connector when required | Native MCP when available; version-bound tunnel only for a proven tool gap | Every Boot or supported task reattachment |
| Headless API with durable local storage | Durable local SQLite | Not required at API layer | Every invocation entry |
| Interactive ephemeral VM with durable mount | Durable mount SQLite | Native MCP when available; otherwise one tunnel for that VM lifetime | Every Boot or supported task reattachment |
| Interactive ephemeral VM without durable mount | Configured transactional connector | Native MCP when available; otherwise one tunnel for that VM lifetime | Every Boot or supported task reattachment |
| Headless API on an ephemeral VM | Durable mount or configured transactional connector | Not required at API layer | Every API entry |

An ephemeral or stateless route must consume one
`evidence-lane.host-entry-envelope.v2` before governed work. The envelope binds
the accepted PV/generation, active Plan row, exact source and destination task
identities, dirty/untracked worktree hashes, locked ENV/UOP projection, and the
separate Project Truth, Canon Input, Agent Learning, and ChatLineage heads. Its
expiry and replay nonce are checked transactionally. An exact retry returns the
prior consumption receipt without replaying mutations; a different consumer,
pointer generation, worktree, candidate overlay, or authority head fails
closed. Durable local SQLite needs no envelope.

At a remote exit, the existing immutable host-exit packet remains unchanged.
Continuity is claimed only after the next-entry envelope is durably stored and
a separate exit-completion receipt is sealed. Missing persistence leaves the
exit incomplete. Advancing the accepted Project PV invalidates older entry
generations and requires one replacement envelope for each still-active host
binding; this host action never advances the Project pointer itself.

Google Drive can carry sealed Entry/Exit artifacts but cannot provide the
transactional sessions, backlog, Chat Lineage, candidate state, receipt state,
or pointer compare-and-swap required by the live runtime.

Storage connectors, Google Drive, and the eight additional-plugin/toolchain
slots are separate surfaces. None may silently substitute for another. The
desktop stable/current and Beta channels are both multi-surface containers;
only a host receipt proving `active_surface=CODEX` enters this runtime. Chat and
Work surfaces remain out of scope, and package/process/title/CWD alone is never
enough evidence.

Every project-scoped tool requires an exact `project_id` and resolves only
beneath `<configured-store-root>/projects/<project_id>`. There is no implicit
default project or cross-project fallback.

## Boot and exact task continuity

Every Boot or supported exact-task reattachment produces a sealed
`evidence-lane.runtime-continuity.v1` receipt containing:

- canonical Codex host and host-session binding;
- exact accepted PV, pointer generation, manifest hash, and package hash;
- selected storage route plus native MCP read/write policy;
- exact project ID, resolved store root, and isolated project route;
- locked ENV/UOP authority and Flash receipt hashes;
- proof that ENV/UOP bytes are not embedded in a PV;
- proof that the receipt moves no pointer and infers no HIL approval;
- invocation profile and the stable `PV_EXIT_SUGGESTED_NEXT_PROMPT` label.
- container channel, proven Codex surface, workspace class, account/API route,
  and allowlisted native-capability results;
- a collision-resistant runtime namespace binding project, governed session,
  workspace, host session, plugin version, channel, and active surface without
  storing raw host/workspace identifiers.
- a host-entry route contract that is `NOT_REQUIRED_DURABLE_LOCAL_AUTHORITY`
  for proven durable storage and
  `CONSUMED_EXACT_ONCE_FROM_TRANSACTIONAL_CONNECTOR` otherwise; the runtime
  receipt is not issued before that consumption passes.

Headless API entry rechecks the same locked Flash, loads the exact durable
project state and accepted or pending Entry/Exit Slip, and returns a copyable
next prompt. Ending a client process does not end the durable project runtime.

Direct same-worktree State Travel is a separate one-shot destination-entry
route. Its public action accepts only project, session, authoritative-source
task, runtime-donor task, destination task, and destination title. The server
derives the worktree, runtime, replay, Plan, accepted-baseline, and pointer
proofs. Caller bindings, nonces, PIDs/runtime IDs, hashes, PV/pointer payloads,
sealed PREPARE/RESUME, and retries are not public compatibility paths.

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
- A learning candidate records exact project/task/Delta/PV/ref/hash evidence,
  outcome, scope, lesson type, confidence, counterevidence,
  contradiction/supersession, temporal validity/expiry, privacy, lineage head,
  identity, and content hash. Actor/model evidence remains in the linked
  ChatLineage event instead of being duplicated into the candidate.
- Only exact lane-specific HIL promotes learning. Accepted learning never
  rewrites Project Truth, an accepted pointer, or a source claim.

The executable authority lives under the project-scoped `learning/` namespace:

- `agent-learning.sqlite` stores immutable candidates, append-only lifecycle
  events, decision receipts, and every Learning pointer generation;
- `candidates/` and `receipts/` retain canonical JSON artifacts whose seals are
  reconciled against SQLite;
- `active_pointer.json` is Learning-only and can move only on exact
  `APPROVE` or `ROLLBACK: LGENn` decisions;
- `APPROVE_WITH_DELTA`, `MORE_RESEARCH`, `REJECT`, and `FAIL` preserve both the
  Project Truth pointer and the Learning pointer;
- expiry, revocation, supersession, rollback, and Project Truth contradiction
  suppress stale learning without deleting immutable history; and
- retrieval returns a bounded Agent Learning slice with its own provenance.
  It never merges that slice into Project Truth. Formula Engine may route the
  operators, but it cannot infer or accept a lesson. Brain Scaling is bounded
  indexed retrieval and composition, never autonomous training.

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

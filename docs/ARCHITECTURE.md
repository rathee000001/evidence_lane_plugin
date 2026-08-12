# Evidence Lane 2.1.0 architecture

Evidence Lane 2.1.0 is a Codex-native, local-first evidence lifecycle. The
architecture separates source truth, derived project memory, task/Delta state,
candidate state, accepted truth, and host presentation so no one surface can
silently promote another.

## Trust planes

### Source plane

The source plane contains the user-authorized repository and ordered source
packages. Source Intake records repository identity, dirty/untracked state,
source hashes, parser capability, lane routes, and provenance. It does not
rewrite or normalize user bytes merely to make a package clean.

### Evidence plane

Lane builders create deterministic SQLite, graph, text, and receipt artifacts.
Every derived artifact is tied to source hashes and tool/runtime identity. A
changed source section invalidates only affected derived projections.

### Plan and Delta plane

Plan Lane is append-only and linear:

- at most one executable task is active;
- linked steers append to that task without duplicating it;
- unrelated work creates one complete task row;
- dropped and superseded tasks remain immutable history;
- the final six-way HIL remains physically and semantically final.

The Codex task panel is a projection of this plane, not its authority.

### Candidate plane

Build and Refresh create sealed, unaccepted candidate packages. Candidate
identity includes its base accepted PV, source hashes, derived artifacts,
runtime/toolchain identity, test receipts, and proposed next PV. Multiple
candidate states cannot move the accepted pointer implicitly.

### Accepted plane

The accepted pointer references one immutable accepted PV and a monotonic
generation. Only exact case-sensitive `APPROVE` at the matching HIL can
authorize Fuse. Rollback changes only the accepted pointer to a previously
accepted immutable PV under its own receipt.

## Native MCP surface

The package-local server identity is `evidence-lane`; the canonical display
namespace is `mcp__evidence_lane__`. It exposes exactly 62 actions:

- 21 read-only operations;
- 41 write-capable operations.

The server may expose a collision-safe host display suffix, but the route
receipt proves canonical tool names and rejects generated, app, legacy, or
external-connector surfaces as lifecycle authority.

The six primary controls are Boot, Rollback, Build, Refresh, Mode, and Source
Intake. State Travel is a user-timed recovery path and `/evi-plan` is a Codex
Plan-mode sidecar.

## Local durable storage

Each project is contained under one configured store root:

```text
<store-root>/projects/<project-id>/
```

The store contains project configuration, SQLite authority, accepted pointer,
immutable PVs, candidates, Plan/Delta events, prompt/response indexes, receipts,
and installation/runtime bindings. Every project-scoped tool carries an exact
`project_id`; cross-project fallback is forbidden.

On local or persistent Codex hosts, durable local SQLite is primary. Ephemeral
headless environments use a durable mount when available or an explicitly
configured transactional connector. Optional artifact mirrors never become
live SQLite authority.

## ENV/UOP Flash and runtime continuity

Boot verifies the locked ENV/UOP authority before mutable work. Runtime
continuity binds:

- project and session identity;
- Codex task UUID and host session;
- host execution profile;
- exact accepted pointer generation;
- ENV/UOP authority and Flash receipt;
- storage route;
- source boundary;
- model/submodel/reasoning profile when governed by the task.

Headless API invocations reverify the locked authority at entry and load the
prior accepted or pending Entry/Exit state from durable storage. The API layer
does not depend on an Evidence Lane network tunnel.

## State Travel

State Travel is permitted only after an explicit user request or genuine host
context exhaustion. A handoff alone does not invoke it. The sealed handoff
binds the project, session, accepted pointer, candidate/HIL state, source
boundary, Plan/Delta event head, host profile, and destination task identity.

Resume consumes a valid handoff once, fails closed on any mismatch, restores
the complete task panel before source inspection, and resumes the exact active
row. It never infers HIL, moves the pointer, or creates a candidate.

## Hooks and visible continuity

The v2 package registers four hook events:

- `SessionStart` — verify installation and prepare bounded session context;
- `UserPromptSubmit` — bind the visible turn without storing private reasoning;
- `PostToolUse` — refresh the linked task/Delta change projection after relevant
  native actions;
- `Stop` — preserve the response/exit boundary.

The package inventory is four events, four handlers, and five hook files
including `hooks.json`. Hook output can request a persistent change notice, but
Codex owns its final placement. The icon and rendered panel are therefore
installed-host observations, not facts inferred from source metadata.

## Project and runtime panels

`render_project_panel` and `render_runtime_panel` return MCP Apps-compatible
resource metadata and a structured snapshot. The project panel shows accepted
PV, candidate, active task, Deltas, source state, and HIL boundary. The runtime
panel shows installation, activation, Flash, storage, catalog, hook, skill, and
version state.

Plain-text fallback is not accepted as proof that the visual resource attached.
The installed-host HIL must observe the resource and Evidence Lane identity.

## Git and CI/CD

Git reads and writes are separated. A remote push is prepared against one exact
project, non-default branch, commit, tree, remote, and expected remote head.
The configured v2 test branch may use standing host-managed authorization for a
fast-forward execute step; force-push, default-branch mutation, merge, release,
candidate acceptance, pointer movement, and Fuse remain unauthorized.

One coherent correction commit triggers one CI cycle. The reusable Code-mode
action records formula, loop, operators, commands, exit code, commit/tree, and
receipt hash. Preview compilation and CodeQL are evidence gates, not lifecycle
promotion.

All built v2.1.0 candidates must pass both gates: clean-checkout CI for the
exact source commit and installed-host verification for the exact package.

## Installation and restart

The deterministic package builder excludes site source, evidence directories,
generated app metadata, and host-connection artifacts. Installation uses the
supported Codex marketplace route. Direct mutation of the generated plugin
cache is prohibited.

Because a running task can freeze its capability snapshot, every changed
package receives a fresh collision-free build identity and a controlled
restart. Restart preparation seals the exact installation receipt, root Codex
process, app identity, project, Evidence Lane session, task UUID, host session,
and task deep link. Any mismatch stops before process termination.

## Security boundaries

- Private reasoning is never stored.
- Secrets are redacted and excluded from packages and receipts.
- Dirty and untracked bytes remain untouched unless explicitly authorized.
- Generated namespaces and external connectors cannot substitute for the
  package-local native lifecycle route.
- Tests, commits, pushes, packages, installs, and restarts are evidence only.
- HIL is never inferred.

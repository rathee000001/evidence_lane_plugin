# Evidence Lane 2.2.0 architecture

Evidence Lane 2.2.0 is a Codex-native, local-first evidence lifecycle. The
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

The bundle-level lane disposition authority contains exactly eighteen ordered
rows. Emitted lanes are `PRESERVED` or `PARTIAL` from measured parser states
and capabilities; never-loaded lanes are `MISSING`; lanes removed after a
prior build are `DEFERRED`. Only emitted lanes may own SQLite/MMD/DOT/
`tools.json` authorities. Required, conditional, and optional capabilities,
deterministic chunk/FTS counts, topology files, and lane receipts are explicit.
The additive contract validates new bundles while retaining read compatibility
for historical V1/V2/V3 manifests.

Code dependency detection is ecosystem-specific. `package.json` remains an npm
manifest, while `pnpm-lock.yaml` uses its bounded pnpm importer detector and
cannot be silently routed through a Python or pip detector. General source
search uses hash-pinned package-local `rg` and `fzf`, then an explicitly
configured verified host binary, then deterministic bounded built-in
fallbacks; PATH-only selection and handshake downloads are forbidden.

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

Insufficiently durable hosts add a separate host-entry continuity plane. A
secret-safe, expiring envelope binds the exact accepted generation, active Plan
row, source/destination task identities, dirty worktree hashes, ENV/UOP
projection, and four authority heads. Consumption is transactional and
exact-once; exact retries reuse the prior receipt, while stale generations,
cross-project/task bindings, unauthorized candidate overlays, and replay under
a different consumer fail closed. The envelope and its completion receipt move
no Project Truth pointer, accept no Canon or Learning input, and replay no HIL.

Storage connectors, Google Drive, and the eight additional-plugin/toolchain
slots are independent surfaces. A storage connector is selected only through
an explicit capability route, Google Drive is at most a sealed artifact mirror
or carrier, and the eight slots never carry persistence authority.

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

The runtime classifier also binds the stable/current or Beta desktop container
channel, a host-proven `active_surface=CODEX`, local/worktree/durable-remote/
ephemeral workspace class, account-versus-API route when exposed, and allowlisted
native capabilities. Chat and Work surfaces are out of scope. Package, process,
window title, or CWD alone cannot prove the active surface. The resulting
runtime namespace separates project, governed session, workspace, host session,
plugin version, channel, and surface while receipts retain only secret-safe
hashes for raw workspace and host-session identifiers.

Native local Codex uses the package-local MCP and durable local SQLite without
a network tunnel. Only an explicitly classified interactive ephemeral VM may
require the separate, one-VM-lifetime tunnel support channel.

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

The v2.2 package registers eight hook events:

- `SessionStart` — verify installation and prepare bounded session context;
- `UserPromptSubmit` — bind the visible turn without storing private reasoning;
- `PreToolUse` — fail closed when the governed PREPARE binding is absent;
- `PostToolUse` — refresh the linked task/Delta change projection after relevant
  native actions;
- `PreCompact` — seal the current compaction boundary;
- `PostCompact` — rehydrate lifecycle context and require skill re-entry;
- `Stop` — preserve the response/exit boundary;
- `SessionEnd` — best-effort lifecycle flush without inferring completion.

The package inventory is eight events, six command handlers, and seven hook files
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

All built v2.2.0 candidates must pass both gates: clean-checkout CI for the
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

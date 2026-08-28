<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v3 -->

# Host and storage matrix

Evidence Lane classifies host capability, storage durability, interaction
profile, VM lifetime, account tier, and billing as independent axes. Reduced
host capability never expands lifecycle authority.

## Execution profiles

| Execution profile | Primary project runtime | Native transport | Optional support tunnel |
| --- | --- | --- | --- |
| Durable Codex desktop on a local host | User-owned durable SQLite | Package-local native MCP | Not required |
| Local Codex CLI | User-owned durable SQLite | Package-local native MCP | Not required |
| Headless API/CLI on a persistent host | Durable local filesystem/SQLite | Native API/MCP route | Not required |
| Ephemeral VM with a durable mount | SQLite on the durable mount | Native API/MCP route | Not required |
| Ephemeral VM without a durable mount | Explicit transactional durable connector | Native API/MCP route | Not required |
| Interactive Codex app on an ephemeral VM | Durable mount or transactional connector | Package-local native MCP | One VM-lifetime tunnel only when the capability receipt requires it |
| Review-only client | No execution authority | Receipt reads only | None |

Stable Codex (`OpenAI.Codex_2p2nqsd0c76g0!App`) and Codex Beta
(`OpenAI.CodexBeta_2p2nqsd0c76g0!App`) are exact app variants of the same
`CODEX_DESKTOP` host profile. They use the same plugin contract and one
host-wide tunnel. The tunnel is never duplicated per app, project, or task.

A website, package name, process title, current directory, account tier, or
running tunnel is not active-host proof. The runtime receipt binds the Codex
surface, container channel, workspace class, model/reasoning/service profile,
project/session/workspace/host-session namespace, storage route, and exposed
native capabilities.

## Durable and ephemeral entry

Durable local SQLite reuses the exact project authority directly. An ephemeral
or stateless route must consume one expiring transactional host-entry envelope
before governed work. The envelope binds:

- accepted PV and pointer generation;
- active Plan row and task identity;
- source/destination task UUIDs and deep links;
- dirty and untracked worktree hashes;
- locked ENV/UOP projection; and
- separate Project Truth, Canon, AI Learning, ChatLineage, and Memory heads.

An exact retry may return the prior consumption receipt. A different consumer,
pointer generation, worktree, candidate overlay, or authority head fails
closed. Google Drive may carry a sealed artifact but cannot replace the
transactional runtime, sessions, Plan, candidates, receipts, or pointer CAS.

## Multi-project isolation

Every project-scoped operation requires an exact `project_id` and resolves
only under `<configured-store-root>/projects/<project_id>`. The hidden runtime
registry retains multiple project/task bindings while one project-neutral
tunnel transports their calls. Each running task remains isolated by its exact
app, task UUID, deep link, workspace, session, and host identity. One helper
invocation cannot redirect another project or another running Codex app.

## Plugin channel matrix

Maintainer testing uses two registered roles:

| Role | Authority |
| --- | --- |
| Local verified successor | Current local source/package under active development |
| Git release | Exact accepted/main release package |

Only one role is active for one task runtime. A slot switch requires exact
installed-byte, catalog, hook, runtime/tunnel, and task-binding readback after
the host restart. A transient error is insufficient to switch. Historical
packages remain provenance outside the active selectors.

Downstream users receive one verified plugin release. Maintainer development
uses exactly two selectors: verified Git main and one mutable local testing
slot; no third recovery selector is a current execution route.

## ENV/UOP and storage

Every Boot or supported exact-task reattachment flashes the exact locked ENV/UOP authority for the selected
host mode. ENV/UOP may route operators and storage, but its bytes are not
embedded in a Project PV or returned as ordinary user data. Account plan and
API billing never choose the storage connector or change HIL law.

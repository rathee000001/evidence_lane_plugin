<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v3 -->

# Host and storage matrix

<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->
## Current backend contract

This public document is refreshed from the same source graph used by the installable plugin package.

- Plugin package: `3.0.0+codex.20260828064341`.
- Native MCP: **91 actions** (**30 read / 61 write**).
- Native skills: **26 governed skills**; the separate command layer is absent.
- Hooks: **11 events / 44 ordered handler actions**.
- SDK: internal action SDK and outer routing SDK remain distinct; public action count **91**.
- ENV/UOP: separate executable authorities with **7 ENV members / 5 UOP members**.
- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.
- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.

Exact backend bindings:
  - `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` — `42A726CD910A27EF9B8987907F02D127789857C8B04E1E214A91D1F74D151A4B`
  - `plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json` — `B571AF9EC31691C96DB0B3845ED0B7A6700D1C84A2578ABA9A2EA594982AF045`
  - `plugins/evidence-lane-plugin/skills/skill-surface-registry.v1.json` — `38B1F95B8160E037B43B209A6D6047BF8BCA4D2599182C2F20E4606B6CBDF3A5`
  - `plugins/evidence-lane-plugin/hooks/hooks.json` — `C37DB05DD4701087EAD0BD31203C843AAFA79ED39A081F2E9DFF313A77631EEF`
  - `plugins/evidence-lane-plugin/sdk/sdk-manifest.v1.json` — `5BD21AEB96D7E41209E3D059D8A5296D851BDED1D453D6EF486C0CD50D745245`
  - `plugins/evidence-lane-plugin/mcp/mcp-manifest.v1.json` — `E9E402C2F20B2BBE63B6BF91613B1C97E85E615F982D52CF6D020408251AFAFB`
  - `plugins/evidence-lane-plugin/env/authority-manifest.v1.json` — `E4F283EC16F86995E2937288DD8A8E5623007351CBB1CA3FD01FDA5C7363B6C1`
  - `plugins/evidence-lane-plugin/uop/authority-manifest.v1.json` — `BBA3CDAE9CC0FF981E5C6E19F83FBBCE6EB2ED8167CDBB2E9D1C557FA03CA57C`
  - `plugins/evidence-lane-plugin/toolchains/TOOLCHAIN_EXECUTION_MATRIX.md` — `E5379D7C4B17BC9293F332216581D60F88ADF73A4B7B361D84D09B47FC4EA66F`
<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->


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

<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Evidence Lane user tunnel guide

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


The tunnel is a version-bound host transport for an interactive
Codex environment whose capability classifier proves a host-tool gap. Durable
local storage alone neither requires nor forbids it: local desktop and native
CLI use native MCP when available, and use the tunnel only when those required
tools are absent. Headless API and direct CLI/API profiles never require the
interactive tunnel; account tier and API billing do not select this route.

## One-time setup for an eligible host

Use the packaged installer:

`plugins/evidence-lane-plugin/scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1`

The registration must bind the exact release/slot, interactive profile,
`HostToolTransport HOST_TOOL_GAP`, host lifetime, and—when ephemeral—the real VM
instance identity. A representative ephemeral shape is:

```powershell
./Install-EvidenceLaneTunnel.ps1 -SlotRole main-git-release -InteractionProfile CODEX_APP_INTERACTIVE -HostToolTransport HOST_TOOL_GAP -HostLifetime Ephemeral -VmInstanceId exact-vm-instance-id
```

The Runtime API key is entered through the supported host path, stays masked,
and is protected with Windows DPAPI. It must never appear in Git, chat text,
receipts, logs, screenshots, command history, or a package.

One configured tunnel is host-wide and project-neutral. Stable Codex and Codex
Beta share it. The plugin supplies the exact `project_id` and task/session
binding on every governed call, so the tunnel can serve many projects and
tasks without becoming their authority or creating per-project processes.

## Operate and verify

Use `Manage-EvidenceLaneTunnel.ps1` with `Start`, `Stop`, `Status`, `Repair`, or
`Remove`. `-Action Status` must return `status = PASS` before the tunnel can be
treated as healthy. Native lifecycle proof still comes from
`mcp__evidence_lane__*`; a listening port or running process is not enough.

Every tunnel child process must use hidden/no-window launch semantics. A
persistent tunnel stays hidden and observable through receipts. On Windows,
the one host-wide process uses one exact versioned at-logon scheduled task, not
one scheduled task per project or Codex task. Repeated terminal windows that spawn and close are a
first-class defect.

## Authority and version boundaries

- The tunnel transports an eligible host connection; it cannot classify work,
  accept HIL, Fuse, move a pointer, install a package, or merge Git.
- One version-matched tunnel belongs to the active installed plugin. There is
  no separate user-helper or parallel maintainer-tunnel execution route.
- Registrations are exact-build keyed. Removed executable tunnel identities are
  absent from live routing; immutable historical receipts remain provenance.
- The current source still needs installed-host proof that every child
  independently enforces no-window behavior and that State Travel blocks
  tunnel management through one shared lease.

See [HOST_AND_STORAGE_MATRIX.md](HOST_AND_STORAGE_MATRIX.md) for the public host
matrix and this guide for the complete tunnel verification contract.

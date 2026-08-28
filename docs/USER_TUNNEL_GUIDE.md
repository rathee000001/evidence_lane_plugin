<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Evidence Lane user tunnel guide

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

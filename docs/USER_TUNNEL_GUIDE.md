<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / 2026-08-23 -->

# Evidence Lane user tunnel guide

The Stable tunnel is a version-bound host transport helper for an interactive
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

## Operate and verify

Use `Manage-EvidenceLaneTunnel.ps1` with `Start`, `Stop`, `Status`, `Repair`, or
`Remove`. `-Action Status` must return `status = PASS` before the tunnel can be
treated as healthy. Native lifecycle proof still comes from
`mcp__evidence_lane__*`; a listening port or running task is not enough.

Every tunnel/helper child process must use hidden/no-window launch semantics.
A persistent tunnel stays hidden and observable through receipts. Repeated
terminal windows that spawn and close are a first-class defect.

## Authority and version boundaries

- The tunnel transports an eligible host connection; it cannot classify work,
  accept HIL, Fuse, move a pointer, install a package, or merge Git.
- User Stable tunnel setup is separate from the maintainer release tunnel and
  helper route.
- Registrations are versioned and must ultimately be exact-build keyed. Older
  identities may be retained for recovery but only one eligible version may be
  active.
- The current pre-HIL source still needs installed-host proof that every child
  independently enforces no-window behavior and that State Travel blocks
  tunnel management through one shared lease.

See [WINDOWS_TUNNEL_PERSISTENCE.md](WINDOWS_TUNNEL_PERSISTENCE.md) for the full
host matrix and maintainer verification contract.

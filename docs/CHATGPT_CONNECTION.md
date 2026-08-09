# ChatGPT connection

ChatGPT and Codex are separate host universes. They may run the same Evidence
Lane code and lifecycle laws, but they never share an implicit PV or storage
authority. ChatGPT Pro uses the exact `CHATGPT_PRO_GOVERNED` MCP profile through a
verified outbound Tunnel or a public connector backed by a durable Evidence
Lane origin. It never treats the Git repository, Vercel filesystem, or the
Codex store as its live database authority.

The supported public-connector shape is:

```text
ChatGPT -> Vercel preview/adapter -> exact-release durable MCP origin
```

## Installation route distinction

Codex installs Evidence Lane from the governed Git marketplace route and keeps
the full lifecycle. ChatGPT installs the same full plugin package, whose
`.app.json` maps the registered Evidence Lane MCP connection into the bundled
skill corpus. The product display name stays **Evidence Lane**; `1.5.0` belongs
in version metadata. ChatGPT therefore displays and can route through all
fifteen packaged skills, including Boot/ENV-UOP Flash guidance and accepted-PV
Entry/Exit inspection, alongside a complete 62-action catalog. Exactly 21
operations execute as reads for accepted PVs, lanes, Chat Lineage, task backlog,
receipts, diffs, search, and governed panels. All 41 lifecycle-write actions stay
visible but are intercepted before service invocation and return
`UNAVAILABLE_ON_CHATGPT_PRO` with no mutation.
ChatGPT's native ENV/UOP package and Project Mutation sector may continue under
the host's own append-only law, but the MCP does not perform or claim that
mutation. No MCP write occurs in the Codex store or Vercel's function
filesystem.

The separate public **New Plugin** or connector route accepts an **MCP Server
URL** or a **Tunnel**, not a Git working tree. That step registers only the MCP
app. The app's `plugin_asdk_app...` technical ID must then be mapped in the full
plugin package before ChatGPT can show the bundled skills and release metadata.
The v1.5 package maps the existing read-safe Evidence Lane connection as
`plugin_asdk_app_6a7743d238e48191be8b69c87fb71d7f` through `.app.json`, and
`.codex-plugin/plugin.json` points its compatibility `apps` field to that file.
This public technical ID is routing metadata, not a credential and not proof
that the underlying endpoint currently serves the candidate Git SHA.
Git supplies source and release identity, while that route still requires a
reachable MCP transport. An older personal app shown in ChatGPT proves only
that one runtime, server, or tunnel was connected; it does not prove that the
currently selected Git SHA or full plugin package is active.

Do not treat a connector details page with no Skills section as a complete
installation. The complete-package check requires the Evidence Lane details
page to show the bundled skill inventory and a fresh conversation to route a
read-safe Boot request through the packaged `evi-boot` instructions and the
governed 62-action profile. The six primary controls remain visible;
unsupported lifecycle-write actions return an explicit fail-closed receipt.

For a contributor's local route, run
`scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1`. It accepts one exact
Tunnel ID, captures one Runtime API key through a masked DPAPI prompt, creates
the governed 62-action profile, registers boot persistence, and provides Status,
Repair, and exact Remove actions. ChatGPT is linked once after the tunnel is
ready. Codex never uses this route.

The Vercel adapter performs no local evidence writes. Its `/healthz` must report the
exact Git SHA, `durable_origin_verified: true`, and
`local_state_authority: false`. The durable origin must expose `/mcp`, preserve
the complete runtime state, and enforce ChatGPT-compatible OAuth/JWT scopes.

ChatGPT does not expose Codex's native `/pl`, Goal, or task-panel controls. Its
plugin still persists the canonical Plan Lane and task state inside its own
runtime and resumes them through Evidence Lane tools. Public MCP metadata and
MCP Apps panels are publication/UI surfaces, not a bridge into Codex state.

Before creating a ChatGPT connection, verify:

1. the implementation branch and exact SHA are remotely visible;
2. the durable origin reports that SHA;
3. the adapter preview reports the same SHA;
4. OAuth discovery and protected-resource metadata are correct;
5. the ChatGPT profile exposes exactly 62 tools: 21 read-only operations and 41 visible write-annotated operations that return no-mutation fail-closed receipts;
6. no compromised OpenAI key exists in source or deployment configuration;
7. a fresh chat can read the same accepted-PV, slip, lane, and panel evidence.

Use the adapter `/mcp` URL with OAuth. Creation and any UI confirmation remain
manual HIL unless screen control is explicitly authorized in that task.

A successful connection, test call, or fresh-chat resume does not accept a
candidate. Only exact `APPROVE` through Fuse may do so. Fuse prepares a sealed
handoff, but ChatGPT State Travel remains unconsumed until the user explicitly
requests it or the current chat context is genuinely exhausted. It then verifies
the handoff in a separate fresh chat and stops waiting.

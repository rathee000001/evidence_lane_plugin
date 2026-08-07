# ChatGPT connection

ChatGPT and Codex are separate host universes. They may run the same Evidence
Lane code and lifecycle laws, but they never share an implicit PV or storage
authority. ChatGPT uses either its own mounted persistent plugin runtime or a
public connector backed by a durable Evidence Lane MCP. It never treats the Git
repository or the Codex store as its live database authority.

The supported public-connector shape is:

```text
ChatGPT -> Vercel preview/adapter -> exact-release durable MCP origin
```

## Installation route distinction

Codex installs the native Evidence Lane plugin from the governed Git
marketplace route. In the user's mounted ChatGPT runtime, the same plugin code
reads and append-only writes that ChatGPT host's own PV, lanes, ENV/UOP state,
Chat Lineage, candidates, receipts, Exit Slips, and accepted pointer in that
host's persistent storage. Those writes remain governed by the same one-writer,
lane, ENV/UOP, pointer, and six-way HIL laws; they do not occur in the Codex
store or in Vercel's function filesystem.

The separate public **New Plugin** or connector route accepts an **MCP Server
URL** or a **Tunnel**, not a Git working tree. Git supplies source and release
identity, while that route still requires a reachable MCP transport. An older
personal app shown in ChatGPT proves only that one plugin runtime, server, or
tunnel was connected; it does not prove that the currently selected Git SHA is
running behind that app.

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
5. tool schemas include the six-control lifecycle and stable internal APIs;
6. no compromised OpenAI key exists in source or deployment configuration;
7. a fresh-chat resume can read the same governed session and pointer.

Use the adapter `/mcp` URL with OAuth. Creation and any UI confirmation remain
manual HIL unless screen control is explicitly authorized in that task.

A successful connection, test call, or fresh-chat resume does not accept a
candidate. Only exact `APPROVE` through Fuse may do so. Fuse prepares a sealed
handoff, but ChatGPT State Travel remains unconsumed until the user explicitly
requests it or the current chat context is genuinely exhausted. It then verifies
the handoff in a separate fresh chat and stops waiting.

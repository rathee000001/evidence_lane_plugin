# ChatGPT connection

ChatGPT connects to a running durable Evidence Lane MCP, never directly to the
Git repository. The supported remote shape is:

```text
ChatGPT -> Vercel preview/adapter -> exact-release durable MCP origin
```

The adapter performs no local evidence writes. Its `/healthz` must report the
exact Git SHA, `durable_origin_verified: true`, and
`local_state_authority: false`. The durable origin must expose `/mcp`, preserve
the complete runtime state, and enforce ChatGPT-compatible OAuth/JWT scopes.

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

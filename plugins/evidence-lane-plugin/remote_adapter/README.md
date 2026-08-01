# ChatGPT remote MCP adapter

This Vercel project is only a thin ChatGPT-facing reverse adapter. It stores no
Evidence Lane state and is not the general router. Every `/mcp` request is
forwarded to one HTTPS durable MCP origin after `/healthz` proves the origin's
`release_sha` equals both `EVIDENCE_LANE_RELEASE_SHA` and, when present,
`VERCEL_GIT_COMMIT_SHA`.

Required preview variables:

- `EVIDENCE_LANE_DURABLE_MCP_ORIGIN`
- `EVIDENCE_LANE_RELEASE_SHA`

The incoming OAuth bearer is passed through to the durable origin, which owns
authentication, one-writer SQLite state, backlog, ChatLineage, queueing,
candidates, receipts, and pointer CAS. A missing origin, non-HTTPS origin, or
SHA mismatch returns `503 BLOCKED`. Vercel local files and memory are never
treated as durable state.

`vercel.json` preserves the incoming public route in the reserved
`__evi_path` query value. The adapter restores `/healthz` or `/mcp` and strips
that internal value before validation or proxying, so the catch-all does not
turn valid endpoints into a root-path 404.
